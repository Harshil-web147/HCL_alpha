"""HTTP boundary validation and safe artifact exports."""

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.models import Store
from backend.orchestrator import Orchestrator


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "api.sqlite3"))
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "orchestrator", Orchestrator(store))
    monkeypatch.setattr(main, "schedule", lambda run_id: None)
    with TestClient(main.app) as client:
        yield client


@pytest.mark.integration
def test_intake_and_stale_approval(client):
    assert client.post("/api/runs", json={"requirement": " " * 20}).status_code == 422
    response = client.post(
        "/api/runs", json={"requirement": "Build a contact update API", "mode": "demo"}
    )
    assert response.status_code == 201
    rid = response.json()["data"]["id"]
    assert client.get(f"/api/runs/{rid}").status_code == 200
    assert (
        client.post(f"/api/runs/{rid}/approve", json={"kind": "design", "version": 1}).status_code
        == 409
    )
    assert client.get(f"/api/runs/{rid}/download").status_code == 409
    assert client.get("/api/runs/missing").status_code == 404


@pytest.mark.integration
def test_unsafe_generated_artifact_cannot_be_downloaded(client):
    run = main.orchestrator.create("Build a contact update API", "demo")
    main.orchestrator.artifact(
        run, "files", {"files": [{"path": "../outside.py", "content": "bad"}]}
    )
    assert client.get(f"/api/runs/{run['id']}/download").status_code == 409
