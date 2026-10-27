"""Agent 6b - Defect Resolution Agent (UC-07 AI Defect Remediation)."""

from __future__ import annotations

from typing import Any

from app.agents.base import Agent, code_block, dumps, feature_block
from app.models import Finding, RemediationProposal, ToolRun, WorkflowState


class DefectResolutionAgent(Agent[RemediationProposal]):
    name = "defect_resolver"
    role = "Defect Resolution Agent"
    schema = RemediationProposal

    def instructions(self) -> str:
        return """
You receive the failures from unit testing, static analysis, security scanning and code review.
For the blocking failures:
1. capture the failure, 2. identify the probable cause, 3. determine the affected code,
4. generate a proposed correction as COMPLETE replacement file contents (never fragments or diffs)
for each affected file, keeping every other behaviour and every passing test intact.
Rules:
- Only touch files that are necessary; never weaken or delete tests to make them pass.
- Never introduce new secrets, eval/exec, shell execution or disabled security checks.
- List the finding ids you address in addressed_finding_ids.
- If you cannot propose a safe fix with confidence, return an empty `changes` list with
  confidence="low" so the change is escalated to a developer instead of guessing. In that case
  `probable_cause` must end with the concrete question(s) the developer has to answer
  (e.g. "Which storage should be used: SQLite, PostgreSQL or keep in-memory?").
The correction will be applied only after human approval and will be re-validated by every tool.
"""

    def user_prompt(self, state: WorkflowState, **kwargs: Any) -> str:
        failures: list[Finding] = kwargs.get("failures", [])
        failed_runs: list[ToolRun] = kwargs.get("failed_runs", [])
        run_text = "\n".join(
            f"- {r.tool}: {r.status} - {r.summary}\n  output: {r.raw_output[-1500:]}" for r in failed_runs
        )
        return (
            feature_block(state)
            + "\nFAILED ENGINEERING CONTROLS\n"
            + (run_text or "(none)")
            + "\n\nBLOCKING FINDINGS TO REMEDIATE\n"
            + dumps([f.model_dump() for f in failures])
            + "\n\nPREVIOUS REMEDIATION ATTEMPTS\n"
            + ("\n".join(f"- {h}" for h in state.remediation_history) or "(none)")
            + "\n\nDEVELOPER GUIDANCE (answers to your earlier escalations - these decisions are made, follow them)\n"
            + ("\n".join(f"- {g}" for g in state.remediation_guidance) or "(none)")
            + "\n\nCURRENT WORKSPACE CODE\n"
            + code_block(state)
            + "\n\nProduce the RemediationProposal artifact."
        )
