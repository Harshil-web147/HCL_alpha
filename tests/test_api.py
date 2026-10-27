"""REST API + dashboard endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

pytestmark = pytest.mark.e2e

PAYLOAD = {
    "title": "Customer contact update API",
    "description": "Develop an API allowing customers to update their contact information. Validate mandatory fields, prevent unauthorized modification, maintain an audit record and return appropriate API error responses.",
}


@pytest.fixture
def client(engine):
    return TestClient(create_app(engine))


def test_dashboard_and_health(client):
    assert client.get("/health").json()["llm_provider"] == "stub"
    html = client.get("/").text
    assert "SDLC Engineering Copilot" in html and "/api/dashboard/summary" in html
    assert client.get("/api/stages").json()[0] == "received"
    assert "gates" in client.get("/api/gates").json()


def test_intake_validation(client):
    assert client.post("/api/features", json={"title": "x", "description": "short"}).status_code == 422


def test_feature_lifecycle_through_the_api(client):
    r = client.post("/api/features?wait=true", json=PAYLOAD)
    assert r.status_code == 201
    fid = r.json()["feature_id"]
    assert r.json()["stage"] == "awaiting_design_approval"

    # cannot run while paused; wrong checkpoint is rejected
    assert client.post(f"/api/features/{fid}/run").status_code == 409
    assert client.post(f"/api/features/{fid}/approvals/release", json={"decision": "approve"}).status_code == 409
    assert client.post(f"/api/features/{fid}/approvals/bogus", json={"decision": "approve"}).status_code == 409

    r = client.post(f"/api/features/{fid}/approvals/design?wait=true", json={"decision": "approve", "approver": "dev"})
    assert r.json()["stage"] == "awaiting_remediation_approval"
    detail = client.get(f"/api/features/{fid}").json()
    assert detail["paused"] and detail["remediation_proposal"]["affected_files"] == ["app/config.py"]
    assert "content" not in detail["code_bundle"]["files"][0] and detail["code_bundle"]["files"][0]["size"] > 0
    mods = client.get(f"/api/features/{fid}/artifacts/modifications").json()
    assert mods[0]["file"] == "app/config.py" and "-JWT_SECRET" in mods[0]["diff"]

    r = client.post(f"/api/features/{fid}/approvals/remediation?wait=true", json={"decision": "approve", "approver": "dev"})
    assert r.json()["stage"] == "awaiting_release_approval"
    trace = client.get(f"/api/features/{fid}/traceability").json()
    assert trace["requirement_coverage"] == 100
    ws = client.get(f"/api/features/{fid}/workspace").json()["files"]
    assert "app/config.py" in ws and "tests/test_contact_api.py" in ws
    assert "JWT_SECRET" in client.get(f"/api/features/{fid}/workspace?path=app/config.py").json()["content"]
    assert client.get(f"/api/features/{fid}/workspace?path=../../etc/passwd").status_code == 404

    r = client.post(f"/api/features/{fid}/approvals/release?wait=true", json={"decision": "approve", "approver": "lead"})
    assert r.json()["stage"] == "completed"

    summary = client.get("/api/dashboard/summary").json()
    assert summary["features_processed"] == 1 and summary["readiness"]["CONDITIONAL"] == 1
    assert summary["gate_status"]["security"] == {"passed": 1, "failed": 0}
    events = client.get(f"/api/features/{fid}/events").json()
    assert any(e["type"] == "human_decision" and e["actor"] == "lead" for e in events)
    assert client.get(f"/api/features/{fid}/artifacts/nope").status_code == 404
    assert client.get("/api/features/does-not-exist").status_code == 404
    assert client.delete(f"/api/features/{fid}").status_code == 204
    assert client.get("/api/features").json() == []
