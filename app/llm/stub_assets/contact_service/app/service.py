"""Business logic for contact updates.

Implements: FR-01, FR-03, FR-04, AC-01, AC-04, AC-05, AC-06, BR-01
"""

from __future__ import annotations

from typing import Any

from .audit import AuditService
from .errors import ApiError
from .logging_config import get_logger, mask_email, mask_phone
from .repository import CustomerNotFound, CustomerRepository
from .schemas import ContactUpdateRequest

logger = get_logger("service")


class ContactService:
    def __init__(self, customers: CustomerRepository, audit: AuditService):
        self._customers = customers
        self._audit = audit

    def _authorize(self, actor_id: str, customer_id: str) -> None:
        # BR-01: a customer may only modify their own contact information.
        if actor_id != customer_id:
            logger.warning("authorization denied actor=%s target=%s", actor_id, customer_id)
            raise ApiError(403, "FORBIDDEN", "You are not allowed to modify this customer")

    def get_contact(self, *, actor_id: str, customer_id: str) -> dict[str, Any]:
        self._authorize(actor_id, customer_id)
        try:
            return self._customers.get(customer_id)
        except CustomerNotFound as exc:
            raise ApiError(404, "CUSTOMER_NOT_FOUND", f"Customer {customer_id} does not exist") from exc

    def update_contact(
        self, *, actor_id: str, customer_id: str, payload: ContactUpdateRequest, request_id: str
    ) -> dict[str, Any]:
        self._authorize(actor_id, customer_id)
        try:
            before = self._customers.get(customer_id)
        except CustomerNotFound as exc:
            raise ApiError(404, "CUSTOMER_NOT_FOUND", f"Customer {customer_id} does not exist") from exc

        after = self._customers.update_contact(customer_id, payload.model_dump())
        self._audit.record_update(
            customer_id=customer_id, actor_id=actor_id, before=before, after=after, request_id=request_id
        )
        logger.info(
            "contact updated customer=%s email=%s phone=%s request_id=%s",
            customer_id,
            mask_email(after["email"]),
            mask_phone(after["phone"]),
            request_id,
        )
        return after

    def audit_history(self, *, actor_id: str, customer_id: str) -> list[dict[str, Any]]:
        self._authorize(actor_id, customer_id)
        return self._audit.history(customer_id)
