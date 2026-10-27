"""Isolated per-feature development workspace.

Every feature gets its own directory under ``SDLC_WORKSPACE_ROOT``.  All
generated code, tests and tool outputs live there; every tool is executed with
that directory as ``cwd`` and a scrubbed environment, so one feature can never
see another's files and generated code can never touch the platform's own
sources.  Paths are validated so a generated file cannot escape the workspace.  Subprocess
use is intentional: fixed argv lists, shell=False, cwd pinned to the workspace.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import shutil
import subprocess  # nosec B404
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from app.models import GeneratedFile


class WorkspaceError(RuntimeError):
    pass


@dataclass
class CommandResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def output(self) -> str:
        return (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()


class Workspace:
    def __init__(self, root: Path, feature_id: str):
        self.feature_id = feature_id
        self.path = (root / feature_id).resolve()
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / ".sdlc").mkdir(exist_ok=True)

    # ------------------------------------------------------------------ files
    def _safe_path(self, rel: str) -> Path:
        if rel.startswith(("/", "\\")) or ".." in Path(rel).parts:
            raise WorkspaceError(f"Refusing to write outside the workspace: {rel}")
        target = (self.path / rel).resolve()
        if self.path not in target.parents and target != self.path:
            raise WorkspaceError(f"Refusing to write outside the workspace: {rel}")
        return target

    def write_files(self, files: list[GeneratedFile]) -> list[str]:
        written = []
        for f in files:
            target = self._safe_path(f.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f.content, encoding="utf-8")
            written.append(f.path)
        return written

    def read_file(self, rel: str) -> str:
        return self._safe_path(rel).read_text(encoding="utf-8")

    def exists(self, rel: str) -> bool:
        return self._safe_path(rel).exists()

    def list_files(self) -> list[str]:
        out = []
        for p in sorted(self.path.rglob("*")):
            rel_parts = p.relative_to(self.path).parts
            if p.is_file() and "__pycache__" not in rel_parts and not any(part.startswith(".") for part in rel_parts):
                out.append(str(p.relative_to(self.path)))
        return out

    def file_hash(self, rel: str) -> str:
        target = self._safe_path(rel)
        if not target.exists():
            return ""
        return hashlib.sha256(target.read_bytes()).hexdigest()[:16]

    def diff(self, rel: str, new_content: str) -> str:
        old = self.read_file(rel).splitlines(keepends=True) if self.exists(rel) else []
        new = new_content.splitlines(keepends=True)
        return "".join(difflib.unified_diff(old, new, fromfile=f"a/{rel}", tofile=f"b/{rel}"))

    def snapshot(self, name: str) -> Path:
        """Copy the current source tree (for before/after comparison and rollback)."""
        dest = self.path / ".sdlc" / "snapshots" / name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(self.path, dest, ignore=shutil.ignore_patterns(".sdlc", "__pycache__", ".pytest_cache"))
        return dest

    def destroy(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    # ---------------------------------------------------------------- execute
    def run(self, args: list[str], timeout: int = 180, env: dict[str, str] | None = None) -> CommandResult:
        """Run a command inside the workspace with a minimal environment."""
        base_env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.path / ".sdlc"),
            "XDG_CACHE_HOME": str(self.path / ".sdlc" / "cache"),
            "PYTHONPATH": str(self.path),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "LANG": "C.UTF-8",
        }
        for key in ("VIRTUAL_ENV", "PYTHONHOME", "HTTPS_PROXY", "HTTP_PROXY", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
            if key in os.environ:
                base_env[key] = os.environ[key]
        if env:
            base_env.update(env)
        started = time.perf_counter()
        try:
            proc = subprocess.run(  # nosec B603
                args,
                cwd=self.path,
                env=base_env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return CommandResult(
                " ".join(args), proc.returncode, proc.stdout[-20000:], proc.stderr[-20000:], time.perf_counter() - started
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                " ".join(args),
                124,
                (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
                f"timed out after {timeout}s",
                time.perf_counter() - started,
                timed_out=True,
            )

    def python(self, args: list[str], timeout: int = 180, env: dict[str, str] | None = None) -> CommandResult:
        return self.run([sys.executable, "-B", *args], timeout=timeout, env=env)
