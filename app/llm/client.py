"""Provider-agnostic LLM client with schema-enforced JSON output.

Every agent calls ``LLMClient.generate(agent, system, user, Schema)`` and gets a
validated Pydantic object back.  The provider is chosen by ``LLM_PROVIDER``:

* ``anthropic`` - Claude via the Messages API, using forced tool-use so the
  model must emit JSON matching the schema.
* ``openai``    - any OpenAI-compatible endpoint (``OPENAI_BASE_URL`` lets you
  point at Azure OpenAI, vLLM, Ollama, LiteLLM ...) in JSON mode.
* ``stub``      - a deterministic rule-based stand-in used for the platform's
  own tests and offline demos.  No network, no keys.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings

T = TypeVar("T", bound=BaseModel)
log = logging.getLogger("sdlc.copilot")


class LLMError(RuntimeError):
    pass


class LLMFatalError(LLMError):
    """Retrying would not help (and would cost money): no usable key, budget spent, truncated output."""


# Agents whose output is code or tests - they get the higher reasoning effort.
CODE_AGENTS = {"developer", "test_engineer", "defect_resolver"}


class LLMProvider(Protocol):
    name: str

    def complete_json(self, *, agent: str, system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]: ...


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Best-effort extraction of a JSON object from model text."""
    text = text.strip()
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise LLMError("Model response did not contain a JSON object")


def schema_instructions(schema: type[BaseModel]) -> str:
    return (
        "Respond with ONLY a single JSON object (no prose, no markdown fences) that validates "
        "against this JSON schema:\n" + json.dumps(schema.model_json_schema(), indent=None)
    )


# --------------------------------------------------------------------------- #
# providers
# --------------------------------------------------------------------------- #
class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings: Settings):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise LLMError("pip install anthropic to use LLM_PROVIDER=anthropic") from exc
        self._client = anthropic.Anthropic()
        self._settings = settings

    def complete_json(self, *, agent: str, system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]:
        tool_name = f"emit_{schema.__name__}"
        response = self._client.messages.create(
            model=self._settings.anthropic_model,
            max_tokens=self._settings.llm_max_tokens,
            temperature=self._settings.llm_temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[
                {
                    "name": tool_name,
                    "description": f"Emit the {schema.__name__} artifact produced by the {agent}.",
                    "input_schema": schema.model_json_schema(),
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
        )
        for block in response.content:
            if getattr(block, "type", "") == "tool_use":
                return dict(block.input)
        text = "".join(getattr(b, "text", "") for b in response.content)
        return extract_json(text)


def parse_key_pool(spec: str, fallback: str = "") -> list[tuple[str, str]]:
    """``"main:sk-a, angel:sk-b"`` -> [("main", "sk-a"), ("angel", "sk-b")]; unlabeled keys get key-N."""
    pool = []
    for i, item in enumerate((p.strip() for p in spec.split(",")), start=1):
        if not item:
            continue
        label, _, key = item.rpartition(":")
        pool.append((label.strip() or f"key-{i}", key.strip()))
    if not pool and fallback.strip():
        pool.append(("default", fallback.strip()))
    return pool


class OpenAIProvider:
    """OpenAI chat completions in JSON mode over a pool of API keys.

    The current key is used until it fails with a rate-limit, quota or auth error; it is then
    benched (briefly for rate limits, for the rest of the process otherwise) and the call is
    retried on the next key, so one exhausted key never stops a workflow."""

    name = "openai"
    rate_limit_cooldown_seconds = 60.0

    def __init__(self, settings: Settings):
        try:
            import openai
        except ImportError as exc:  # pragma: no cover
            raise LLMError("pip install openai to use LLM_PROVIDER=openai") from exc
        pool = parse_key_pool(settings.openai_api_keys, os.environ.get("OPENAI_API_KEY", ""))
        if not pool:
            raise LLMError("LLM_PROVIDER=openai needs OPENAI_API_KEYS (or OPENAI_API_KEY) in .env")
        self._openai = openai
        self._clients = [(label, openai.OpenAI(api_key=key, base_url=settings.openai_base_url, max_retries=1)) for label, key in pool]
        self._benched_until: dict[str, float] = {}
        self._why: dict[str, str] = {}
        self._current = 0
        self._guard = threading.Lock()
        self._unsupported: set[str] = set()
        self._local = threading.local()
        self._settings = settings

    def _pick(self) -> tuple[int, str, Any]:
        with self._guard:
            now = time.monotonic()
            for step in range(len(self._clients)):
                i = (self._current + step) % len(self._clients)
                label, client = self._clients[i]
                if self._benched_until.get(label, 0.0) <= now:
                    self._current = i
                    return i, label, client
            waits = {label: round(until - now) for label, until in self._benched_until.items() if until != float("inf")}
            reasons = ", ".join(f"{label}: {self._why.get(label, '?')}" for label, _ in self._clients)
        if waits:
            raise LLMFatalError(f"every OpenAI key in the pool is busy ({reasons}); retry in {min(waits.values())}s")
        raise LLMFatalError(f"no usable OpenAI key in the pool ({reasons}) - add credits at platform.openai.com/settings/organization/billing or add another key to OPENAI_API_KEYS")

    def _bench(self, index: int, label: str, seconds: float, why: str) -> None:
        log.warning("OpenAI key '%s' %s - benched %s, switching to the next key in the pool", label, why, "for this session" if seconds == float("inf") else f"for {seconds:.0f}s")
        with self._guard:
            self._benched_until[label] = time.monotonic() + seconds
            self._why[label] = why
            if self._current == index:
                self._current = (index + 1) % len(self._clients)

    def _params(self, agent: str, system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]:
        s = self._settings
        params: dict[str, Any] = {
            "model": s.openai_model,
            # static instructions + schema first and the per-feature content last, so OpenAI's
            # automatic prompt caching bills the repeated prefix at the cached-input rate
            "messages": [
                {"role": "system", "content": system + "\n\n" + schema_instructions(schema)},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "max_completion_tokens": s.llm_max_tokens,
            "reasoning_effort": s.llm_effort_code if agent in CODE_AGENTS else s.llm_effort_default,
            "temperature": s.llm_temperature,
            "prompt_cache_key": f"sdlc-{agent}",
        }
        for name in self._unsupported:
            value = params.pop(name, None)
            if name == "max_completion_tokens" and value is not None:
                params["max_tokens"] = value
        return params

    def _drop_unsupported(self, exc: Exception, params: dict[str, Any]) -> bool:
        """Remember a parameter the model rejected so it is never sent again; True if one was dropped."""
        param = getattr(exc, "param", None) or ""
        message = str(exc)
        for name in ("temperature", "reasoning_effort", "prompt_cache_key", "max_completion_tokens"):
            if name in params and (param == name or f"'{name}'" in message or f"`{name}`" in message):
                log.info("model %s does not accept '%s' - dropping it", self._settings.openai_model, name)
                self._unsupported.add(name)
                return True
        return False

    def last_usage(self) -> dict[str, int] | None:
        return getattr(self._local, "usage", None)

    def complete_json(self, *, agent: str, system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]:
        oa = self._openai
        self._local.usage = None
        while True:
            index, label, client = self._pick()
            params = self._params(agent, system, user, schema)
            try:
                response = client.chat.completions.create(**params)
            except oa.BadRequestError as exc:
                if self._drop_unsupported(exc, params):
                    continue
                raise LLMFatalError(f"OpenAI rejected the request: {exc}") from exc
            except oa.RateLimitError as exc:
                out_of_quota = "insufficient_quota" in (exc.type, exc.code) or exc.code == "credit_balance_exhausted"
                if out_of_quota:
                    self._bench(index, label, float("inf"), "out of credits/quota")
                else:
                    self._bench(index, label, self.rate_limit_cooldown_seconds, "rate-limited")
                continue
            except oa.AuthenticationError:
                self._bench(index, label, float("inf"), "invalid key")
                continue
            except oa.PermissionDeniedError:
                self._bench(index, label, float("inf"), f"no access to model {self._settings.openai_model}")
                continue
            self._local.usage = _usage(response)
            choice = response.choices[0]
            if choice.finish_reason == "length":
                raise LLMFatalError(
                    f"{agent}: output hit the {self._settings.llm_max_tokens}-token limit before the JSON was complete - "
                    "raise LLM_MAX_TOKENS or lower LLM_EFFORT_CODE (retrying would only pay for the same truncation)"
                )
            return extract_json(choice.message.content or "")


def _usage(response: Any) -> dict[str, int]:
    u = getattr(response, "usage", None)
    if u is None:
        return {}
    prompt_details = getattr(u, "prompt_tokens_details", None)
    completion_details = getattr(u, "completion_tokens_details", None)
    return {
        "input_tokens": u.prompt_tokens or 0,
        "cached_input_tokens": getattr(prompt_details, "cached_tokens", 0) or 0,
        "output_tokens": u.completion_tokens or 0,
        "reasoning_tokens": getattr(completion_details, "reasoning_tokens", 0) or 0,
    }


def build_provider(settings: Settings) -> LLMProvider:
    provider = settings.llm_provider.lower()
    if provider == "anthropic":
        return AnthropicProvider(settings)
    if provider == "openai":
        return OpenAIProvider(settings)
    if provider == "stub":
        from app.llm.stub import StubProvider

        return StubProvider()
    raise LLMError(f"Unknown LLM_PROVIDER '{settings.llm_provider}' (expected anthropic | openai | stub)")


# --------------------------------------------------------------------------- #
# client
# --------------------------------------------------------------------------- #
CallObserver = Callable[[dict[str, Any]], None]


class LLMClient:
    """Validates provider output against the requested schema and retries once with the
    validation errors fed back to the model (self-repair)."""

    def __init__(
        self,
        settings: Settings | None = None,
        provider: LLMProvider | None = None,
        observer: CallObserver | None = None,
        tokens_used: int = 0,
    ):
        self.settings = settings or get_settings()
        self.provider = provider or build_provider(self.settings)
        self.observer = observer
        self.tokens_used = tokens_used  # running input+output total for the feature (budget)

    def _check_budget(self, agent: str) -> None:
        budget = self.settings.llm_token_budget_per_feature
        if budget and self.tokens_used >= budget:
            raise LLMFatalError(
                f"{agent}: token budget for this feature is spent ({self.tokens_used:,} of {budget:,}); "
                "raise LLM_TOKEN_BUDGET_PER_FEATURE to continue"
            )

    def generate(self, agent: str, system: str, user: str, schema: type[T], max_attempts: int = 2) -> T:
        last_error: Exception | None = None
        prompt = user
        for attempt in range(1, max_attempts + 1):
            self._check_budget(agent)
            started = time.perf_counter()
            ok = False
            error_text = ""
            try:
                raw = self.provider.complete_json(agent=agent, system=system, user=prompt, schema=schema)
                artifact = schema.model_validate(raw)
                ok = True
                return artifact
            except LLMFatalError as exc:
                error_text = str(exc)[:2000]
                raise
            except (ValidationError, LLMError, json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                error_text = str(exc)[:2000]
                prompt = (
                    user
                    + "\n\nYour previous response failed validation with the following errors. "
                    "Fix them and respond again with ONLY the JSON object:\n"
                    + error_text
                )
            finally:
                last_usage = getattr(self.provider, "last_usage", None)
                usage = (last_usage() if last_usage else None) or {}
                self.tokens_used += usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                if self.observer:
                    self.observer(
                        {
                            "agent": agent,
                            "provider": self.provider.name,
                            "schema": schema.__name__,
                            "attempt": attempt,
                            "ok": ok,
                            "duration_seconds": round(time.perf_counter() - started, 3),
                            "error": error_text,
                            "usage": usage,
                        }
                    )
        raise LLMError(f"{agent}: could not produce a valid {schema.__name__} after {max_attempts} attempts: {last_error}")
