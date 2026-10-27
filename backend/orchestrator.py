"""Durable, approval-gated orchestration of small FastAPI projects."""

import ast
import asyncio
import re
import uuid

from backend.agents import analyst, designer, developer, fixer, release, reviewer, security, tester
from backend.models import Store, now
from backend.services import gates, runner, workspace


class WorkflowError(ValueError):
    pass


class Orchestrator:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.locks: dict[str, asyncio.Lock] = {}

    def create(self, requirement: str, mode: str) -> dict:
        run = {
            "id": str(uuid.uuid4()),
            "title": requirement.strip().split("\n")[0][:70],
            "requirement": requirement,
            "mode": mode,
            "current_stage": "analyzing",
            "status": "running",
            "version": 1,
            "correction_round": 0,
            "created_at": now(),
            "artifacts": [],
            "checks": [],
            "findings": [],
            "approvals": [],
            "events": [],
            "traceability": [],
            "report": None,
        }
        self.event(run, "Orchestrator", "Feature received. Requirement analysis queued.")
        return run

    def event(self, run: dict, agent: str, message: str) -> None:
        run["events"].append(
            {"id": str(uuid.uuid4()), "agent": agent, "message": message, "created_at": now()}
        )
        self.store.save(run)

    def artifact(self, run: dict, kind: str, content: dict) -> None:
        run["artifacts"].append(
            {"id": str(uuid.uuid4()), "kind": kind, "version": run["version"], "content": content}
        )
        self.store.save(run)

    @staticmethod
    def latest(run: dict, kind: str) -> dict:
        return next(a["content"] for a in reversed(run["artifacts"]) if a["kind"] == kind)

    async def call(self, run: dict, name: str, fn, *args) -> dict:
        self.event(run, name, f"Started ({run['mode']} mode).")
        result = await fn(*args, run["mode"])
        self.artifact(run, name, result)
        self.event(run, name, "Structured output validated and recorded.")
        return result

    async def advance(self, run_id: str) -> None:
        async with self.locks.setdefault(run_id, asyncio.Lock()):
            run = self.store.get(run_id)
            if not run:
                return
            try:
                stage = run["current_stage"]
                if stage == "analyzing":
                    analysis = await self.call(run, "analyst", analyst.run, run["requirement"])
                    await self.call(run, "designer", designer.run, analysis)
                    run.update(current_stage="design_approval", status="waiting_approval")
                elif stage == "generating":
                    analysis, design = self.latest(run, "analyst"), self.latest(run, "designer")
                    code = await self.call(run, "developer", developer.run, analysis, design)
                    tests = await self.call(
                        run, "tester", tester.run, analysis, design, code["files"]
                    )
                    files = self.merge_files(code["files"], tests["files"])
                    workspace.validate_files(files)
                    self.artifact(run, "files", {"files": files})
                    self.artifact(run, "mapping", {"mapping": tests["mapping"]})
                    run.update(current_stage="validating", status="running")
                    self.store.save(run)
                    await self.validate(run)
                elif stage == "validating":
                    await self.validate(run)
                self.store.save(run)
            except Exception as exc:  # noqa: BLE001 -- persist failure at the workflow boundary
                run.update(current_stage="error", status="error")
                self.event(
                    run, "Orchestrator", f"Workflow stopped: {type(exc).__name__}: {str(exc)[:800]}"
                )

    @staticmethod
    def merge_files(code: list[dict], tests: list[dict]) -> list[dict]:
        merged = {f["path"]: f for f in code}
        for item in tests:
            if item["path"] in merged:
                raise WorkflowError("Test agent attempted to overwrite application code")
            merged[item["path"]] = item
        return list(merged.values())

    async def validate(self, run: dict) -> None:
        files = self.latest(run, "files")["files"]
        path = workspace.write_project(run["id"], run["version"], files)
        self.event(run, "Engineering tools", "Executing isolated checks; network disabled.")
        checks = await asyncio.to_thread(runner.run_checks, path, run["version"])
        for check in checks:
            check["id"] = str(uuid.uuid4())
        run["checks"].extend(checks)
        for finding in run["findings"]:
            if finding["version"] == run["version"]:
                finding["status"] = "superseded"
        for name, agent in (("reviewer", reviewer), ("security", security)):
            result = await self.call(run, name, agent.run, files, checks)
            for finding in result.get("findings", []):
                run["findings"].append(
                    {
                        **finding,
                        "id": str(uuid.uuid4()),
                        "status": "open",
                        "version": run["version"],
                    }
                )
        self.trace(run, checks)
        policy = gates.assess(run)
        blocking = any(g["status"] == "failed" for g in policy["gates"]) or any(
            f["status"] == "open" and f["severity"].lower() in ("high", "critical")
            for f in run["findings"]
            if f["version"] == run["version"]
        )
        missing = any(
            g["status"] == "not_executed" and g["name"] != "final approval" for g in policy["gates"]
        )
        if blocking and run["correction_round"] < 2:
            proposal = await self.call(
                run,
                "fixer",
                fixer.run,
                files,
                [f for f in run["findings"] if f["status"] == "open"],
                checks,
            )
            workspace.validate_files(proposal["files"])
            run.update(current_stage="fix_approval", status="waiting_approval")
        else:
            run.update(
                current_stage="manual_review" if blocking or missing else "release_review",
                status="waiting_approval",
            )
            await self.assess(run)
        self.store.save(run)

    def trace(self, run: dict, checks: list[dict]) -> None:
        analysis = self.latest(run, "analyst")
        executions = next((c.get("tests", {}) for c in checks if c["name"].lower() == "pytest"), {})
        mapping = self.latest(run, "mapping")["mapping"]
        files = {f["path"]: f["content"] for f in self.latest(run, "files")["files"]}
        rows = []
        for ac in analysis["acceptance_criteria"]:
            matches = [m for m in mapping if m["ac_id"] == ac["id"]]
            for m in matches or [{}]:
                req = next(
                    (
                        r["text"]
                        for r in analysis["requirements"]
                        if r["id"] == ac["requirement_id"]
                    ),
                    "",
                )
                rows.append(
                    {
                        **m,
                        "requirement_id": ac["requirement_id"],
                        "requirement": req,
                        "ac_id": ac["id"],
                        "criterion": ac["text"],
                        "implementation": m.get("implementation", "Unmapped")
                        if self.implementation_exists(m.get("implementation", ""), files)
                        else "Unmapped",
                        "test": m.get("test", "Unmapped"),
                        "execution": executions.get(m.get("test"), "NOT EXECUTED"),
                    }
                )
        run["traceability"] = rows

    @staticmethod
    def implementation_exists(reference: str, files: dict[str, str]) -> bool:
        """Verify a mapped Python path and function against the current source AST."""
        match = re.fullmatch(r"(.+?\.py)\s*(?:→|->|::)\s*(\w+)\(\)", reference)
        if not match or match[1].strip() not in files:
            return False
        try:
            tree = ast.parse(files[match[1].strip()])
        except SyntaxError:
            return False
        return any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == match[2]
            for node in ast.walk(tree)
        )

    async def assess(self, run: dict) -> None:
        run["report"] = gates.assess(run)
        explanation = await self.call(run, "release", release.run, run["report"])
        run["report"]["explanation"] = explanation["explanation"]
        self.store.save(run)

    async def approve(self, run_id: str, kind: str, version: int, answers: str) -> dict:
        async with self.locks.setdefault(run_id, asyncio.Lock()):
            run = self.store.get(run_id)
            if not run:
                raise WorkflowError("Run not found")
            expected = {
                "design": "design_approval",
                "fix": "fix_approval",
                "release": "release_review",
            }
            if run["current_stage"] != expected[kind] or run["version"] != version:
                raise WorkflowError("Approval is stale or invalid for the current stage")
            if kind == "design":
                questions = self.latest(run, "analyst").get("questions", [])
                if questions and not answers.strip():
                    raise WorkflowError("Resolve the analyst questions before approving the design")
                if questions and answers.strip():
                    analysis = await analyst.run(run["requirement"], run["mode"], answers)
                    run["version"] += 1
                    self.artifact(run, "analyst", analysis)
                    await self.call(run, "designer", designer.run, analysis)
                    self.event(
                        run,
                        "Human",
                        "Clarifications applied. Review the revised design before approval.",
                    )
                    return run
            run["approvals"].append(
                {"kind": kind, "version": version, "created_at": now(), "notes": answers.strip()}
            )
            if kind == "design":
                run.update(current_stage="generating", status="running")
            elif kind == "fix":
                proposal = self.latest(run, "fixer")
                run["version"] += 1
                run["correction_round"] += 1
                for f in run["findings"]:
                    if f["status"] == "open":
                        f["status"] = "superseded"
                run["report"] = None
                run.update(current_stage="validating", status="running")
                self.artifact(run, "files", {"files": proposal["files"]})
                self.event(
                    run,
                    "Orchestrator",
                    "Approved correction applied; previous evidence is stale. Revalidating.",
                )
            else:
                await self.assess(run)
                run.update(current_stage="completed", status="completed")
            self.event(run, "Human", f"Approved {kind} artifact version {version}.")
            self.store.save(run)
            return run
