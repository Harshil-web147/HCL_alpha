"""Build check: byte-compile the workspace and import the application module."""

from __future__ import annotations

import time

from app.models import Finding, Severity, ToolRun
from app.tools.workspace import Workspace


def run_build(ws: Workspace, timeout: int = 120, module: str = "app.main") -> ToolRun:
    started = time.perf_counter()
    compile_result = ws.python(["-m", "compileall", "-q", "app", "tests"], timeout=timeout)
    if compile_result.returncode != 0:
        return ToolRun(
            tool="build",
            status="failed",
            summary="Byte-compilation failed (syntax error)",
            command=compile_result.command,
            raw_output=compile_result.output,
            duration_seconds=round(time.perf_counter() - started, 3),
            findings=[
                Finding(
                    id="BUILD-01",
                    severity=Severity.CRITICAL,
                    component="build",
                    finding="Generated code does not compile",
                    evidence=compile_result.output[-1500:],
                    recommended_fix="Fix the syntax error reported by compileall",
                    source="build",
                    category="build",
                )
            ],
        )
    import_result = ws.python(["-c", f"import {module}"], timeout=timeout)
    ok = import_result.returncode == 0
    findings = []
    if not ok:
        findings.append(
            Finding(
                id="BUILD-02",
                severity=Severity.CRITICAL,
                component="build",
                finding=f"Application module {module} cannot be imported",
                evidence=import_result.output[-1500:],
                recommended_fix="Resolve the import error (missing module, bad relative import, module-level side effect)",
                source="build",
                category="build",
            )
        )
    return ToolRun(
        tool="build",
        status="passed" if ok else "failed",
        summary=f"compileall ok; import {module} {'ok' if ok else 'FAILED'}",
        command=f"{compile_result.command} && {import_result.command}",
        raw_output=(compile_result.output + "\n" + import_result.output).strip(),
        duration_seconds=round(time.perf_counter() - started, 3),
        findings=findings,
    )
