"""Request / response schemas with mandatory-field validation.

Implements: FR-02, AC-02, BR-02
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")


class Address(BaseModel):
    line1: str = Field(..., min_length=1, max_length=120)
    line2: str = Field("", max_length=120)
    city: str = Field(..., min_length=1, max_length=80)
    postal_code: str = Field(..., min_length=3, max_length=12)
    country: str = Field(..., min_length=2, max_length=2, description="ISO 3166-1 alpha-2")

    @field_validator("country")
    @classmethod
    def _upper_country(cls, value: str) -> str:
        return value.upper()


class ContactUpdateRequest(BaseModel):
    """Full replacement of the customer's contact information (partial updates are not supported)."""

    email: str = Field(..., max_length=254)
    phone: str = Field(..., max_length=16)
    address: Address

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_RE.match(value):
            raise ValueError("email must be a valid address")
        return value

    @field_validator("phone")
    @classmethod
    def _validate_phone(cls, value: str) -> str:
        value = value.strip().replace(" ", "")
        if not PHONE_RE.match(value):
            raise ValueError("phone must contain 7-15 digits with an optional leading +")
        return value


class ContactResponse(BaseModel):
    customer_id: str
    email: str
    phone: str
    address: Address
    updated_at: str


class AuditEntry(BaseModel):
    audit_id: str
    customer_id: str
    actor_id: str
    action: str
    before: dict
    after: dict
    request_id: str
    recorded_at: str
