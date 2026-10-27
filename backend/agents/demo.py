"""Transparent reference fixture; generated programs still face real tools."""

ANALYSIS = {
    "requirements": [
        {"id": "REQ-01", "text": "A customer can update their phone number."},
        {"id": "REQ-02", "text": "Reject invalid phones and unknown customers."},
        {"id": "REQ-03", "text": "Only the owning customer may update the contact record."},
    ],
    "acceptance_criteria": [
        {
            "id": "AC-01",
            "requirement_id": "REQ-01",
            "text": "A valid international phone number returns HTTP 200 and the updated record.",
        },
        {
            "id": "AC-02",
            "requirement_id": "REQ-02",
            "text": "An invalid phone number returns HTTP 422.",
        },
        {
            "id": "AC-03",
            "requirement_id": "REQ-02",
            "text": "An unknown customer returns HTTP 404.",
        },
        {
            "id": "AC-04",
            "requirement_id": "REQ-03",
            "text": "A different X-User-Id returns HTTP 403; a missing identity returns HTTP 401.",
        },
    ],
    "assumptions": [
        "DEMO MODE: this is a deterministic contact-update reference project, regardless of the submitted feature. Use LLM mode for arbitrary requirements.",
        "X-User-Id simulates an authenticated principal for the hackathon only; a production identity provider is outside this reference scope.",
        "Contacts and audit events are held in memory and reset on restart. No durability or production-readiness claim is made.",
        "A deliberate phone-validation defect in version 1 demonstrates detection, approved remediation and real revalidation.",
    ],
    "questions": [],
}

DESIGN = {
    "summary": "Small FastAPI contact API with Pydantic input, ownership checks and in-memory audit events.",
    "endpoints": [
        {
            "method": "PATCH",
            "path": "/customers/{customer_id}/contact",
            "description": "Validate phone input and simulated identity, update the contact, and append an audit event.",
        }
    ],
    "models": [
        "ContactUpdate(phone: international E.164 string)",
        "Contact(customer_id: int, phone: str)",
        "AuditEvent(customer_id: int, actor: int, action: str)",
    ],
    "file_structure": [
        "app/__init__.py",
        "app/main.py",
        "app/routes/__init__.py",
        "app/routes/contact.py",
        "tests/test_contact.py",
    ],
    "security": [
        "Reject absent simulated identity with 401 and non-owner with 403.",
        "Use typed request validation. Do not log phone numbers.",
        "Production deployment requires a verified identity provider, durable storage and operational review.",
    ],
}

CONTACT = '''"""Contact update routes for the hackathon reference API."""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()
contacts: dict[int, str] = {1: "+14155550100"}
audit_events: list[dict[str, int | str]] = []


class ContactUpdate(BaseModel):
    phone: str = Field(PHONE_VALIDATION)


@router.patch("/customers/{customer_id}/contact")
def update_contact(
    customer_id: int,
    body: ContactUpdate,
    x_user_id: int | None = Header(default=None),
) -> dict[str, int | str]:
    if x_user_id is None:
        raise HTTPException(status_code=401, detail="Identity required")
    if customer_id not in contacts:
        raise HTTPException(status_code=404, detail="Customer not found")
    if x_user_id != customer_id:
        raise HTTPException(status_code=403, detail="Cannot modify another customer")
    contacts[customer_id] = body.phone
    audit_events.append({"customer_id": customer_id, "actor": x_user_id, "action": "contact_updated"})
    return {"customer_id": customer_id, "phone": body.phone}
'''

MAIN = '''"""Generated contact service."""

from fastapi import FastAPI

from app.routes.contact import router

app = FastAPI(title="Customer Contact API")
app.include_router(router)
'''

TESTS = '''"""Acceptance-criterion checks for the contact update API."""

from fastapi.testclient import TestClient

from app.main import app
from app.routes.contact import audit_events

client = TestClient(app)


def test_valid_phone_update() -> None:
    """AC-01: valid phone updates contact and records an audit event."""
    response = client.patch("/customers/1/contact", json={"phone": "+14155550123"}, headers={"X-User-Id": "1"})
    assert response.status_code == 200
    assert response.json()["phone"] == "+14155550123"
    assert audit_events[-1]["action"] == "contact_updated"


def test_invalid_phone_rejected() -> None:
    """AC-02: invalid and empty phone strings are rejected."""
    for phone in ["bad-phone", "", "123", "+0123456789", "+1234567890123456"]:
        response = client.patch("/customers/1/contact", json={"phone": phone}, headers={"X-User-Id": "1"})
        assert response.status_code == 422


def test_unknown_customer() -> None:
    """AC-03: unknown records return 404."""
    response = client.patch("/customers/999/contact", json={"phone": "+14155550123"}, headers={"X-User-Id": "999"})
    assert response.status_code == 404


def test_unauthorized_update() -> None:
    """AC-04: reject a missing or non-owning simulated identity."""
    response = client.patch("/customers/1/contact", json={"phone": "+14155550123"}, headers={"X-User-Id": "2"})
    assert response.status_code == 403
    response = client.patch("/customers/1/contact", json={"phone": "+14155550123"})
    assert response.status_code == 401
'''

MAPPING = [
    {
        "requirement_id": req,
        "ac_id": ac,
        "implementation": "app/routes/contact.py → update_contact()",
        "test": "tests/test_contact.py::" + test,
    }
    for req, ac, test in [
        ("REQ-01", "AC-01", "test_valid_phone_update"),
        ("REQ-02", "AC-02", "test_invalid_phone_rejected"),
        ("REQ-02", "AC-03", "test_unknown_customer"),
        ("REQ-03", "AC-04", "test_unauthorized_update"),
    ]
]


def application_files(fixed: bool = False) -> list[dict[str, str]]:
    """Return fixture application files, with an explicit optional correction."""
    validation = 'pattern=r"^\\+[1-9][0-9]{6,14}$"' if fixed else "min_length=1"
    return [
        {"path": "app/__init__.py", "content": ""},
        {"path": "app/routes/__init__.py", "content": ""},
        {"path": "app/main.py", "content": MAIN},
        {
            "path": "app/routes/contact.py",
            "content": CONTACT.replace("PHONE_VALIDATION", validation),
        },
        {"path": "requirements.txt", "content": "fastapi\nhttpx\npydantic\n"},
        {
            "path": "README.md",
            "content": "# Customer Contact API\n\nHackathon reference only: simulated X-User-Id identity and volatile in-memory records. Run `uvicorn app.main:app`. Real identity verification and durable storage are required before production use.\n",
        },
    ]
