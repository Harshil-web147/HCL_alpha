"""Agent 4 - Test Engineer Agent (UC-04 Intelligent Test Generation)."""

from __future__ import annotations

from typing import Any

from app.agents.base import Agent, code_block, dumps, feature_block
from app.models import TestBundle, WorkflowState


class TestEngineerAgent(Agent[TestBundle]):
    __test__ = False  # not a pytest test class
    name = "test_engineer"
    role = "Test Engineer Agent"
    schema = TestBundle

    def instructions(self) -> str:
        return """
Generate a pytest suite for the generated FastAPI service from the requirement analysis, acceptance
criteria, technical design and the actual code. Produce:
- tests/__init__.py (empty docstring), tests/conftest.py (fixtures: TestClient, auth headers, valid
  payload, autouse state reset) and tests/test_<feature>.py.
- Cover every acceptance criterion with at least one test and include: positive, negative,
  boundary-condition, validation, authorization, exception and API-contract tests.
- Each test function has a docstring whose first line is `AC: AC-xx, AC-yy` naming the acceptance
  criteria it verifies, and the same mapping must appear in `test_cases`.
- Any environment variable the app needs must be set in conftest.py BEFORE the app is imported.
- Tests must be deterministic (no network, no sleeps, no randomness) and runnable with
  `python -m pytest` from the workspace root using the provided pytest.ini.
- Only use the public behaviour of the generated code; do not modify application files.
"""

    def user_prompt(self, state: WorkflowState, **kwargs: Any) -> str:
        return (
            feature_block(state)
            + "\nREQUIREMENT ANALYSIS\n"
            + dumps(state.requirement_analysis)
            + "\n\nTECHNICAL DESIGN (API contract)\n"
            + dumps(state.technical_design)
            + "\n\nGENERATED APPLICATION CODE\n"
            + code_block(state, include_tests=False)
            + "\n\nProduce the TestBundle artifact."
        )
