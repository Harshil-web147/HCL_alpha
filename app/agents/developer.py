"""Agent 3 - Developer Agent (UC-03 AI Code Generation)."""

from __future__ import annotations

from typing import Any

from app.agents.base import Agent, dumps, feature_block
from app.models import CodeBundle, WorkflowState


class DeveloperAgent(Agent[CodeBundle]):
    name = "developer"
    role = "Developer Agent"
    schema = CodeBundle

    def instructions(self) -> str:
        return """
Implement the human-approved technical design as a complete, runnable Python 3.11+ / FastAPI service.
Produce EVERY file in the design's file plan plus:
- app/__init__.py
- requirements.txt (fastapi, pydantic, httpx, pytest)
- pytest.ini containing exactly:
      [pytest]
      testpaths = tests
      pythonpath = .
      addopts = -p no:cacheprovider
Rules:
- Use relative imports inside the app package (from .auth import ...).
- Expose `app = create_app()` in app/main.py.
- Business logic in a service module, data access in a repository module (in-memory is acceptable
  when the design says so), validation via Pydantic schemas, a uniform error envelope via exception
  handlers, structured logging that never logs personal data, configuration from environment variables.
- Never hard-code secrets, never use eval/exec/shell commands, never use assert for control flow.
- Add a module docstring to each file stating "Implements: <requirement/AC ids>" and fill the
  `implements` list of each file with the same ids - this drives traceability.
- Code must pass `ruff check` and be importable with `python -c "import app.main"` without any
  environment variables set (read secrets lazily at request time).
- The code is a proposal: it will be validated by tests, static analysis, security scanners and a
  human reviewer. Do not describe it as production-ready.
"""

    def user_prompt(self, state: WorkflowState, **kwargs: Any) -> str:
        return (
            feature_block(state)
            + "\nREQUIREMENT ANALYSIS (acceptance criteria drive the behaviour)\n"
            + dumps(state.requirement_analysis)
            + "\n\nAPPROVED TECHNICAL DESIGN\n"
            + dumps(state.technical_design)
            + "\n\nProduce the CodeBundle artifact with the full content of every file."
        )
