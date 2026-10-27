"""Customer Contact API - FastAPI application.

Implements: FR-01, FR-05, AC-01, AC-06
Component flow: UI -> Customer API -> Authentication -> Profile Service -> Repository -> Audit Service
"""

from __future__ import annotations

import uuid

from fastapi import Depends, FastAPI, Request

from .audit import AuditService
from .auth import get_current_customer
from .config import settings
from .errors import register_error_handlers
from .repository import audits, customers
from .schemas import AuditEntry, ContactResponse, ContactUpdateRequest
from .service import ContactService


def create_app() -> FastAPI:
    app = FastAPI(title="Customer Contact API", version="1.0.0")
    register_error_handlers(app)
    service = ContactService(customers, AuditService(audits))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.service_name}

    @app.get("/customers/{customer_id}/contact", response_model=ContactResponse)
    def get_contact(customer_id: str, actor_id: str = Depends(get_current_customer)) -> ContactResponse:
        return ContactResponse(**service.get_contact(actor_id=actor_id, customer_id=customer_id))

    @app.put("/customers/{customer_id}/contact", response_model=ContactResponse)
    def update_contact(
        customer_id: str,
        payload: ContactUpdateRequest,
        request: Request,
        actor_id: str = Depends(get_current_customer),
    ) -> ContactResponse:
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        record = service.update_contact(
            actor_id=actor_id, customer_id=customer_id, payload=payload, request_id=request_id
        )
        return ContactResponse(**record)

    @app.get("/customers/{customer_id}/contact/audit", response_model=list[AuditEntry])
    def audit_history(customer_id: str, actor_id: str = Depends(get_current_customer)) -> list[AuditEntry]:
        return [AuditEntry(**e) for e in service.audit_history(actor_id=actor_id, customer_id=customer_id)]

    return app


app = create_app()
