"""Deterministic code-quality tools (FR-10): Ruff + AST-based structural checks."""

from __future__ import annotations

import ast
import json
import shutil
import sys
import time

from app.models import Finding, Severity, ToolRun
from app.tools.workspace import Workspace

DEFAULT_CRITICAL_PREFIXES = ("E9", "F63", "F7", "F82")


def _ruff_severity(code: str, critical_prefixes: tuple[str, ...]) -> Severity:
    if code.startswith(critical_prefixes):
        return Severity.HIGH
    if code.startswith(("B", "S")):
        return Severity.MEDIUM
    return Severity.LOW


def run_ruff(ws: Workspace, critical_prefixes: tuple[str, ...] = DEFAULT_CRITICAL_PREFIXES, timeout: int = 120) -> ToolRun:
    started = time.perf_counter()
    ruff = shutil.which("ruff")
    base = [ruff] if ruff else [sys.executable, "-m", "ruff"]
    # --isolated: ignore any pyproject/ruff.toml the workspace may inherit from parent directories,
    # so the rule set is fixed by the platform and identical for every feature.
    args = [*base, "check", "--isolated", "--select", "E,F,W,B,I", "--ignore", "E501", "--exclude", ".sdlc,.cache,__pycache__", "--output-format", "json", "--no-cache", "."]
    result = ws.run(args, timeout=timeout)
    try:
        items = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return ToolRun(
            tool="ruff",
            status="error",
            summary="ruff did not return JSON",
            command=result.command,
            raw_output=result.output,
            duration_seconds=round(time.perf_counter() - started, 3),
        )
    findings = []
    for i, item in enumerate(items, start=1):
        code = item.get("code") or "RUFF"
        filename = item.get("filename", "")
        rel = filename.replace(str(ws.path) + "/", "")
        line = (item.get("location") or {}).get("row")
        findings.append(
            Finding(
                id=f"RUFF-{i:02d}",
                severity=_ruff_severity(code, critical_prefixes),
                component=rel.split("/")[0] if rel else "code",
                finding=f"{code}: {item.get('message', '')}",
                evidence=f"{rel}:{line}",
                recommended_fix=(item.get("fix") or {}).get("message") or f"Resolve ruff rule {code}",
                source="ruff",
                category=code,
                file=rel,
                line=line,
            )
        )
    critical = [f for f in findings if f.severity == Severity.HIGH]
    return ToolRun(
        tool="ruff",
        status="failed" if critical else "passed",
        summary=f"{len(findings)} violation(s), {len(critical)} critical",
        command=result.command,
        raw_output=result.output[:4000],
        duration_seconds=round(time.perf_counter() - started, 3),
        findings=findings,
        metrics={"violations": len(findings), "critical": len(critical)},
    )


# --------------------------------------------------------------------------- AST
class _Visitor(ast.NodeVisitor):
    def __init__(self, rel: str):
        self.rel = rel
        self.findings: list[tuple[Severity, str, str, int, str]] = []
        self.function_count = 0
        self.max_complexity = 0

    def _complexity(self, node: ast.AST) -> int:
        score = 1
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.For, ast.While, ast.ExceptHandler, ast.With, ast.IfExp, ast.comprehension)):
                score += 1
            elif isinstance(child, ast.BoolOp):
                score += len(child.values) - 1
        return score

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.function_count += 1
        length = (node.end_lineno or node.lineno) - node.lineno + 1
        complexity = self._complexity(node)
        self.max_complexity = max(self.max_complexity, complexity)
        if length > 60:
            self.findings.append((Severity.LOW, "maintainability", f"Function {node.name} is {length} lines long", node.lineno, "Split into smaller functions"))
        if complexity > 12:
            self.findings.append((Severity.MEDIUM, "maintainability", f"Function {node.name} has cyclomatic complexity {complexity}", node.lineno, "Reduce branching / extract helpers"))
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:  # noqa: N802
        if node.type is None:
            self.findings.append((Severity.MEDIUM, "exception_handling", "Bare except: swallows all exceptions", node.lineno, "Catch specific exception types"))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
        if name in {"eval", "exec"}:
            self.findings.append((Severity.HIGH, "dangerous_call", f"Use of {name}()", node.lineno, "Remove dynamic code execution"))
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:  # noqa: N802
        if not self.rel.startswith("tests"):
            self.findings.append((Severity.LOW, "control_flow", "assert used in application code (stripped with -O)", node.lineno, "Raise an explicit exception"))
        self.generic_visit(node)


def run_ast_analysis(ws: Workspace) -> ToolRun:
    started = time.perf_counter()
    findings: list[Finding] = []
    files = [f for f in ws.list_files() if f.endswith(".py") and f.startswith("app")]
    function_total = 0
    max_complexity = 0
    counter = 0
    for rel in files:
        try:
            tree = ast.parse(ws.read_file(rel))
        except SyntaxError as exc:
            counter += 1
            findings.append(Finding(id=f"AST-{counter:02d}", severity=Severity.HIGH, component=rel, finding=f"Syntax error: {exc.msg}", evidence=f"{rel}:{exc.lineno}", recommended_fix="Fix syntax", source="ast", category="syntax", file=rel, line=exc.lineno))
            continue
        visitor = _Visitor(rel)
        visitor.visit(tree)
        function_total += visitor.function_count
        max_complexity = max(max_complexity, visitor.max_complexity)
        for severity, category, text, line, fix in visitor.findings:
            counter += 1
            findings.append(Finding(id=f"AST-{counter:02d}", severity=severity, component=rel, finding=text, evidence=f"{rel}:{line}", recommended_fix=fix, source="ast", category=category, file=rel, line=line))
    blocking = [f for f in findings if f.severity in (Severity.HIGH, Severity.CRITICAL)]
    return ToolRun(
        tool="ast",
        status="failed" if blocking else "passed",
        summary=f"{len(files)} files, {function_total} functions, max complexity {max_complexity}, {len(findings)} structural finding(s)",
        command="ast.parse(app/**/*.py)",
        duration_seconds=round(time.perf_counter() - started, 3),
        findings=findings,
        metrics={"files": len(files), "functions": function_total, "max_complexity": max_complexity},
    )
