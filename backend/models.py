"""API input contracts and durable SQLite records."""

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def now() -> str:
    return datetime.now(UTC).isoformat()


class RunInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    requirement: str = Field(min_length=15, max_length=12000)
    mode: Literal["demo", "llm"] = "demo"


class ApprovalInput(BaseModel):
    kind: Literal["design", "fix", "release"]
    version: int = Field(ge=1)
    answers: str = Field(default="", max_length=12000)


class Store:
    """Persist run snapshots and queryable evidence atomically."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.getenv("DATABASE_PATH", "data/forge.sqlite3")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                  id TEXT PRIMARY KEY, requirement TEXT NOT NULL, current_stage TEXT NOT NULL,
                  status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                  payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS records (
                  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                  kind TEXT NOT NULL, position INTEGER NOT NULL, payload TEXT NOT NULL,
                  PRIMARY KEY (run_id, kind, position));
                CREATE INDEX IF NOT EXISTS records_kind ON records(run_id, kind);
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, run: dict) -> None:
        run["updated_at"] = now()
        with self.connect() as db:
            db.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "current_stage=excluded.current_stage,status=excluded.status,"
                "updated_at=excluded.updated_at,payload=excluded.payload",
                (
                    run["id"],
                    run["requirement"],
                    run["current_stage"],
                    run["status"],
                    run["created_at"],
                    run["updated_at"],
                    json.dumps(run),
                ),
            )
            for kind in ("artifacts", "checks", "findings", "approvals", "events"):
                db.execute("DELETE FROM records WHERE run_id=? AND kind=?", (run["id"], kind))
                db.executemany(
                    "INSERT INTO records VALUES (?,?,?,?)",
                    [
                        (run["id"], kind, i, json.dumps(item))
                        for i, item in enumerate(run.get(kind, []))
                    ],
                )

    def get(self, run_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM runs ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]
