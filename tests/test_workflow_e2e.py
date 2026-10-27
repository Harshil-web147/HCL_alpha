"""End-to-end orchestration: the full 19-step workflow including the remediation loop,
human approvals, traceability and the release policy (stub LLM, real tools)."""

from __future__ import annotations

import pytest

from app.models import ApprovalRequest, ReleaseDecision, Severity, Stage
from app.orchestration.engine import WorkflowEngine, WorkflowError
from app.orchestration.gates import GateConfig

pytestmark = pytest.mark.e2e


def _approve(engine, fid, checkpoint, decision="approve", comment=""):
    return engine.approve(fid, checkpoint, ApprovalRequest(decision=decision, approver="tester", comment=comment))


def test_full_workflow_with_remediation_loop(engine, example_request, settings):
    state = engine.create(example_request)
    fid = state.feature_id

    # 1-7: requirement analysis, gate, design -> pause for human design approval
    state = engine.run(fid)
    assert state.stage == Stage.AWAITING_DESIGN_APPROVAL
    assert state.latest_gate_results()["requirement"].passed
    assert state.technical_design is not None

    with pytest.raises(WorkflowError):  # cannot approve the wrong checkpoint
        _approve(engine, fid, "release")

    # 8-14: code, tests, execution, static analysis, security, AI review, gates -> security gate fails
    state = _approve(engine, fid, "design")
    state = engine.run(fid)
    assert state.stage == Stage.AWAITING_REMEDIATION_APPROVAL
    gates = state.latest_gate_results()
    assert gates["code"].passed and gates["test"].passed and not gates["security"].passed
    tools = state.latest_tool_runs()
    assert tools["pytest"].status == "passed" and tools["pytest"].metrics["total"] >= 10
    assert tools["secrets"].status == "failed"
    open_ids = {f.id for f in state.open_findings()}
    assert {"SECRETS-01", "SEC-01", "BANDIT-01"} <= open_ids
    assert state.iteration == 1 and state.remediation_proposal is not None
    assert state.modifications and not state.modifications[0].approved_by  # recorded but NOT applied yet
    assert (settings.workspace_root / fid / "app" / "config.py").read_text().count("hardcoded-dev-secret") == 1

    # 15-16: human approves the AI correction -> applied, revalidated, gates re-evaluated
    state = _approve(engine, fid, "remediation")
    assert state.modifications[0].approved_by == "tester" and state.modifications[0].applied_at
    assert "hardcoded-dev-secret" not in (settings.workspace_root / fid / "app" / "config.py").read_text()
    state = engine.run(fid)
    assert state.stage == Stage.AWAITING_RELEASE_APPROVAL
    resolved = {f.id for f in state.findings if f.status == "resolved"}
    assert {"SECRETS-01", "SEC-01", "BANDIT-01", "RUFF-01"} <= resolved
    assert not any(f.severity in (Severity.HIGH, Severity.CRITICAL) for f in state.open_findings())
    assert all(g.passed for g in state.latest_gate_results().values())
    assert any("->" in h for h in state.remediation_history)  # before/after comparison recorded

    # 17-18: traceability + release assessment
    t = state.traceability
    assert t.requirement_coverage == 100 and t.acceptance_criteria_coverage == 100 and t.tests_with_results == 100
    assert state.release_assessment.decision == ReleaseDecision.CONDITIONAL  # MEDIUM findings remain

    # 19: human release approval
    state = _approve(engine, fid, "release")
    assert state.stage == Stage.COMPLETED
    assert [a.checkpoint for a in state.approvals] == ["design", "remediation", "release"]

    # observability: every AI call, tool call, gate and human decision is on record
    types = {e["type"] for e in engine.store.events(fid)}
    assert {"agent_call", "tool_call", "gate_evaluation", "human_decision", "ai_modification", "stage_transition", "policy_decision"} <= types


def test_rejecting_the_ai_correction_leads_to_manual_review(engine, example_request):
    fid = engine.create(example_request).feature_id
    engine.run(fid)
    _approve(engine, fid, "design")
    state = engine.run(fid)
    assert state.stage == Stage.AWAITING_REMEDIATION_APPROVAL
    state = _approve(engine, fid, "remediation", decision="reject", comment="I want a secrets manager instead")
    state = engine.run(fid)
    assert state.stage == Stage.AWAITING_RELEASE_APPROVAL
    assert state.release_assessment.decision in (ReleaseDecision.NOT_READY, ReleaseDecision.MANUAL_REVIEW)
    assert state.release_assessment.policy_override or state.release_assessment.decision == ReleaseDecision.NOT_READY
    assert not state.latest_gate_results()["security"].passed
    assert not state.modifications[0].applied_at
    state = _approve(engine, fid, "release", decision="reject")
    assert state.stage == Stage.REJECTED


def test_design_rejection_with_feedback_triggers_redesign(engine, example_request):
    fid = engine.create(example_request).feature_id
    engine.run(fid)
    state = _approve(engine, fid, "design", decision="reject", comment="Add an admin role that may update any customer")
    assert state.stage == Stage.DESIGNING
    state = engine.run(fid)
    assert state.stage == Stage.AWAITING_DESIGN_APPROVAL
    assert state.human_feedback == ["Add an admin role that may update any customer"]
    state = _approve(engine, fid, "design", decision="reject")  # no comment -> terminal rejection
    assert state.stage == Stage.REJECTED


def test_auto_apply_policy_skips_the_remediation_pause(store, settings, gate_config):
    cfg = GateConfig(gate_config.raw)
    cfg.remediation = {**cfg.remediation, "require_human_approval": False}
    from app.llm.stub import StubProvider

    engine = WorkflowEngine(store, settings, provider=StubProvider(), gate_config=cfg)
    from tests.conftest import EXAMPLE_REQUEST

    fid = engine.create(EXAMPLE_REQUEST).feature_id
    engine.run(fid)
    _approve(engine, fid, "design")
    state = engine.run(fid)
    assert state.stage == Stage.AWAITING_RELEASE_APPROVAL
    assert state.modifications[0].approved_by == "auto-policy"
    assert all(g.passed for g in state.latest_gate_results().values())


class _NeedsGuidanceFirst:
    """Stub provider whose defect resolver asks for guidance until the developer has given some."""

    name = "stub"

    def __init__(self):
        from app.llm.stub import StubProvider

        self.stub = StubProvider()
        self.remediation_prompts: list[str] = []

    def complete_json(self, *, agent, system, user, schema):
        if schema.__name__ == "RemediationProposal":
            self.remediation_prompts.append(user)
            if "Use os.environ for the secret" not in user:
                return {"failure_summary": "secret detected", "probable_cause": "Where should the secret come from?",
                        "affected_files": [], "changes": [], "confidence": "low"}
        return self.stub.complete_json(agent=agent, system=system, user=user, schema=schema)


def test_ai_asks_for_guidance_and_retries_with_the_answer(store, settings, gate_config):
    provider = _NeedsGuidanceFirst()
    engine = WorkflowEngine(store, settings, provider=provider, gate_config=gate_config)
    from tests.conftest import EXAMPLE_REQUEST

    fid = engine.create(EXAMPLE_REQUEST).feature_id
    engine.run(fid)
    _approve(engine, fid, "design")
    state = engine.run(fid)
    # the agent gave up -> the workflow pauses for the developer instead of skipping ahead
    assert state.stage == Stage.AWAITING_REMEDIATION_APPROVAL and state.remediation_proposal.changes == []
    with pytest.raises(WorkflowError, match="needs guidance"):
        _approve(engine, fid, "remediation")  # approving without an answer is refused

    state = _approve(engine, fid, "remediation", comment="Use os.environ for the secret")
    assert state.stage == Stage.REMEDIATING and state.remediation_guidance == ["Use os.environ for the secret"]
    assert state.human_feedback == []  # guidance never leaks into requirement/design prompts
    state = engine.run(fid)
    assert state.stage == Stage.AWAITING_REMEDIATION_APPROVAL and state.remediation_proposal.changes  # real fix now
    assert "DEVELOPER GUIDANCE" in provider.remediation_prompts[-1]
    _approve(engine, fid, "remediation")
    state = engine.run(fid)
    assert state.stage == Stage.AWAITING_RELEASE_APPROVAL
    assert all(g.passed for g in state.latest_gate_results().values())


def test_skipping_guidance_escalates_to_release_assessment(store, settings, gate_config):
    engine = WorkflowEngine(store, settings, provider=_NeedsGuidanceFirst(), gate_config=gate_config)
    from tests.conftest import EXAMPLE_REQUEST

    fid = engine.create(EXAMPLE_REQUEST).feature_id
    engine.run(fid)
    _approve(engine, fid, "design")
    engine.run(fid)
    state = _approve(engine, fid, "remediation", decision="reject")
    assert state.stage == Stage.TRACEABILITY_CHECK
    state = engine.run(fid)
    assert state.stage == Stage.AWAITING_RELEASE_APPROVAL and state.release_assessment.decision == ReleaseDecision.NOT_READY
