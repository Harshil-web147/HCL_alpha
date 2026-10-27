"""Agent 1 - Business Analyst Agent (UC-01 Requirement Intelligence)."""

from __future__ import annotations

from typing import Any

from app.agents.base import Agent, feature_block
from app.models import RequirementAnalysis, WorkflowState


class BusinessAnalystAgent(Agent[RequirementAnalysis]):
    name = "business_analyst"
    role = "Business Analyst Agent"
    schema = RequirementAnalysis

    def instructions(self) -> str:
        return """
Analyse the feature request and produce:
- functional requirements (FR-01..) and non-functional requirements (NFR-01..) - always consider
  security, audit/compliance, performance, logging/observability, availability and data privacy;
- business rules (BR-01..);
- user stories (US-01..) in role / goal / benefit form, each linked to requirement ids;
- measurable acceptance criteria (AC-01..) in Given / When / Then form, each linked to a story;
- assumptions (AS-01..) with needs_confirmation=true when a human must confirm them;
- dependencies on other systems / teams;
- technical questions for the engineers;
- ambiguities (AMB-01..): anything incomplete, contradictory or unspecified, each with a concrete
  clarification question and the default you would propose.
Every acceptance criterion must be testable by an automated test.
"""

    def user_prompt(self, state: WorkflowState, **kwargs: Any) -> str:
        feedback = "\n".join(f"- {f}" for f in state.human_feedback)
        return (
            feature_block(state)
            + ("\nFEEDBACK ON THE PREVIOUS ANALYSIS (must be addressed)\n" + feedback if feedback else "")
            + "\nProduce the RequirementAnalysis artifact."
        )
