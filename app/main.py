"""FastAPI application: REST API for the SDLC copilot + the observability dashboard."""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.config import get_settings
from app.models import ApprovalRequest, FeatureRequest, Stage, WorkflowState
from app.orchestration.engine import WorkflowEngine, WorkflowError
from app.persistence import Store
from app.tools.product import LaunchError, ProductLauncher, build_handoff

DASHBOARD_HTML = Path(__file__).parent / "dashboard" / "index.html"


class IntakeRequest(FeatureRequest):
    auto_run: bool = True


def create_app(engine: WorkflowEngine | None = None) -> FastAPI:
    settings = get_settings()
    eng = engine or WorkflowEngine(Store(settings.db_path), settings)
    store = eng.store
    launcher = ProductLauncher(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        launcher.stop_all()

    app = FastAPI(
        lifespan=lifespan,
        title="AI-Powered SDLC Engineering Copilot",
        version="0.1.0",
        description="Requirement -> GenAI analysis -> design -> code -> deterministic validation -> testing -> security -> AI review -> correction -> revalidation -> human approval -> release recommendation.",
    )
    app.state.engine = eng
    app.state.store = store
    app.state.launcher = launcher

    def _start_run(feature_id: str) -> None:
        threading.Thread(target=eng.run, args=(feature_id,), name=f"run-{feature_id}", daemon=True).start()

    def _load(feature_id: str) -> WorkflowState:
        try:
            return eng.load(feature_id)
        except WorkflowError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # ------------------------------------------------------------------ UI
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def dashboard() -> str:
        return DASHBOARD_HTML.read_text(encoding="utf-8")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "llm_provider": settings.llm_provider}

    # ----------------------------------------------------------------- intake
    @app.post("/api/features", status_code=201)
    def create_feature(payload: IntakeRequest, background: BackgroundTasks, wait: bool = Query(False)) -> dict[str, Any]:
        state = eng.create(FeatureRequest(**payload.model_dump(exclude={"auto_run"})))
        if payload.auto_run and wait:
            state = eng.run(state.feature_id)
        elif payload.auto_run:
            _start_run(state.feature_id)
        return {"feature_id": state.feature_id, "stage": state.stage.value}

    @app.post("/api/features/{feature_id}/run")
    def run_feature(feature_id: str, wait: bool = Query(False)) -> dict[str, Any]:
        state = _load(feature_id)
        if state.is_terminal():
            raise HTTPException(status_code=409, detail=f"feature is {state.stage.value}")
        if state.is_paused():
            raise HTTPException(status_code=409, detail=f"feature is paused at {state.stage.value}; record an approval first")
        if wait:
            state = eng.run(feature_id)
        else:
            _start_run(feature_id)
        return {"feature_id": feature_id, "stage": state.stage.value}

    @app.post("/api/features/{feature_id}/approvals/{checkpoint}")
    def record_approval(feature_id: str, checkpoint: str, payload: ApprovalRequest, wait: bool = Query(False)) -> dict[str, Any]:
        _load(feature_id)
        try:
            state = eng.approve(feature_id, checkpoint, payload)
        except WorkflowError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if state.stage == Stage.COMPLETED and settings.auto_launch_product:
            threading.Thread(target=_launch_quietly, args=(state,), name=f"launch-{feature_id}", daemon=True).start()
        if not state.is_terminal() and not state.is_paused():
            if wait:
                state = eng.run(feature_id)
            else:
                _start_run(feature_id)
        return {"feature_id": feature_id, "stage": state.stage.value, "approvals": [a.model_dump() for a in state.approvals]}

    # ---------------------------------------------------------------- product
    def _launch_quietly(state: WorkflowState) -> None:
        try:
            launcher.launch(state)
        except LaunchError as exc:
            store.add_event(state.feature_id, "error", "product_launcher", f"auto-launch failed: {exc}"[:500])

    @app.get("/api/features/{feature_id}/product")
    def get_product(feature_id: str) -> dict[str, Any]:
        state = _load(feature_id)
        return build_handoff(state, settings, launcher.status(feature_id))

    @app.post("/api/features/{feature_id}/product/launch")
    def launch_product(feature_id: str) -> dict[str, Any]:
        state = _load(feature_id)
        try:
            product = launcher.launch(state)
        except LaunchError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return build_handoff(state, settings, product)

    @app.post("/api/features/{feature_id}/product/stop")
    def stop_product(feature_id: str) -> dict[str, Any]:
        state = _load(feature_id)
        launcher.stop(feature_id)
        return build_handoff(state, settings, None)

    # ---------------------------------------------------------------- queries
    @app.get("/api/features")
    def list_features() -> list[dict[str, Any]]:
        return [f.model_dump() for f in store.list_features()]

    @app.get("/api/features/{feature_id}")
    def get_feature(feature_id: str) -> dict[str, Any]:
        state = _load(feature_id)
        data = state.model_dump()
        # trim bulky content for the overview; artifacts endpoint returns everything
        for bundle in ("code_bundle", "test_bundle"):
            if data.get(bundle):
                for f in data[bundle]["files"]:
                    f["size"] = len(f.pop("content") or "")
        for run in data["tool_runs"]:
            run["raw_output"] = run["raw_output"][-1200:]
        data["paused"] = state.is_paused()
        data["terminal"] = state.is_terminal()
        data["llm_usage"] = _usage_with_cost(store.llm_usage(feature_id))
        return data

    def _usage_with_cost(u: dict[str, Any]) -> dict[str, Any]:
        s = settings
        u = {**u, "model": s.openai_model if s.llm_provider == "openai" else s.anthropic_model if s.llm_provider == "anthropic" else s.llm_provider,
             "budget": s.llm_token_budget_per_feature, "effort_code": s.llm_effort_code, "effort_default": s.llm_effort_default}
        if None in (s.llm_price_input_per_1m, s.llm_price_output_per_1m):
            u["cost_usd"] = None
            return u
        cached_price = s.llm_price_cached_input_per_1m if s.llm_price_cached_input_per_1m is not None else s.llm_price_input_per_1m
        fresh = u["input_tokens"] - u["cached_input_tokens"]
        u["cost_usd"] = round((fresh * s.llm_price_input_per_1m + u["cached_input_tokens"] * cached_price + u["output_tokens"] * s.llm_price_output_per_1m) / 1e6, 4)
        return u

    @app.get("/api/features/{feature_id}/artifacts/{name}")
    def get_artifact(feature_id: str, name: str) -> Any:
        state = _load(feature_id)
        allowed = {
            "requirement_analysis", "technical_design", "code_bundle", "test_bundle", "review_report",
            "security_report", "remediation_proposal", "release_assessment", "traceability",
            "tool_runs", "gate_results", "findings", "modifications", "approvals", "transitions", "snapshots",
        }
        if name not in allowed:
            raise HTTPException(status_code=404, detail=f"unknown artifact {name}")
        value = getattr(state, name)
        if value is None:
            return JSONResponse(status_code=404, content={"detail": f"{name} not produced yet"})
        if isinstance(value, list):
            return [v.model_dump() if isinstance(v, BaseModel) else v for v in value]
        return value.model_dump()

    @app.get("/api/features/{feature_id}/events")
    def get_events(feature_id: str, limit: int = Query(500, le=2000)) -> list[dict[str, Any]]:
        _load(feature_id)
        return store.events(feature_id, limit)

    @app.get("/api/features/{feature_id}/traceability")
    def get_traceability(feature_id: str) -> dict[str, Any]:
        state = _load(feature_id)
        if not state.traceability:
            raise HTTPException(status_code=404, detail="traceability not computed yet")
        return state.traceability.model_dump()

    @app.get("/api/features/{feature_id}/workspace")
    def get_workspace(feature_id: str, path: str | None = None) -> Any:
        state = _load(feature_id)
        from app.tools.workspace import Workspace, WorkspaceError

        ws = Workspace(settings.workspace_root, state.feature_id)
        if path:
            try:
                return {"path": path, "content": ws.read_file(path)}
            except (WorkspaceError, FileNotFoundError) as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"files": ws.list_files()}

    @app.delete("/api/features/{feature_id}", status_code=204)
    def delete_feature(feature_id: str) -> None:
        _load(feature_id)
        launcher.stop(feature_id)
        store.delete_feature(feature_id)

    # -------------------------------------------------------------- dashboard
    @app.get("/api/dashboard/summary")
    def dashboard_summary() -> dict[str, Any]:
        return store.dashboard_summary()

    @app.get("/api/gates")
    def gates_config() -> dict[str, Any]:
        return eng.gates.cfg.raw

    @app.get("/api/stages")
    def stages() -> list[str]:
        return [s.value for s in Stage]

    return app


app = create_app()
