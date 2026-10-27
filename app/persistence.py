"""SQLite persistence for workflow state and the observability event log (FR-18).

The full ``WorkflowState`` is stored as JSON (source of truth for a feature);
events are written to their own table so the dashboard can query them without
loading every feature.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from app.models import FeatureSummary, ReleaseDecision, Stage, WorkflowState, utcnow

_SCHEMA = """
CREATE TABLE IF NOT EXISTS features (
    feature_id TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    stage      TEXT NOT NULL,
    iteration  INTEGER NOT NULL DEFAULT 0,
    decision   TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    state_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_id TEXT NOT NULL,
    ts         TEXT NOT NULL,
    type       TEXT NOT NULL,
    actor      TEXT NOT NULL,
    summary    TEXT NOT NULL,
    payload    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_feature ON events(feature_id, id);
"""


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    # ---------------------------------------------------------------- features
    def save_state(self, state: WorkflowState) -> None:
        state.updated_at = utcnow()
        decision = state.release_assessment.decision.value if state.release_assessment else None
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO features (feature_id, title, stage, iteration, decision, created_at, updated_at, state_json)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(feature_id) DO UPDATE SET title=excluded.title, stage=excluded.stage,
                   iteration=excluded.iteration, decision=excluded.decision, updated_at=excluded.updated_at,
                   state_json=excluded.state_json""",
                (
                    state.feature_id,
                    state.request.title,
                    state.stage.value,
                    state.iteration,
                    decision,
                    state.created_at,
                    state.updated_at,
                    state.model_dump_json(),
                ),
            )

    def load_state(self, feature_id: str) -> WorkflowState | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT state_json FROM features WHERE feature_id=?", (feature_id,)).fetchone()
        return WorkflowState.model_validate_json(row["state_json"]) if row else None

    def list_features(self) -> list[FeatureSummary]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT state_json FROM features ORDER BY created_at DESC").fetchall()
        summaries = []
        for row in rows:
            state = WorkflowState.model_validate_json(row["state_json"])
            gates = state.latest_gate_results()
            summaries.append(
                FeatureSummary(
                    feature_id=state.feature_id,
                    title=state.request.title,
                    stage=state.stage,
                    iteration=state.iteration,
                    decision=state.release_assessment.decision if state.release_assessment else None,
                    created_at=state.created_at,
                    updated_at=state.updated_at,
                    open_findings=len(state.open_findings()),
                    gates_passed=sum(1 for g in gates.values() if g.passed),
                    gates_total=len(gates),
                )
            )
        return summaries

    def delete_feature(self, feature_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM features WHERE feature_id=?", (feature_id,))
            conn.execute("DELETE FROM events WHERE feature_id=?", (feature_id,))

    # ------------------------------------------------------------------ events
    def add_event(self, feature_id: str, type_: str, actor: str, summary: str, payload: dict[str, Any] | None = None) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO events (feature_id, ts, type, actor, summary, payload) VALUES (?,?,?,?,?,?)",
                (feature_id, utcnow(), type_, actor, summary, json.dumps(payload or {}, default=str)[:20000]),
            )

    def events(self, feature_id: str, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, ts, type, actor, summary, payload FROM events WHERE feature_id=? ORDER BY id ASC LIMIT ?",
                (feature_id, limit),
            ).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def event_counts(self) -> dict[str, int]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT type, COUNT(*) AS n FROM events GROUP BY type").fetchall()
        return {r["type"]: r["n"] for r in rows}

    # --------------------------------------------------------------- dashboard
    def dashboard_summary(self) -> dict[str, Any]:
        features = self.list_features()
        by_stage: dict[str, int] = {}
        by_decision: dict[str, int] = {d.value: 0 for d in ReleaseDecision}
        gates: dict[str, dict[str, int]] = {}
        findings_by_severity: dict[str, int] = {}
        tests = {"passed": 0, "failed": 0, "total": 0}
        coverage = []
        for summary in features:
            by_stage[summary.stage.value] = by_stage.get(summary.stage.value, 0) + 1
            if summary.decision:
                by_decision[summary.decision.value] += 1
            state = self.load_state(summary.feature_id)
            if not state:
                continue
            for name, gate in state.latest_gate_results().items():
                bucket = gates.setdefault(name, {"passed": 0, "failed": 0})
                bucket["passed" if gate.passed else "failed"] += 1
            for f in state.open_findings():
                findings_by_severity[f.severity.value] = findings_by_severity.get(f.severity.value, 0) + 1
            run = state.latest_tool_runs().get("pytest")
            if run:
                tests["passed"] += run.metrics.get("passed", 0)
                tests["failed"] += run.metrics.get("failed", 0)
                tests["total"] += run.metrics.get("total", 0)
            if state.traceability:
                coverage.append(state.traceability.requirement_coverage)
        return {
            "features_processed": len(features),
            "features_by_stage": by_stage,
            "readiness": by_decision,
            "in_progress": sum(1 for f in features if f.stage not in (Stage.COMPLETED, Stage.REJECTED, Stage.FAILED)),
            "awaiting_human": sum(1 for f in features if f.stage.value.startswith("awaiting")),
            "requirement_coverage_avg": round(sum(coverage) / len(coverage), 1) if coverage else None,
            "tests": tests,
            "open_findings_by_severity": findings_by_severity,
            "gate_status": gates,
            "events_by_type": self.event_counts(),
            "features": [f.model_dump() for f in features],
        }
