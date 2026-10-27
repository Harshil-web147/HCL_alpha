"""OpenAI-compatible hosted inference without a provider SDK."""

import asyncio
import json
import os
import urllib.error
import urllib.request

from pydantic import BaseModel


def _complete[Output: BaseModel](system: str, context: dict, schema: type[Output]) -> dict:
    key = os.getenv("LLM_API_KEY", "")
    model = os.getenv("LLM_MODEL", "")
    if not key or not model:
        raise ValueError(
            "LLM mode requires LLM_API_KEY and LLM_MODEL. Select demo mode for the reference workflow."
        )
    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": system
                + "\nReturn exactly one JSON object matching this schema:\n"
                + json.dumps(schema.model_json_schema()),
            },
            {"role": "user", "content": json.dumps(context)},
        ],
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"LLM provider returned HTTP {exc.code}; check model and provider configuration."
        ) from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("LLM provider could not be reached within its timeout.") from exc
    try:
        content = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("LLM provider returned no structured assistant response") from exc
    return schema.model_validate_json(content).model_dump()


async def complete[Output: BaseModel](system: str, context: dict, schema: type[Output]) -> dict:
    """Call the configured provider and validate its complete response."""
    return await asyncio.to_thread(_complete, system, context, schema)
