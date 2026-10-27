"""Data-access layer.

In-memory repositories with the same interface a SQL implementation would expose,
so the service layer never touches storage details.

Implements: FR-01, FR-04, AS-04
"""

from __future__ import annotations

import copy
import threading
import uuid
from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class CustomerNotFound(LookupError):
    pass


class CustomerRepository:
    """Stores the contact record per customer id."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._customers: dict[str, dict[str, Any]] = {}
        self.seed()

    def seed(self) -> None:
        with self._lock:
            self._customers = {
                "cust-1001": {
                    "customer_id": "cust-1001",
                    "email": "ana.silva@example.com",
                    "phone": "+14155550101",
                    "address": {
                        "line1": "1 Market St",
                        "line2": "",
                        "city": "San Francisco",
                        "postal_code": "94105",
                        "country": "US",
                    },
                    "updated_at": _now(),
                },
                "cust-1002": {
                    "customer_id": "cust-1002",
                    "email": "ravi.k@example.com",
                    "phone": "+919876543210",
                    "address": {
                        "line1": "12 MG Road",
                        "line2": "",
                        "city": "Bengaluru",
                        "postal_code": "560001",
                        "country": "IN",
                    },
                    "updated_at": _now(),
                },
            }

    def reset(self) -> None:
        self.seed()

    def get(self, customer_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._customers.get(customer_id)
            if record is None:
                raise CustomerNotFound(customer_id)
            return copy.deepcopy(record)

    def update_contact(self, customer_id: str, contact: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            record = self._customers.get(customer_id)
            if record is None:
                raise CustomerNotFound(customer_id)
            record.update(
                {
                    "email": contact["email"],
                    "phone": contact["phone"],
                    "address": dict(contact["address"]),
                    "updated_at": _now(),
                }
            )
            return copy.deepcopy(record)


class AuditRepository:
    """Append-only audit store (BR-03)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[dict[str, Any]] = []

    def reset(self) -> None:
        with self._lock:
            self._entries = []

    def add(self, entry: dict[str, Any]) -> dict[str, Any]:
        stored = dict(entry)
        stored["audit_id"] = str(uuid.uuid4())
        stored["recorded_at"] = _now()
        with self._lock:
            self._entries.append(stored)
        return copy.deepcopy(stored)

    def list_for_customer(self, customer_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [copy.deepcopy(e) for e in self._entries if e["customer_id"] == customer_id]


customers = CustomerRepository()
audits = AuditRepository()
