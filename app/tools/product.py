"""Product handoff: run the generated service and tell the user what they got.

When a workflow finishes, the developer should not have to dig through the
workspace to find out whether a working product came out of it.  The
``ProductLauncher`` starts the generated FastAPI app from its isolated
workspace on a free local port, and ``build_handoff`` explains in plain terms
what was built, how it was verified, how to try it and what its limits are.
Subprocess use is intentional: fixed argv list, shell=False, cwd pinned to the
workspace (same rules as every other workspace tool).
"""

from __future__ import annotations

import json
import re
import secrets
import shlex
import socket
import subprocess  # nosec B404
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import Settings
from app.models import Stage, WorkflowState
from app.tools.workspace import Workspace

LAUNCHABLE_STAGES = {Stage.AWAITING_RELEASE_APPROVAL, Stage.COMPLETED}
# env vars the code cannot start without: _require_env("X"), os.environ["X"], os.getenv("X") with no default
_REQUIRED_ENV = re.compile(
    r"""_require_env\(\s*["']([A-Z][A-Z0-9_]*)["']"""
    r"""|os\.environ\[\s*["']([A-Z][A-Z0-9_]*)["']\s*\]"""
    r"""|(?:os\.environ\.get|os\.getenv)\(\s*["']([A-Z][A-Z0-9_]*)["']\s*\)"""
)
_SECRET_NAME = re.compile(r"SECRET|KEY|TOKEN|PASSWORD")


class LaunchError(RuntimeError):
    pass


@dataclass
class RunningProduct:
    process: subprocess.Popen
    port: int
    url: str
    started_at: float
    generated_env: list[str] = field(default_factory=list)
    demo_tokens: dict[str, str] = field(default_factory=dict)


class ProductLauncher:
    """Keeps at most one running instance of each feature's generated app."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._running: dict[str, RunningProduct] = {}
        self._guard = threading.Lock()

    def status(self, feature_id: str) -> RunningProduct | None:
        with self._guard:
            product = self._running.get(feature_id)
            if product and product.process.poll() is not None:
                del self._running[feature_id]
                return None
            return product

    def launch(self, state: WorkflowState) -> RunningProduct:
        if state.stage not in LAUNCHABLE_STAGES:
            raise LaunchError(f"the product can be launched once the workflow reaches release approval (now: {state.stage.value})")
        existing = self.status(state.feature_id)
        if existing:
            return existing
        ws = Workspace(self.settings.workspace_root, state.feature_id)
        if not ws.exists("app/main.py"):
            raise LaunchError("no generated app/main.py in the workspace")
        bundle = state.code_bundle
        env, generated = _demo_env(ws, bundle.demo_env if bundle else {})
        port = _free_port(self.settings.product_host)
        log = ws.path / ".sdlc" / "product.log"
        args = [sys.executable, "-m", "uvicorn", "app.main:app", "--host", self.settings.product_host, "--port", str(port)]
        with log.open("w", encoding="utf-8") as out:
            process = subprocess.Popen(args, cwd=ws.path, env=ws.env(env), stdout=out, stderr=subprocess.STDOUT)  # nosec B603
        host = self.settings.product_host
        url = f"http://{'localhost' if host.startswith(('127.', '0.')) else host}:{port}"
        if not _wait_until_up(f"http://127.0.0.1:{port}/openapi.json", process, timeout=20):
            process.kill()
            tail = log.read_text(encoding="utf-8", errors="replace")[-1500:]
            raise LaunchError(f"the generated app did not start:\n{tail}")
        tokens = (bundle.demo_tokens if bundle else {}) or _demo_tokens(ws, env)
        product = RunningProduct(process, port, url, time.time(), generated, tokens)
        with self._guard:
            self._running[state.feature_id] = product
        return product

    def stop(self, feature_id: str) -> bool:
        with self._guard:
            product = self._running.pop(feature_id, None)
        if not product:
            return False
        product.process.terminate()
        try:
            product.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            product.process.kill()
        return True

    def stop_all(self) -> None:
        for feature_id in list(self._running):
            self.stop(feature_id)


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _wait_until_up(url: str, process: subprocess.Popen, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=1) as r:  # nosec B310
                if r.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.25)
    return False


def _required_secrets(source: str) -> set[str]:
    return {name for match in _REQUIRED_ENV.findall(source) for name in match if name and _SECRET_NAME.search(name)}


def _demo_env(ws: Workspace, declared: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """The developer agent's declared demo settings, plus a random value for any required secret it left out."""
    source = "\n".join(ws.read_file(rel) for rel in ws.list_files() if rel.endswith(".py") and not rel.startswith("tests"))
    generated = sorted(_required_secrets(source) - set(declared))
    return {**{name: secrets.token_urlsafe(32) for name in generated}, **declared}, generated


def _demo_tokens(ws: Workspace, env: dict[str, str]) -> dict[str, str]:
    """Call the generated app's ``demo_tokens()`` wherever it lives (app/auth.py, app/core/auth.py, ...)."""
    module = next(
        (rel[:-3].replace("/", ".") for rel in ws.list_files()
         if rel.startswith("app/") and rel.endswith(".py") and "def demo_tokens" in ws.read_file(rel)),
        None,
    )
    if module is None:
        return {}
    result = ws.python(["-c", f"import json; from {module} import demo_tokens; print(json.dumps(demo_tokens()))"], timeout=15, env=env)
    try:
        tokens = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {}
    return {str(k): str(v) for k, v in tokens.items()} if isinstance(tokens, dict) else {}


# --------------------------------------------------------------------------- #
# What the user is told at the end of a run
# --------------------------------------------------------------------------- #
def build_handoff(state: WorkflowState, settings: Settings, product: RunningProduct | None) -> dict[str, Any]:
    workspace = (settings.workspace_root / state.feature_id).resolve()
    design = state.technical_design
    pytest_run = state.latest_tool_runs().get("pytest")
    gates = state.latest_gate_results()
    applied_fixes = sorted({m.file for m in state.modifications if m.applied_at})
    declared_env = state.code_bundle.demo_env if state.code_bundle else {}
    secret_vars = sorted(_required_secrets(_read_all(workspace)) - set(declared_env))
    decision = state.release_assessment.decision.value if state.release_assessment else None
    blocking = [f for f in state.open_findings() if f.severity.value in ("CRITICAL", "HIGH")]

    if product:
        headline = f"Your app is running at {product.url} - open {product.url}/docs to try every endpoint in the browser."
    elif state.stage in LAUNCHABLE_STAGES and decision == "NOT_READY":
        headline = "Your app is built and you can try it, but it is NOT ready to release - see the open issues below. Click \"Launch app\" to start it."
    elif state.stage in LAUNCHABLE_STAGES:
        headline = "Your app is built and verified. Click \"Launch app\" to start it and get a link you can open."
    else:
        headline = "The app is not finished yet - the link appears here once the workflow reaches release approval."

    verified = []
    if pytest_run:
        m = pytest_run.metrics
        verified.append(f"{m.get('passed', 0)} of {m.get('total', m.get('passed', 0) + m.get('failed', 0))} automated tests passed")
    if gates:
        verified.append(f"{sum(g.passed for g in gates.values())} of {len(gates)} quality gates passed ({', '.join(g for g, r in gates.items() if not r.passed) or 'none failed'})")
    if applied_fixes:
        verified.append(f"AI fixes were applied after your approval to: {', '.join(applied_fixes)}")
    if state.release_assessment:
        verified.append(f"release recommendation: {state.release_assessment.decision.value}")

    how_to_try = []
    if product:
        how_to_try.append(f"Open {product.url}/docs - each endpoint has a \"Try it out\" button.")
        if product.demo_tokens:
            how_to_try.append("Click \"Authorize\" (top right) and paste one of the demo tokens below, then call the endpoints as that user.")
        how_to_try.append(f"Health check: {product.url}/health")

    exports = [f"export {n}={shlex.quote(v)};" for n, v in declared_env.items()]
    exports += [f"export {n}=<any-long-random-string>;" for n in secret_vars]
    limitations = [f"Unresolved {f.severity.value} finding {f.id}: {f.finding}" for f in blocking]
    limitations += [
        "This is a working prototype, not a production deployment: it runs only on this computer and only while the dashboard server is running.",
        "Storage is whatever the generated design chose (for the reference example: in memory, reset on every restart).",
        "Demo settings and tokens are made-up values for local testing; a real deployment needs proper secret management.",
    ]
    if settings.llm_provider == "stub":
        limitations.insert(
            0,
            "Offline demo mode (LLM_PROVIDER=stub): the code comes from the built-in reference example, so it is the same Customer Contact API whatever you typed. "
            "Set LLM_PROVIDER=anthropic (with ANTHROPIC_API_KEY) or openai to build what your description asks for.",
        )
    if state.release_assessment and state.release_assessment.conditions:
        limitations.extend(f"Release condition: {c}" for c in state.release_assessment.conditions)

    return {
        "status": "running" if product else ("ready" if state.stage in LAUNCHABLE_STAGES else "not_ready"),
        "headline": headline,
        "url": product.url if product else None,
        "docs_url": f"{product.url}/docs" if product else None,
        "what_was_built": {
            "summary": design.overview if design else state.request.title,
            "kind": "A backend REST API (no visual UI) - you use it through its interactive /docs page or any HTTP client.",
            "endpoints": [{"method": a.method, "path": a.path, "description": a.description} for a in (design.api if design else [])],
        },
        "what_was_verified": verified,
        "how_to_try": how_to_try,
        "demo_tokens": product.demo_tokens if product else (state.code_bundle.demo_tokens if state.code_bundle else {}),
        "where_the_code_is": str(workspace),
        "run_it_yourself": " ".join([f"cd {shlex.quote(str(workspace))} &&", *exports, sys.executable, "-m uvicorn app.main:app --port 8001"]),
        "limitations": limitations,
    }


def _read_all(workspace: Path) -> str:
    app_dir = workspace / "app"
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in sorted(app_dir.rglob("*.py"))) if app_dir.exists() else ""
