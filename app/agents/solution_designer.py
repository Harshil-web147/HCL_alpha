"""Agent 2 - Solution Design Agent (UC-02 AI-Assisted Technical Design)."""

from __future__ import annotations

from typing import Any

from app.agents.base import Agent, dumps, feature_block
from app.models import TechnicalDesign, WorkflowState


class SolutionDesignAgent(Agent[TechnicalDesign]):
    name = "solution_designer"
    role = "Solution Design Agent"
    schema = TechnicalDesign

    def instructions(self) -> str:
        return """
From the approved requirement analysis, produce a technical design for a Python + FastAPI service:
- application components with clear responsibilities, each mapped to requirement ids;
- the ordered component flow (e.g. UI -> Customer API -> Authentication -> Profile Service -> Database -> Audit Service);
- REST API design: method, path, request/response schemas, error responses (status + machine code)
  and the acceptance criteria each endpoint satisfies;
- data model entities with typed fields and constraints;
- validation rules, error-handling strategy (uniform error envelope), logging requirements
  (no PII in logs) and security considerations (authn, authz, secrets from configuration, injection safety);
- a file plan: every source file the Developer Agent must produce (paths relative to the workspace,
  code under app/, e.g. app/main.py) with purpose and the requirement / AC ids it implements.
Honour the stated assumptions and proposed defaults for ambiguities unless the human comment says otherwise.
"""

    def user_prompt(self, state: WorkflowState, **kwargs: Any) -> str:
        feedback = "\n".join(f"- {f}" for f in state.human_feedback)
        return (
            feature_block(state)
            + "\nAPPROVED REQUIREMENT ANALYSIS\n"
            + dumps(state.requirement_analysis)
            + ("\n\nHUMAN REVIEW FEEDBACK ON THE PREVIOUS DESIGN (must be addressed)\n" + feedback if feedback else "")
            + "\n\nProduce the TechnicalDesign artifact."
        )
