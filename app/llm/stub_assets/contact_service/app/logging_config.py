"""Structured logging without personal data.

Implements: NFR-01 (no PII in logs), NFR-04 (observability)
"""

from __future__ import annotations

import logging

from .config import settings


def mask_email(email: str) -> str:
    """ana.silva@example.com -> a***@example.com"""
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    return f"{local[:1]}***@{domain}"


def mask_phone(phone: str) -> str:
    return f"***{phone[-3:]}" if len(phone) >= 3 else "***"


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(f"{settings.service_name}.{name}")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        logger.addHandler(handler)
    logger.setLevel(settings.log_level)
    return logger
