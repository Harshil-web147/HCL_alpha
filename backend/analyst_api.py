"""HTTP endpoints for the Business Analyst agent.

Standalone:  uvicorn backend.analyst_api:app --reload
Team main.py: from backend.analyst_api import router; app.include_router(router)
"""

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from backend.agents import analyst
from backend.agents.analyst_schemas import AnalysisSpec

router = APIRouter(prefix="/analyst", tags=["Business Analyst"])


class AnalyzeIn(BaseModel):
    prompt: str


class RefineIn(BaseModel):
    spec: AnalysisSpec
    answers: dict[str, str] = {}


@router.post("/analyze", response_model=AnalysisSpec)
def analyze(body: AnalyzeIn) -> AnalysisSpec:
    try:
        return analyst.analyze(body.prompt)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except analyst.AnalystError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/refine", response_model=AnalysisSpec)
def refine(body: RefineIn) -> AnalysisSpec:
    try:
        return analyst.refine(body.spec, body.answers)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except analyst.AnalystError as exc:
        raise HTTPException(502, str(exc)) from exc


app = FastAPI(title="Business Analyst Agent")
app.include_router(router)
