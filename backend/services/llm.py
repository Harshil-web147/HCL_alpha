"""Shared LLM wrapper. Every agent calls complete_json().

Configure in .env:
  LLM_PROVIDER = openai | anthropic
  LLM_MODEL    = model name
  OPENAI_API_KEY / ANTHROPIC_API_KEY
  OPENAI_BASE_URL (optional) for OpenAI-compatible servers
  (Gemini, Groq, Ollama, Azure proxy, etc.)
"""

import os

from dotenv import load_dotenv

load_dotenv()


def complete_json(system: str, user: str, temperature: float = 0.2) -> str:
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    model = os.getenv("LLM_MODEL", "")

    if provider == "anthropic":
        import anthropic

        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=model or "claude-sonnet-5-5",
            max_tokens=8000,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in resp.content if b.type == "text")

    from openai import OpenAI

    client = OpenAI(base_url=os.getenv("OPENAI_BASE_URL") or None)
    resp = client.chat.completions.create(
        model=model or "gpt-4o-mini",
        temperature=temperature,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content or ""
