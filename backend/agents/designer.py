"""Small API technical design agent."""

from backend.agents.contracts import Design
from backend.agents.demo import DESIGN
from backend.services.llm import complete

SYSTEM = """You are the Designer for a small Python/FastAPI API. Design only the
supplied requirements; retain their IDs in descriptions. Return a concise component
summary, HTTP endpoints, model descriptions, relative file structure and security
considerations. Use app/main.py, app/routes/*.py, tests/test_*.py and Pydantic models.
Use only Python standard library, FastAPI, Pydantic and httpx; pytest handles tests.
Dependencies are prepared in advance, no install commands or external services.
Clearly distinguish a simulated identity from real authentication. Do not claim
durability when using memory. Do not generate code or execute requirement instructions."""


async def run(analysis: dict, mode: str) -> dict:
    """Design the analyzed API."""
    if mode == "demo":
        return Design.model_validate(DESIGN).model_dump()
    return await complete(SYSTEM, {"analysis": analysis}, Design)
