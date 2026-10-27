"""Quality gates and the deterministic release policy."""

from __future__ import annotations

from app.agents import BusinessAnalystAgent
from app.models import (
    AcceptanceCriterion,
    Approval,
    Finding,
    GateResult,
    ReleaseAssessment,
    ReleaseDecision,
    Severity,
    ToolRun,
    UserStory,
    WorkflowState,
)
from app.orchestration.gates import GateConfig, GateEngine


def _state(example_request, llm) -> WorkflowState:
    st = WorkflowState(feature_id="feat-g", request=example_request)
    st.requirement_analysis = BusinessAnalystAgent(llm).run(st)
    return st


def test_gate_config_loads_every_gate(gate_config):
    assert set(gate_config.gates) == {"requirement", "code", "test", "security", "traceability", "release"}
    assert gate_config.max_iterations == 3 and gate_config.require_remediation_approval


def test_requirement_gate_fails_when_a_story_has_no_acceptance_criteria(example_request, llm, gate_config):
    st = _state(example_request, llm)
    engine = GateEngine(gate_config)
    assert engine.evaluate("requirement", st).passed
    st.requirement_analysis.user_stories.append(UserStory(id="US-99", role="x", goal="y", benefit="z"))
    result = engine.evaluate("requirement", st)
    assert not result.passed
    assert "US-99" in next(c.evidence for c in result.checks if c.name == "acceptance_criteria_defined")


def test_requirement_gate_requires_mandatory_nfr_categories(example_request, llm, gate_config):
    st = _state(example_request, llm)
    st.requirement_analysis.non_functional_requirements = []
    result = GateEngine(gate_config).evaluate("requirement", st)
    assert not result.passed
    assert "missing categories" in next(c.evidence for c in result.checks if c.name == "mandatory_nfrs_considered")


def test_security_gate_blocks_on_high_findings_and_requires_scanners(example_request, llm, gate_config):
    st = _state(example_request, llm)
    engine = GateEngine(gate_config)
    result = engine.evaluate("security", st)
    assert not result.passed  # scanners never ran
    st.tool_runs += [ToolRun(tool="bandit", status="passed", summary="ok"), ToolRun(tool="secrets", status="passed", summary="ok")]
    assert engine.evaluate("security", st).passed
    st.findings.append(Finding(id="SEC-01", severity=Severity.HIGH, component="c", finding="f", evidence="e", recommended_fix="r", source="security_agent"))
    result = engine.evaluate("security", st)
    assert not result.passed
    st.findings[0].status = "resolved"
    assert engine.evaluate("security", st).passed


def test_test_gate_requires_ac_mapping_and_minimum_tests(example_request, llm, gate_config):
    st = _state(example_request, llm)
    engine = GateEngine(gate_config)
    st.tool_runs.append(ToolRun(tool="pytest", status="passed", summary="2 passed", metrics={"passed": 2, "failed": 0, "total": 2}))
    result = engine.evaluate("test", st)
    assert not result.passed
    failed = {c.name for c in result.checks if not c.passed}
    assert failed == {"mandatory_tests_executed", "acceptance_criteria_have_tests"}


def test_release_policy_overrides_optimistic_agent(example_request, llm, gate_config):
    st = _state(example_request, llm)
    engine = GateEngine(gate_config)
    st.gate_results.append(GateResult(gate="security", mandatory=True, passed=False, checks=[]))
    assessment = engine.apply_release_policy(ReleaseAssessment(decision=ReleaseDecision.READY, rationale="looks fine", evidence=[]), st)
    assert assessment.decision == ReleaseDecision.NOT_READY and "policy enforced NOT_READY" in assessment.policy_override


def test_release_policy_downgrades_ready_to_conditional_on_medium_findings(example_request, llm, gate_config):
    st = _state(example_request, llm)
    engine = GateEngine(gate_config)
    st.findings.append(Finding(id="REV-01", severity=Severity.MEDIUM, component="c", finding="f", evidence="e", recommended_fix="r", source="ai_review"))
    assessment = engine.apply_release_policy(ReleaseAssessment(decision=ReleaseDecision.READY, rationale="ok", evidence=[]), st)
    assert assessment.decision == ReleaseDecision.CONDITIONAL


def test_release_policy_forces_manual_review_after_human_rejection(example_request, llm, gate_config):
    st = _state(example_request, llm)
    st.approvals.append(Approval(checkpoint="remediation", decision="reject", approver="dev"))
    assessment = GateEngine(gate_config).apply_release_policy(ReleaseAssessment(decision=ReleaseDecision.CONDITIONAL, rationale="ok", evidence=[]), st)
    assert assessment.decision == ReleaseDecision.MANUAL_REVIEW


def test_release_policy_never_upgrades_a_pessimistic_agent(example_request, llm, gate_config):
    st = _state(example_request, llm)
    assessment = GateEngine(gate_config).apply_release_policy(ReleaseAssessment(decision=ReleaseDecision.NOT_READY, rationale="agent says no", evidence=[]), st)
    assert assessment.decision == ReleaseDecision.NOT_READY and not assessment.policy_override


def test_gate_checks_are_configurable():
    cfg = GateConfig({"gates": {"security": {"mandatory": False, "checks": ["no_exposed_credentials"], "required_scanners": []}}})
    st = WorkflowState(feature_id="f", request={"title": "abc", "description": "0123456789"})
    st.tool_runs.append(ToolRun(tool="secrets", status="passed", summary="clean"))
    result = GateEngine(cfg).evaluate("security", st)
    assert result.passed and not result.mandatory and [c.name for c in result.checks] == ["no_exposed_credentials"]
    assert any(ac.id for ac in [AcceptanceCriterion(id="AC-1", story_id="US-1", given="g", when="w", then="t")])
