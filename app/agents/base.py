"""Base class shared by all specialised agents.

An agent is a *role prompt* + *schema*.  It never touches the workspace or
runs tools itself - it only turns workflow state into a typed artifact.  The
orchestrator decides what happens with that artifact (deterministic validation,
human approval, persistence), which keeps the "AI generates, tools validate,
humans govern" principle enforceable in code.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel

from app.llm.client import LLMClient
from app.models import WorkflowState

T = TypeVar("T", bound=BaseModel)

COMMON_RULES = """
General rules for every agent in this SDLC copilot:
- You are one specialised agent in a governed software-delivery workflow. Engineering tools will
  validate what you produce and a human will approve or reject it; nothing you output is treated
  as production-ready automatically.
- Never silently convert an assumption into a requirement. State assumptions explicitly.
- Be concrete: reference requirement ids (FR-xx / NFR-xx), acceptance-criteria ids (AC-xx), file
  paths and line numbers wherever they apply so the traceability matrix can be built.
- Output must be a single JSON object that validates against the schema you are given. No prose.
"""


def dumps(obj: Any) -> str:
    if isinstance(obj, BaseModel):
        return obj.model_dump_json(indent=2)
    return json.dumps(obj, indent=2, default=str)


class Agent(Generic[T]):
    name: ClassVar[str] = "agent"
    role: ClassVar[str] = ""
    schema: ClassVar[type[BaseModel]]

    def __init__(self, llm: LLMClient):
        self.llm = llm

    # -- hooks ---------------------------------------------------------------
    def system_prompt(self) -> str:
        return f"You are the {self.role}.\n{COMMON_RULES}\n{self.instructions()}"

    def instructions(self) -> str:
        raise NotImplementedError

    def user_prompt(self, state: WorkflowState, **kwargs: Any) -> str:
        raise NotImplementedError

    # -- execution -----------------------------------------------------------
    def run(self, state: WorkflowState, **kwargs: Any) -> T:
        return self.llm.generate(  # type: ignore[return-value]
            agent=self.name,
            system=self.system_prompt(),
            user=self.user_prompt(state, **kwargs),
            schema=self.schema,
        )


def feature_block(state: WorkflowState) -> str:
    r = state.request
    return f"FEATURE REQUEST\nTitle: {r.title}\nDescription: {r.description}\nContext: {r.context or '(none)'}\n"


def code_block(state: WorkflowState, include_tests: bool = True) -> str:
    """Render the current workspace files (as recorded in state) for prompts."""
    parts = []
    if state.code_bundle:
        for f in state.code_bundle.files:
            parts.append(f"### FILE: {f.path}\n{f.content}\n")
    if include_tests and state.test_bundle:
        for f in state.test_bundle.files:
            parts.append(f"### FILE: {f.path}\n{f.content}\n")
    return "\n".join(parts)
