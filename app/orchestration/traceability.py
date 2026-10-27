"""Requirement -> story -> acceptance criteria -> design -> code -> test -> result traceability (FR-14)."""

from __future__ import annotations

from app.models import TraceabilityMatrix, TraceabilityRow, WorkflowState


def build_traceability(state: WorkflowState) -> TraceabilityMatrix:
    ra = state.requirement_analysis
    if ra is None:
        return TraceabilityMatrix(rows=[])

    design = state.technical_design
    code_files = state.code_bundle.files if state.code_bundle else []
    test_cases = state.test_bundle.test_cases if state.test_bundle else []
    pytest_run = state.latest_tool_runs().get("pytest")
    results = {t.name: t for t in pytest_run.test_results} if pytest_run else {}

    # AC -> tests (from the test bundle mapping and from executed results)
    ac_tests: dict[str, set[str]] = {}
    for tc in test_cases:
        for ac in tc.acceptance_criteria_ids:
            ac_tests.setdefault(ac, set()).add(tc.name)
    for name, res in results.items():
        for ac in res.acceptance_criteria_ids:
            ac_tests.setdefault(ac, set()).add(name)

    rows: list[TraceabilityRow] = []
    requirements = ra.functional_requirements + ra.non_functional_requirements
    for req in requirements:
        stories = [s for s in ra.user_stories if req.id in s.requirement_ids]
        story_ids = [s.id for s in stories]
        acs = [ac for ac in ra.acceptance_criteria if ac.story_id in story_ids]
        ac_ids = [ac.id for ac in acs]
        components = [c.name for c in (design.components if design else []) if req.id in c.requirement_ids]
        files = sorted({f.path for f in code_files if req.id in f.implements or any(ac in f.implements for ac in ac_ids)})
        tests: set[str] = set()
        for ac in ac_ids:
            tests |= ac_tests.get(ac, set())
        outcomes = {t: results[t].outcome for t in sorted(tests) if t in results}
        rows.append(
            TraceabilityRow(
                requirement_id=req.id,
                story_ids=story_ids,
                acceptance_criteria_ids=ac_ids,
                design_components=components,
                code_files=files,
                tests=sorted(tests),
                test_outcomes=outcomes,
            )
        )

    functional_ids = [r.id for r in ra.functional_requirements]
    functional_rows = [r for r in rows if r.requirement_id in functional_ids]
    unmapped_req = [r.requirement_id for r in functional_rows if not r.code_files]
    all_ac = [ac.id for ac in ra.acceptance_criteria]
    unmapped_ac = [ac for ac in all_ac if not ac_tests.get(ac)]
    mapped_tests = {t for s in ac_tests.values() for t in s}
    with_results = [t for t in mapped_tests if t in results]

    return TraceabilityMatrix(
        rows=rows,
        requirement_coverage=round(100.0 * (len(functional_rows) - len(unmapped_req)) / len(functional_rows), 1) if functional_rows else 0.0,
        acceptance_criteria_coverage=round(100.0 * (len(all_ac) - len(unmapped_ac)) / len(all_ac), 1) if all_ac else 0.0,
        tests_with_results=round(100.0 * len(with_results) / len(mapped_tests), 1) if mapped_tests else 0.0,
        unmapped_requirements=unmapped_req,
        unmapped_acceptance_criteria=unmapped_ac,
    )
