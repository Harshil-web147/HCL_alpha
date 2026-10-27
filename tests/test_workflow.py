"""Exercise the state machine with deterministic tooling evidence."""

import asyncio

import pytest

from backend.models import Store
from backend.orchestrator import Orchestrator, WorkflowError
from backend.services import runner, workspace


@pytest.fixture
def flow(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "ROOT", tmp_path / "projects")
    return Orchestrator(Store(str(tmp_path / "test.sqlite3")))


def tools(path, version):
    failed = version == 1
    from backend.agents.demo import MAPPING

    tests = {m["test"]: "FAILED" if failed and m["ac_id"] == "AC-02" else "PASSED" for m in MAPPING}
    return [
        {
            "name": name,
            "command": name,
            "status": "failed" if failed and name == "pytest" else "passed",
            "exit_code": 1 if failed and name == "pytest" else 0,
            "output": "Test evidence",
            "version": version,
            "tests": tests if name == "pytest" else {},
        }
        for name in ("syntax", "pytest", "ruff", "bandit", "pip-audit")
    ]


@pytest.mark.integration
def test_approval_correction_and_release(flow, monkeypatch):
    monkeypatch.setattr(runner, "run_checks", tools)

    async def scenario():
        run = flow.create("Update a customer phone number with validation", "demo")
        rid = run["id"]
        await flow.advance(rid)
        assert flow.store.get(rid)["current_stage"] == "design_approval"
        with pytest.raises(WorkflowError):
            await flow.approve(rid, "design", 99, "")
        await flow.approve(rid, "design", 1, "")
        await flow.advance(rid)
        run = flow.store.get(rid)
        assert run["current_stage"] == "fix_approval"
        assert any(c["status"] == "failed" for c in run["checks"])
        await flow.approve(rid, "fix", 1, "")
        await flow.advance(rid)
        run = flow.store.get(rid)
        assert run["version"] == 2
        assert run["current_stage"] == "release_review"
        assert all(t["execution"] == "PASSED" for t in run["traceability"])
        assert run["report"]["recommendation"] == "MANUAL REVIEW"
        run = await flow.approve(rid, "release", 2, "")
        assert run["status"] == "completed"
        assert run["report"]["recommendation"] == "CONDITIONAL"
        with pytest.raises(WorkflowError):
            await flow.approve(rid, "release", 2, "")

    asyncio.run(scenario())


@pytest.mark.integration
def test_missing_tools_cannot_be_ready(flow, monkeypatch):
    def unavailable(path, version):
        return [
            {
                "name": n,
                "command": n,
                "status": "not_executed",
                "version": version,
                "exit_code": None,
                "output": "Docker unavailable",
                "tests": {},
            }
            for n in runner.COMMANDS
        ]

    monkeypatch.setattr(runner, "run_checks", unavailable)

    async def scenario():
        run = flow.create("Update a customer phone number with validation", "demo")
        rid = run["id"]
        await flow.advance(rid)
        await flow.approve(rid, "design", 1, "")
        await flow.advance(rid)
        run = flow.store.get(rid)
        # Source review may detect the deliberate defect even without tools.
        if run["current_stage"] == "fix_approval":
            await flow.approve(rid, "fix", 1, "")
            await flow.advance(rid)
        run = flow.store.get(rid)
        assert run["current_stage"] == "manual_review"
        assert run["report"]["recommendation"] == "MANUAL REVIEW"
        with pytest.raises(WorkflowError):
            await flow.approve(rid, "release", run["version"], "")

    asyncio.run(scenario())


@pytest.mark.integration
def test_correction_stops_after_two_rounds(flow, monkeypatch):
    def always_failed(path, version):
        checks = tools(path, version)
        checks[1]["status"] = "failed"
        checks[1]["exit_code"] = 1
        return checks

    monkeypatch.setattr(runner, "run_checks", always_failed)

    async def scenario():
        run = flow.create("Update a customer phone with format validation", "demo")
        rid = run["id"]
        await flow.advance(rid)
        await flow.approve(rid, "design", 1, "")
        await flow.advance(rid)
        for version in (1, 2):
            assert flow.store.get(rid)["current_stage"] == "fix_approval"
            await flow.approve(rid, "fix", version, "")
            await flow.advance(rid)
        result = flow.store.get(rid)
        assert result["correction_round"] == 2
        assert result["current_stage"] == "manual_review"
        assert result["report"]["recommendation"] == "NOT READY"

    asyncio.run(scenario())


@pytest.mark.unit
def test_traceability_requires_existing_implementation(flow):
    files = {"app/main.py": "def update_contact():\n    return {}\n"}
    assert flow.implementation_exists("app/main.py → update_contact()", files)
    assert not flow.implementation_exists("app/missing.py → update_contact()", files)
    assert not flow.implementation_exists("app/main.py → invented_function()", files)


@pytest.mark.integration
def test_clarification_creates_new_approval_version(flow, monkeypatch):
    from backend.agents import analyst
    from backend.agents.demo import ANALYSIS

    async def analyze(requirement, mode, answers=""):
        return {**ANALYSIS, "questions": [] if answers else ["Which phone format?"]}

    monkeypatch.setattr(analyst, "run", analyze)

    async def scenario():
        run = flow.create("Update a customer phone with format validation", "demo")
        rid = run["id"]
        await flow.advance(rid)
        revised = await flow.approve(rid, "design", 1, "E.164 international")
        assert revised["current_stage"] == "design_approval"
        assert revised["version"] == 2
        assert revised["approvals"] == []
        with pytest.raises(WorkflowError):
            await flow.approve(rid, "design", 1, "")
        approved = await flow.approve(rid, "design", 2, "")
        assert approved["current_stage"] == "generating"

    asyncio.run(scenario())
