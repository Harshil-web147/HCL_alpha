"""Agent 5 - Code Reviewer Agent (UC-05 Automated Code Review)."""

from __future__ import annotations

from typing import Any

from app.agents.base import Agent, code_block, dumps, feature_block
from app.models import ReviewReport, WorkflowState


class CodeReviewerAgent(Agent[ReviewReport]):
    name = "code_reviewer"
    role = "Code Reviewer Agent"
    schema = ReviewReport

    def instructions(self) -> str:
        return """
Review the proposed implementation against the requirement analysis and technical design. Look for:
logic defects, missing validation, exception-handling gaps, maintainability problems, duplicate
logic, incorrect API behaviour versus the design, dead code, resource-handling issues, potential
performance concerns and testability.
Each finding MUST contain: Severity (CRITICAL/HIGH/MEDIUM/LOW/INFO) | Component | Finding | Evidence
(file:line and the offending code) | Recommended Fix, with ids REV-01, REV-02 ...
Use source="ai_review". Reserve CRITICAL/HIGH for defects that break required behaviour or
acceptance criteria; those will block release. Do not repeat findings already reported by the
deterministic tools (shown below) unless you add new evidence. Set verdict to "request_changes"
when any CRITICAL/HIGH finding exists, otherwise "approve" or "comment".
"""

    def user_prompt(self, state: WorkflowState, **kwargs: Any) -> str:
        tool_summaries = "\n".join(f"- {r.tool}: {r.status} - {r.summary}" for r in state.latest_tool_runs().values())
        return (
            feature_block(state)
            + "\nREQUIREMENT ANALYSIS\n"
            + dumps(state.requirement_analysis)
            + "\n\nTECHNICAL DESIGN\n"
            + dumps(state.technical_design)
            + "\n\nDETERMINISTIC TOOL RESULTS ALREADY AVAILABLE\n"
            + (tool_summaries or "(none yet)")
            + "\n\nPROPOSED IMPLEMENTATION\n"
            + code_block(state)
            + "\n\nProduce the ReviewReport artifact."
        )
