"""Audit service - records every contact modification with before/after state.

Implements: FR-04, AC-05, BR-03
"""

from __future__ import annotations

from typing import Any

from .repository import AuditRepository


class AuditService:
    def __init__(self, repository: AuditRepository):
        self._repository = repository

    def record_update(
        self,
        *,
        customer_id: str,
        actor_id: str,
        before: dict[str, Any],
        after: dict[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        entry = {
            "customer_id": customer_id,
            "actor_id": actor_id,
            "action": "CONTACT_UPDATED",
            "before": _contact_view(before),
            "after": _contact_view(after),
            "request_id": request_id,
        }
        return self._repository.add(entry)

    def history(self, customer_id: str) -> list[dict[str, Any]]:
        return self._repository.list_for_customer(customer_id)


def _contact_view(record: dict[str, Any]) -> dict[str, Any]:
    return {"email": record["email"], "phone": record["phone"], "address": dict(record["address"])}
