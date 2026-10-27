"""HTTP API and built dashboard entrypoint."""

import asyncio
import io
import logging
import os
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.models import ApprovalInput, RunInput, Store
from backend.orchestrator import Orchestrator, WorkflowError
from backend.services.workspace import validate_files

store = Store()
orchestrator = Orchestrator(store)
tasks: set[asyncio.Task] = set()


def schedule(run_id: str) -> None:
    task = asyncio.create_task(orchestrator.advance(run_id))
    tasks.add(task)
    task.add_done_callback(tasks.discard)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Agent calls are repeatable; interrupted engineering checks are re-run, never inferred.
    for run in store.list():
        if run["current_stage"] in ("analyzing", "generating", "validating"):
            schedule(run["id"])
    yield
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="Forge SDLC", version="0.1.0", lifespan=lifespan, docs_url="/api/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/api/health")
def health() -> dict:
    return {
        "data": {
            "status": "ok",
            "llm_configured": bool(os.getenv("LLM_API_KEY") and os.getenv("LLM_MODEL")),
        }
    }


@app.get("/api/runs")
def list_runs() -> dict:
    return {"data": store.list()}


@app.post("/api/runs", status_code=201)
async def create_run(payload: RunInput) -> dict:
    if payload.mode == "llm" and not (os.getenv("LLM_API_KEY") and os.getenv("LLM_MODEL")):
        raise HTTPException(
            422, "Configure LLM_API_KEY and LLM_MODEL on the backend before using LLM mode."
        )
    run = orchestrator.create(payload.requirement.strip(), payload.mode)
    schedule(run["id"])
    return {"data": run}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = store.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return {"data": run}


@app.post("/api/runs/{run_id}/approve")
async def approve(run_id: str, payload: ApprovalInput) -> dict:
    try:
        run = await orchestrator.approve(run_id, payload.kind, payload.version, payload.answers)
    except WorkflowError as exc:
        raise HTTPException(409, str(exc)) from exc
    if run["current_stage"] in ("generating", "validating"):
        schedule(run_id)
    return {"data": run}


@app.post("/api/runs/{run_id}/resume")
async def resume(run_id: str) -> dict:
    run = get_run(run_id)["data"]
    if run["current_stage"] not in ("manual_review", "error"):
        raise HTTPException(409, "Only interrupted or manual-review runs can be resumed")
    has_files = any(a["kind"] == "files" for a in run["artifacts"])
    approved = any(a["kind"] == "design" for a in run["approvals"])
    run.update(
        current_stage="validating" if has_files else "generating" if approved else "analyzing",
        status="running",
        report=None,
    )
    store.save(run)
    schedule(run_id)
    return {"data": run}


@app.get("/api/runs/{run_id}/download")
def download(run_id: str) -> StreamingResponse:
    run = get_run(run_id)["data"]
    artifacts = [a for a in run["artifacts"] if a["kind"] == "files"]
    if not artifacts:
        raise HTTPException(409, "No generated project yet")
    try:
        validate_files(artifacts[-1]["content"]["files"])
    except ValueError as exc:
        raise HTTPException(409, "Generated file paths failed validation") from exc
    import json

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in artifacts[-1]["content"]["files"]:
            archive.writestr(item["path"], item["content"])
        archive.writestr("forge-evidence.json", json.dumps(run, indent=2))
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="forge-{run_id[:8]}-v{run["version"]}.zip"'
        },
    )


dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if dist.is_dir():
    app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")
else:
    logging.getLogger(__name__).info("Frontend not built; use the Vite development server.")
