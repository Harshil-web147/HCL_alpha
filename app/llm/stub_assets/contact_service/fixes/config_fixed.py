"""Application configuration.

Secrets are never stored in source: the token signing key is injected through the
``JWT_SECRET`` environment variable and read lazily so the module can be imported
(for builds, docs generation, tests) before the deployment environment is wired up.

Implements: NFR-01 (security configuration), NFR-04 (logging configuration)
"""

import os

SERVICE_NAME = "customer-contact-api"
LOG_LEVEL = "INFO"
TOKEN_TTL_SECONDS = 3600


class MissingConfiguration(RuntimeError):
    pass


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingConfiguration(f"Required environment variable {name} is not set")
    return value


class Settings:
    service_name: str = SERVICE_NAME
    log_level: str = LOG_LEVEL

    @property
    def jwt_secret(self) -> str:
        return _require_env("JWT_SECRET")

    @property
    def environment(self) -> str:
        return os.environ.get("APP_ENV", "development")


settings = Settings()
