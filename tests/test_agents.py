"""Every agent must turn workflow state into a schema-valid artifact."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.agents import (
    BusinessAnalystAgent,
    CodeReviewerAgent,
    DefectResolutionAgent,
    DeveloperAgent,
    ReleaseAssuranceAgent,
    SecurityReviewAgent,
    SolutionDesignAgent,
    TestEngineerAgent,
)
from app.llm.client import LLMClient, LLMError, extract_json
from app.models import Finding, RequirementAnalysis, Severity, WorkflowState


@pytest.fixture
def state(example_request, llm):
    st = WorkflowState(feature_id="feat-test", request=example_request)
    st.requirement_analysis = BusinessAnalystAgent(llm).run(st)
    st.technical_design = SolutionDesignAgent(llm).run(st)
    st.code_bundle = DeveloperAgent(llm).run(st)
    st.test_bundle = TestEngineerAgent(llm).run(st)
    return st


def test_business_analyst_surfaces_ambiguities_and_measurable_ac(state):
    ra = state.requirement_analysis
    assert isinstance(ra, RequirementAnalysis)
    assert ra.ambiguities, "assumptions must be surfaced, not silently converted into requirements"
    assert all(ac.measurable for ac in ra.acceptance_criteria)
    story_ids = {s.id for s in ra.user_stories}
    assert {ac.story_id for ac in ra.acceptance_criteria} <= story_ids
    assert all(ac.given and ac.when and ac.then for ac in ra.acceptance_criteria)


def test_design_maps_components_and_endpoints_to_requirements(state):
    d = state.technical_design
    req_ids = {r.id for r in state.requirement_analysis.functional_requirements + state.requirement_analysis.non_functional_requirements}
    ac_ids = {ac.id for ac in state.requirement_analysis.acceptance_criteria}
    assert d.components and d.api and d.data_model and d.file_plan
    assert all(set(c.requirement_ids) <= req_ids for c in d.components)
    assert all(set(e.acceptance_criteria_ids) <= ac_ids for e in d.api)


def test_developer_produces_every_planned_file_with_traceability(state):
    paths = {f.path for f in state.code_bundle.files}
    assert {fp.path for fp in state.technical_design.file_plan} <= paths
    assert "pytest.ini" in paths and "requirements.txt" in paths
    assert any(f.implements for f in state.code_bundle.files)
    assert "production-ready" not in state.code_bundle.notes.lower() or "not production-ready" in state.code_bundle.notes.lower()


def test_test_engineer_covers_every_acceptance_criterion(state):
    ac_ids = {ac.id for ac in state.requirement_analysis.acceptance_criteria}
    covered = {ac for tc in state.test_bundle.test_cases for ac in tc.acceptance_criteria_ids}
    assert ac_ids <= covered
    categories = {tc.category for tc in state.test_bundle.test_cases}
    assert {"positive", "negative", "boundary", "validation", "authorization"} <= categories


def test_reviewer_findings_have_required_fields(state, llm):
    report = CodeReviewerAgent(llm).run(state)
    assert report.findings
    for f in report.findings:
        assert f.severity and f.component and f.finding and f.evidence and f.recommended_fix


def test_security_agent_only_reports_secret_while_present(state, llm):
    report = SecurityReviewAgent(llm).run(state)
    assert any(f.category == "secrets_exposure" and f.severity == Severity.HIGH for f in report.findings)
    cfg = next(f for f in state.code_bundle.files if f.path == "app/config.py")
    cfg.content = cfg.content.replace('JWT_SECRET = "hardcoded-dev-secret-9f8e7d6c5b4a3210"', 'JWT_SECRET = os.environ["JWT_SECRET"]')
    report = SecurityReviewAgent(llm).run(state)
    assert not any(f.category == "secrets_exposure" for f in report.findings)


def test_defect_resolver_escalates_when_it_cannot_fix(state, llm):
    failure = Finding(id="TEST-01", severity=Severity.HIGH, component="tests", finding="Test test_x failed", evidence="assert 1 == 2", recommended_fix="", source="pytest")
    proposal = DefectResolutionAgent(llm).run(state, failures=[failure], failed_runs=[])
    assert proposal.changes == [] and proposal.confidence == "low"


def test_release_agent_never_says_ready_with_failed_gates(state, llm):
    state.findings.append(Finding(id="SEC-99", severity=Severity.HIGH, component="x", finding="bad", evidence="e", recommended_fix="f", source="security_agent"))
    assessment = ReleaseAssuranceAgent(llm).run(state)
    assert assessment.decision.value == "NOT_READY"


def test_extract_json_handles_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here you go: {"a": {"b": 2}} thanks') == {"a": {"b": 2}}
    with pytest.raises(LLMError):
        extract_json("no json here")


def test_llm_client_self_repairs_after_invalid_output(settings):
    class Schema(BaseModel):
        value: int

    class FlakyProvider:
        name = "flaky"
        calls = 0

        def complete_json(self, *, agent, system, user, schema):
            self.calls += 1
            if self.calls == 1:
                return {"value": "not-an-int"}
            assert "failed validation" in user
            return {"value": 7}

    provider = FlakyProvider()
    calls = []
    client = LLMClient(settings, provider=provider, observer=calls.append)
    assert client.generate("t", "s", "u", Schema).value == 7
    assert [c["ok"] for c in calls] == [False, True]
