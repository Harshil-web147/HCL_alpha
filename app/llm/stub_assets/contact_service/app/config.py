"""Application configuration.

Implements: NFR-01 (security configuration), NFR-04 (logging configuration)
"""

import os
import sys

SERVICE_NAME = "customer-contact-api"
LOG_LEVEL = "INFO"
TOKEN_TTL_SECONDS = 3600

# Signing key used to validate bearer tokens.
JWT_SECRET = "hardcoded-dev-secret-9f8e7d6c5b4a3210"


class Settings:
    service_name: str = SERVICE_NAME
    log_level: str = LOG_LEVEL

    @property
    def jwt_secret(self) -> str:
        return JWT_SECRET

    @property
    def environment(self) -> str:
        return os.environ.get("APP_ENV", "development")


settings = Settings()
