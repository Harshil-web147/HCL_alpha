"""API tests for the customer contact update feature.

Each test declares the acceptance criteria it verifies with an ``AC:`` marker so
the SDLC Copilot can build the requirement -> test traceability matrix.
"""

from tests.conftest import OTHER, OWNER

URL = f"/customers/{OWNER}/contact"


# ---------------------------------------------------------------- positive
def test_update_contact_success_returns_updated_contact(client, owner_headers, valid_payload):
    """AC: AC-01"""
    response = client.put(URL, json=valid_payload, headers=owner_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["customer_id"] == OWNER
    assert body["email"] == "ana.new@example.com"  # normalised to lower case
    assert body["phone"] == "+14155550199"  # whitespace stripped
    assert body["address"]["country"] == "US"


def test_update_contact_persists_and_is_readable(client, owner_headers, valid_payload):
    """AC: AC-01"""
    client.put(URL, json=valid_payload, headers=owner_headers)
    response = client.get(URL, headers=owner_headers)
    assert response.status_code == 200
    assert response.json()["email"] == "ana.new@example.com"


# ---------------------------------------------------------------- validation
def test_missing_mandatory_email_returns_422(client, owner_headers, valid_payload):
    """AC: AC-02, AC-06"""
    del valid_payload["email"]
    response = client.put(URL, json=valid_payload, headers=owner_headers)
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert any(d["field"] == "email" for d in body["error"]["details"])


def test_invalid_email_format_returns_422(client, owner_headers, valid_payload):
    """AC: AC-02"""
    valid_payload["email"] = "not-an-email"
    response = client.put(URL, json=valid_payload, headers=owner_headers)
    assert response.status_code == 422
    assert any(d["field"] == "email" for d in response.json()["error"]["details"])


def test_missing_address_city_returns_422(client, owner_headers, valid_payload):
    """AC: AC-02"""
    del valid_payload["address"]["city"]
    response = client.put(URL, json=valid_payload, headers=owner_headers)
    assert response.status_code == 422
    assert any(d["field"] == "address.city" for d in response.json()["error"]["details"])


# ---------------------------------------------------------------- boundary
def test_phone_boundary_min_and_max_length_accepted(client, owner_headers, valid_payload):
    """AC: AC-02"""
    for phone in ("1234567", "+123456789012345"):  # 7 digits and 15 digits
        valid_payload["phone"] = phone
        response = client.put(URL, json=valid_payload, headers=owner_headers)
        assert response.status_code == 200, phone


def test_phone_outside_boundary_rejected(client, owner_headers, valid_payload):
    """AC: AC-02"""
    for phone in ("123456", "+1234567890123456"):  # 6 digits and 16 digits
        valid_payload["phone"] = phone
        response = client.put(URL, json=valid_payload, headers=owner_headers)
        assert response.status_code == 422, phone


# ---------------------------------------------------------------- authorization
def test_missing_token_returns_401(client, valid_payload):
    """AC: AC-03"""
    response = client.put(URL, json=valid_payload)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_tampered_token_returns_401(client, owner_headers, valid_payload):
    """AC: AC-03"""
    token = owner_headers["Authorization"].split(" ", 1)[1]
    tampered = token[:-4] + "0000"
    response = client.put(URL, json=valid_payload, headers={"Authorization": f"Bearer {tampered}"})
    assert response.status_code == 401


def test_other_customer_cannot_modify_returns_403_and_no_change(client, other_headers, owner_headers, valid_payload):
    """AC: AC-04"""
    response = client.put(URL, json=valid_payload, headers=other_headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    unchanged = client.get(URL, headers=owner_headers).json()
    assert unchanged["email"] == "ana.silva@example.com"


def test_audit_endpoint_requires_owner(client, other_headers):
    """AC: AC-04, AC-05"""
    response = client.get(f"{URL}/audit", headers=other_headers)
    assert response.status_code == 403


# ---------------------------------------------------------------- negative / errors
def test_unknown_customer_returns_404(client, valid_payload):
    """AC: AC-06"""
    from app.auth import issue_token

    headers = {"Authorization": f"Bearer {issue_token('cust-9999')}"}
    response = client.put("/customers/cust-9999/contact", json=valid_payload, headers=headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CUSTOMER_NOT_FOUND"


def test_error_response_envelope_shape(client, valid_payload):
    """AC: AC-06"""
    response = client.put(URL, json=valid_payload)
    body = response.json()
    assert set(body.keys()) == {"error"}
    assert set(body["error"].keys()) == {"code", "message", "details"}


# ---------------------------------------------------------------- audit
def test_audit_record_created_with_before_and_after(client, owner_headers, valid_payload):
    """AC: AC-05"""
    client.put(URL, json=valid_payload, headers={**owner_headers, "X-Request-Id": "req-42"})
    response = client.get(f"{URL}/audit", headers=owner_headers)
    assert response.status_code == 200
    entries = response.json()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["action"] == "CONTACT_UPDATED"
    assert entry["actor_id"] == OWNER
    assert entry["request_id"] == "req-42"
    assert entry["before"]["email"] == "ana.silva@example.com"
    assert entry["after"]["email"] == "ana.new@example.com"
    assert entry["recorded_at"]


def test_failed_update_creates_no_audit_record(client, owner_headers, other_headers, valid_payload):
    """AC: AC-04, AC-05"""
    client.put(URL, json=valid_payload, headers=other_headers)
    assert client.get(f"{URL}/audit", headers=owner_headers).json() == []
    assert OTHER != OWNER
