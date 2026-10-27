"""Bearer-token authentication.

Tokens have the form ``<customer_id>.<hmac-sha256 signature>``.  The signature is
verified with a constant-time comparison.  Authorization (may this caller modify
this customer?) lives in the service layer.

Implements: FR-03, AC-03
"""

from __future__ import annotations

import hashlib
import hmac

from fastapi import Request

from .config import settings
from .errors import ApiError


def _sign(customer_id: str) -> str:
    return hmac.new(settings.jwt_secret.encode("utf-8"), customer_id.encode("utf-8"), hashlib.sha256).hexdigest()


def issue_token(customer_id: str) -> str:
    """Issue a signed bearer token for a customer (used by the identity provider / tests)."""
    return f"{customer_id}.{_sign(customer_id)}"


def verify_token(token: str) -> str:
    """Return the customer id encoded in a valid token, or raise 401."""
    if "." not in token:
        raise ApiError(401, "UNAUTHENTICATED", "Malformed bearer token")
    customer_id, signature = token.rsplit(".", 1)
    if not customer_id or not hmac.compare_digest(signature, _sign(customer_id)):
        raise ApiError(401, "UNAUTHENTICATED", "Invalid bearer token")
    return customer_id


def get_current_customer(request: Request) -> str:
    """FastAPI dependency: resolves the authenticated customer id from the Authorization header."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise ApiError(401, "UNAUTHENTICATED", "Missing bearer token")
    return verify_token(header[len("Bearer ") :].strip())
