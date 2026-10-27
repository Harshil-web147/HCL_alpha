"""Agent 7 - Release Assurance Agent."""

from __future__ import annotations

from typing import Any

from app.agents.base import Agent, dumps, feature_block
from app.models import ReleaseAssessment, Severity, WorkflowState


class ReleaseAssuranceAgent(Agent[ReleaseAssessment]):
    name = "release_assurance"
    role = "Release Assurance Agent"
    schema = ReleaseAssessment

    def instructions(self) -> str:
        return """
Consolidate the complete workflow evidence (gate results, test execution, static analysis, security
scanning, AI reviews, remediation history, traceability and human approvals) into a release
recommendation: READY, CONDITIONAL, MANUAL_REVIEW or NOT_READY.
- NOT_READY when any mandatory gate failed or any CRITICAL/HIGH finding is open.
- MANUAL_REVIEW when evidence is incomplete, remediation was exhausted or a human rejected a change.
- CONDITIONAL when every mandatory gate passed but MEDIUM findings or confirmed-pending assumptions
  remain; list the conditions.
- READY only when every gate passed and nothing is open.
Every statement in `evidence` must reference a concrete artifact (gate name, test counts, finding
ids, approval records). A deterministic release policy will override you if your decision is more
optimistic than the gate results allow.
"""

    def user_prompt(self, state: WorkflowState, **kwargs: Any) -> str:
        gates = state.latest_gate_results()
        failed_mandatory = [g for g in gates.values() if g.mandatory and not g.passed]
        open_findings = state.open_findings()
        blocking = [f for f in open_findings if f.severity in (Severity.CRITICAL, Severity.HIGH)]
        medium = [f for f in open_findings if f.severity == Severity.MEDIUM]
        runs = state.latest_tool_runs()
        evidence_lines = [
            f"Gate {g.gate}: {'PASSED' if g.passed else 'FAILED'} ({sum(c.passed for c in g.checks)}/{len(g.checks)} checks)"
            for g in gates.values()
        ]
        evidence_lines += [f"Tool {r.tool}: {r.status} - {r.summary}" for r in runs.values()]
        evidence_lines += [f"Approval {a.checkpoint}: {a.decision} by {a.approver} (iteration {a.iteration})" for a in state.approvals]
        evidence_lines += [f"Open finding {f.id} [{f.severity.value}] {f.finding[:90]}" for f in open_findings]
        if state.traceability:
            t = state.traceability
            evidence_lines.append(
                f"Traceability: requirement coverage {t.requirement_coverage:.0f}%, AC coverage {t.acceptance_criteria_coverage:.0f}%, tests with results {t.tests_with_results:.0f}%"
            )
        return (
            feature_block(state)
            + f"\nSUMMARY COUNTS\nMANDATORY_GATES_FAILED: {len(failed_mandatory)}\nOPEN_BLOCKING_FINDINGS: {len(blocking)}\n"
            + f"OPEN_MEDIUM_FINDINGS: {len(medium)}\nREMEDIATION_ITERATIONS: {state.iteration}\n"
            + "\nWORKFLOW EVIDENCE\n"
            + "\n".join(f"- {line}" for line in evidence_lines)
            + "\n\nREMEDIATION HISTORY\n"
            + ("\n".join(f"- {h}" for h in state.remediation_history) or "- none")
            + "\n\nREQUIREMENT ASSUMPTIONS NEEDING CONFIRMATION\n"
            + dumps([a.model_dump() for a in (state.requirement_analysis.assumptions if state.requirement_analysis else []) if a.needs_confirmation])
            + "\n\nProduce the ReleaseAssessment artifact."
        )
