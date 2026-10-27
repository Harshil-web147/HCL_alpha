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
import re
import time
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


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


class OpenAIProvider:
    name = "openai"

    def __init__(self, settings: Settings):
        try:
            import openai
        except ImportError as exc:  # pragma: no cover
            raise LLMError("pip install openai to use LLM_PROVIDER=openai") from exc
        self._client = openai.OpenAI(base_url=settings.openai_base_url) if settings.openai_base_url else openai.OpenAI()
        self._settings = settings

    def complete_json(self, *, agent: str, system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]:
        response = self._client.chat.completions.create(
            model=self._settings.openai_model,
            temperature=self._settings.llm_temperature,
            max_tokens=self._settings.llm_max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system + "\n\n" + schema_instructions(schema)},
                {"role": "user", "content": user},
            ],
        )
        return extract_json(response.choices[0].message.content or "")


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

    def __init__(self, settings: Settings | None = None, provider: LLMProvider | None = None, observer: CallObserver | None = None):
        self.settings = settings or get_settings()
        self.provider = provider or build_provider(self.settings)
        self.observer = observer

    def generate(self, agent: str, system: str, user: str, schema: type[T], max_attempts: int = 2) -> T:
        last_error: Exception | None = None
        prompt = user
        for attempt in range(1, max_attempts + 1):
            started = time.perf_counter()
            ok = False
            error_text = ""
            try:
                raw = self.provider.complete_json(agent=agent, system=system, user=prompt, schema=schema)
                artifact = schema.model_validate(raw)
                ok = True
                return artifact
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
                        }
                    )
        raise LLMError(f"{agent}: could not produce a valid {schema.__name__} after {max_attempts} attempts: {last_error}")
