"""Traceability matrix construction and SQLite persistence."""

from __future__ import annotations

from app.agents import BusinessAnalystAgent, DeveloperAgent, SolutionDesignAgent, TestEngineerAgent
from app.models import TestResult, ToolRun, WorkflowState
from app.orchestration.traceability import build_traceability
from app.persistence import Store


def _state(example_request, llm) -> WorkflowState:
    st = WorkflowState(feature_id="feat-t", request=example_request)
    st.requirement_analysis = BusinessAnalystAgent(llm).run(st)
    st.technical_design = SolutionDesignAgent(llm).run(st)
    st.code_bundle = DeveloperAgent(llm).run(st)
    st.test_bundle = TestEngineerAgent(llm).run(st)
    return st


def test_traceability_without_results_reports_partial_coverage(example_request, llm):
    st = _state(example_request, llm)
    t = build_traceability(st)
    assert t.requirement_coverage == 100 and t.acceptance_criteria_coverage == 100
    assert t.tests_with_results == 0  # nothing executed yet
    row = next(r for r in t.rows if r.requirement_id == "FR-03")
    assert "app/auth.py" in row.code_files and row.story_ids == ["US-02"]
    assert any(name.startswith("test_") for name in row.tests)


def test_traceability_reports_unmapped_items(example_request, llm):
    st = _state(example_request, llm)
    for f in st.code_bundle.files:
        f.implements = [i for i in f.implements if i not in ("FR-04", "AC-05")]
    st.test_bundle.test_cases = [tc for tc in st.test_bundle.test_cases if "AC-05" not in tc.acceptance_criteria_ids]
    t = build_traceability(st)
    assert "FR-04" in t.unmapped_requirements and "AC-05" in t.unmapped_acceptance_criteria
    assert t.requirement_coverage < 100 and t.acceptance_criteria_coverage < 100


def test_traceability_attaches_execution_outcomes(example_request, llm):
    st = _state(example_request, llm)
    names = [tc.name for tc in st.test_bundle.test_cases]
    st.tool_runs.append(
        ToolRun(
            tool="pytest",
            status="passed",
            summary="ok",
            test_results=[TestResult(name=n, outcome="passed", acceptance_criteria_ids=tc.acceptance_criteria_ids) for n, tc in zip(names, st.test_bundle.test_cases, strict=True)],
        )
    )
    t = build_traceability(st)
    assert t.tests_with_results == 100
    assert all(v == "passed" for r in t.rows for v in r.test_outcomes.values())


def test_store_round_trips_state_and_events(tmp_path, example_request):
    store = Store(tmp_path / "db.sqlite")
    st = WorkflowState(feature_id="feat-p", request=example_request)
    store.save_state(st)
    store.add_event("feat-p", "tool_call", "pytest", "ran", {"n": 1})
    loaded = store.load_state("feat-p")
    assert loaded is not None and loaded.request.title == example_request.title
    assert store.events("feat-p")[0]["payload"] == {"n": 1}
    assert store.list_features()[0].feature_id == "feat-p"
    assert store.dashboard_summary()["features_processed"] == 1
    store.delete_feature("feat-p")
    assert store.load_state("feat-p") is None and store.events("feat-p") == []
