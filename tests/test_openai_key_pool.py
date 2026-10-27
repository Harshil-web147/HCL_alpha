"""OpenAI provider: key-pool failover and cost controls (no network: clients are faked)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import openai
import pytest

from app.config import Settings
from app.llm.client import LLMClient, LLMError, LLMFatalError, OpenAIProvider, parse_key_pool
from app.models import BusinessRule

OK = '{"id": "BR-01", "description": "ok"}'


def _error(cls, status: int, type_: str, code: str, param: str | None = None, message: str = "boom"):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    return cls(message, response=response, body={"type": type_, "code": code, "param": param, "message": message})


def _response(content: str, finish_reason: str = "stop"):
    usage = SimpleNamespace(
        prompt_tokens=1200,
        completion_tokens=300,
        prompt_tokens_details=SimpleNamespace(cached_tokens=1024),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=200),
    )
    choice = SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


class FakeClient:
    """Plays back one outcome per call (the last one repeats)."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests: list[dict] = []
        self.chat = self
        self.completions = self

    @property
    def calls(self) -> int:
        return len(self.requests)

    def create(self, **params):
        self.requests.append(params)
        outcome = self.outcomes[min(len(self.requests), len(self.outcomes)) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome if not isinstance(outcome, str) else _response(outcome)


def _provider(*fakes: FakeClient, **settings) -> OpenAIProvider:
    keys = ",".join(f"k{i}:sk-{i}" for i in range(len(fakes)))
    provider = OpenAIProvider(Settings(llm_provider="openai", openai_api_keys=keys, openai_model="gpt-6-luna", **settings))
    provider._clients = [(label, fake) for (label, _), fake in zip(provider._clients, fakes, strict=True)]
    return provider


def _call(provider: OpenAIProvider, agent: str = "t") -> dict:
    return provider.complete_json(agent=agent, system="s", user="u", schema=BusinessRule)


# ------------------------------------------------------------------ key pool
def test_parse_key_pool_labels_and_fallback():
    assert parse_key_pool("main:sk-a, sk-b ,") == [("main", "sk-a"), ("key-2", "sk-b")]
    assert parse_key_pool("", fallback="sk-x") == [("default", "sk-x")]
    assert parse_key_pool("", fallback="") == []


def test_fails_over_and_sticks_to_the_working_key():
    dead = FakeClient(_error(openai.RateLimitError, 429, "insufficient_quota", "credit_balance_exhausted"))
    good = FakeClient(OK)
    provider = _provider(dead, good)
    assert _call(provider)["id"] == "BR-01"
    assert _call(provider)["id"] == "BR-01"
    assert dead.calls == 1 and good.calls == 2  # the exhausted key is not retried


def test_invalid_key_is_skipped():
    bad = FakeClient(_error(openai.AuthenticationError, 401, "invalid_request_error", "invalid_api_key"))
    provider = _provider(bad, FakeClient(OK))
    assert _call(provider)["id"] == "BR-01" and bad.calls == 1


def test_clear_error_when_every_key_is_exhausted():
    exhausted = _error(openai.RateLimitError, 429, "insufficient_quota", "credit_balance_exhausted")
    provider = _provider(FakeClient(exhausted), FakeClient(exhausted))
    with pytest.raises(LLMFatalError, match="no usable OpenAI key.*k0: out of credits.*k1: out of credits"):
        _call(provider)


def test_rate_limited_keys_report_a_retry_time():
    provider = _provider(FakeClient(_error(openai.RateLimitError, 429, "requests", "rate_limit_exceeded")))
    with pytest.raises(LLMError, match="busy .*rate-limited.*retry in"):
        _call(provider)


# -------------------------------------------------------------- cost controls
def test_code_agents_get_higher_reasoning_effort_and_a_cache_key():
    fake = FakeClient(OK)
    provider = _provider(fake, llm_effort_code="high", llm_effort_default="low")
    _call(provider, agent="developer")
    _call(provider, agent="business_analyst")
    dev, ba = fake.requests
    assert dev["model"] == "gpt-6-luna" and dev["reasoning_effort"] == "high" and ba["reasoning_effort"] == "low"
    assert dev["prompt_cache_key"] == "sdlc-developer" and "max_completion_tokens" in dev
    assert dev["messages"][0]["role"] == "system"  # static prefix first -> cacheable


def test_rejected_parameters_are_dropped_once_and_remembered():
    no_temp = _error(openai.BadRequestError, 400, "invalid_request_error", "unsupported_parameter", param="temperature",
                     message="Unsupported parameter: 'temperature' is not supported with this model.")
    fake = FakeClient(no_temp, OK)
    provider = _provider(fake)
    assert _call(provider)["id"] == "BR-01"
    _call(provider)
    assert "temperature" in fake.requests[0] and all("temperature" not in r for r in fake.requests[1:])
    assert fake.calls == 3  # one rejected request, then never sent again


def test_other_bad_requests_fail_without_paid_retries():
    fake = FakeClient(_error(openai.BadRequestError, 400, "invalid_request_error", "context_length_exceeded", message="too long"))
    with pytest.raises(LLMFatalError, match="rejected the request"):
        _call(_provider(fake))
    assert fake.calls == 1


def test_truncated_output_is_not_retried():
    fake = FakeClient(_response('{"id": "BR', finish_reason="length"))
    client = LLMClient(Settings(llm_provider="openai"), provider=_provider(fake))
    with pytest.raises(LLMFatalError, match="token limit"):
        client.generate("developer", "s", "u", BusinessRule)
    assert fake.calls == 1


def test_usage_is_recorded_and_the_budget_stops_further_calls():
    calls: list[dict] = []
    fake = FakeClient(OK)
    client = LLMClient(Settings(llm_provider="openai", llm_token_budget_per_feature=2000), provider=_provider(fake), observer=calls.append)
    client.generate("business_analyst", "s", "u", BusinessRule)
    assert calls[0]["usage"] == {"input_tokens": 1200, "cached_input_tokens": 1024, "output_tokens": 300, "reasoning_tokens": 200}
    assert client.tokens_used == 1500
    client.generate("business_analyst", "s", "u", BusinessRule)  # 1500 < 2000: allowed
    with pytest.raises(LLMFatalError, match="token budget"):
        client.generate("business_analyst", "s", "u", BusinessRule)
    assert fake.calls == 2
