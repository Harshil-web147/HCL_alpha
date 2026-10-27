"""Runtime settings for the SDLC Copilot platform.

Everything is driven by environment variables so the same image runs in CI,
Docker and on a laptop.  ``LLM_PROVIDER=stub`` runs the full pipeline with a
deterministic, rule-based stand-in for the LLM (used by the platform's own
test-suite and for offline demos).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Read KEY=VALUE lines from .env into the environment; variables already set win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1]:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        os.environ.setdefault(key.strip(), value)


load_dotenv()


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _price(name: str) -> float | None:
    value = os.environ.get(name, "").strip()
    return float(value) if value else None


@dataclass
class Settings:
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "stub"))
    anthropic_model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
    openai_model: str = field(default_factory=lambda: _env("OPENAI_MODEL", "gpt-4o"))
    openai_base_url: str | None = field(default_factory=lambda: os.environ.get("OPENAI_BASE_URL"))
    # Key pool: "label:sk-...,label:sk-..." (labels optional); falls back to OPENAI_API_KEY.
    openai_api_keys: str = field(default_factory=lambda: _env("OPENAI_API_KEYS", ""))
    llm_max_tokens: int = field(default_factory=lambda: int(_env("LLM_MAX_TOKENS", "8000")))
    llm_temperature: float = field(default_factory=lambda: float(_env("LLM_TEMPERATURE", "0.1")))
    # Cost control: reasoning effort for code-producing agents vs. everything else, a per-feature
    # token budget (0 = unlimited) and optional prices for the dashboard's cost estimate.
    llm_effort_code: str = field(default_factory=lambda: _env("LLM_EFFORT_CODE", "high"))
    llm_effort_default: str = field(default_factory=lambda: _env("LLM_EFFORT_DEFAULT", "low"))
    llm_token_budget_per_feature: int = field(default_factory=lambda: int(_env("LLM_TOKEN_BUDGET_PER_FEATURE", "0") or 0))
    llm_price_input_per_1m: float | None = field(default_factory=lambda: _price("LLM_PRICE_INPUT_PER_1M"))
    llm_price_cached_input_per_1m: float | None = field(default_factory=lambda: _price("LLM_PRICE_CACHED_INPUT_PER_1M"))
    llm_price_output_per_1m: float | None = field(default_factory=lambda: _price("LLM_PRICE_OUTPUT_PER_1M"))

    data_dir: Path = field(default_factory=lambda: Path(_env("SDLC_DATA_DIR", str(PROJECT_ROOT / "data"))))
    workspace_root: Path = field(
        default_factory=lambda: Path(_env("SDLC_WORKSPACE_ROOT", str(PROJECT_ROOT / "workspaces")))
    )
    gates_config: Path = field(
        default_factory=lambda: Path(_env("SDLC_GATES_CONFIG", str(PROJECT_ROOT / "config" / "gates.yaml")))
    )
    tool_timeout_seconds: int = field(default_factory=lambda: int(_env("SDLC_TOOL_TIMEOUT", "180")))
    enable_pip_audit: bool = field(default_factory=lambda: _env("SDLC_ENABLE_PIP_AUDIT", "false").lower() == "true")
    otel_enabled: bool = field(default_factory=lambda: bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")))
    # Start the generated app automatically when the release is approved, on this interface.
    auto_launch_product: bool = field(default_factory=lambda: _env("SDLC_AUTO_LAUNCH_PRODUCT", "true").lower() == "true")
    product_host: str = field(default_factory=lambda: _env("SDLC_PRODUCT_HOST", "127.0.0.1"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "sdlc.db"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_root.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.ensure_dirs()
    return _settings


def reset_settings() -> None:
    """Used by tests to re-read environment variables."""
    global _settings
    _settings = None
