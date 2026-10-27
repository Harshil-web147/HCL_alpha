"""Automated test execution (FR-08) with acceptance-criteria mapping."""

from __future__ import annotations

import re
import time
from pathlib import Path

from defusedxml import ElementTree as ET  # junit.xml is produced inside the untrusted workspace

from app.models import Finding, Severity, TestResult, ToolRun
from app.tools.workspace import Workspace

_AC_DOC_RE = re.compile(r"def\s+(test_\w+)\s*\([^)]*\)\s*(?:->[^:]+)?:\s*\n\s+(?:\"\"\"|''')\s*AC:\s*([A-Z0-9,\- ]+)", re.MULTILINE)


def acceptance_mapping_from_source(ws: Workspace) -> dict[str, list[str]]:
    """Fallback mapping: parse `AC: AC-01, AC-02` docstring markers in the test files."""
    mapping: dict[str, list[str]] = {}
    tests_dir = ws.path / "tests"
    if not tests_dir.exists():
        return mapping
    for path in tests_dir.rglob("test_*.py"):
        for name, ids in _AC_DOC_RE.findall(path.read_text(encoding="utf-8")):
            mapping[name] = [i.strip() for i in ids.split(",") if i.strip()]
    return mapping


def _parse_junit(path: Path) -> list[TestResult]:
    results: list[TestResult] = []
    if not path.exists():
        return results
    root = ET.parse(path).getroot()
    for case in root.iter("testcase"):
        name = case.get("name", "")
        duration = float(case.get("time", "0") or 0)
        outcome = "passed"
        message = ""
        for child in case:
            if child.tag == "failure":
                outcome, message = "failed", (child.get("message") or child.text or "")[:1500]
            elif child.tag == "error":
                outcome, message = "error", (child.get("message") or child.text or "")[:1500]
            elif child.tag == "skipped":
                outcome, message = "skipped", (child.get("message") or "")[:300]
        results.append(TestResult(name=name, outcome=outcome, duration=duration, message=message))  # type: ignore[arg-type]
    return results


def run_pytest(ws: Workspace, ac_mapping: dict[str, list[str]] | None = None, timeout: int = 180) -> ToolRun:
    started = time.perf_counter()
    junit = ws.path / ".sdlc" / "junit.xml"
    if junit.exists():
        junit.unlink()
    result = ws.python(["-m", "pytest", "-q", "--junitxml", str(junit), "tests"], timeout=timeout)
    tests = _parse_junit(junit)
    mapping = dict(acceptance_mapping_from_source(ws))
    mapping.update(ac_mapping or {})
    for t in tests:
        t.acceptance_criteria_ids = mapping.get(t.name, [])

    passed = sum(1 for t in tests if t.outcome == "passed")
    failed = [t for t in tests if t.outcome in ("failed", "error")]
    findings = [
        Finding(
            id=f"TEST-{i:02d}",
            severity=Severity.HIGH,
            component="tests",
            finding=f"Test {t.name} {t.outcome}",
            evidence=t.message[-800:] or result.output[-800:],
            recommended_fix="Fix the implementation (or the test if the test contradicts the acceptance criteria)",
            source="pytest",
            category="test_failure",
            file="tests",
        )
        for i, t in enumerate(failed, start=1)
    ]
    if not tests:
        status = "error"
        summary = "pytest produced no test results (collection error?)"
        findings.append(
            Finding(
                id="TEST-00",
                severity=Severity.CRITICAL,
                component="tests",
                finding="Test collection failed",
                evidence=result.output[-1500:],
                recommended_fix="Fix the import/collection error in the test suite",
                source="pytest",
                category="test_collection",
            )
        )
    else:
        status = "passed" if not failed and result.returncode == 0 else "failed"
        summary = f"{passed} passed, {len(failed)} failed/errored, {sum(1 for t in tests if t.outcome == 'skipped')} skipped"
    return ToolRun(
        tool="pytest",
        status=status,  # type: ignore[arg-type]
        summary=summary,
        command=result.command,
        raw_output=result.output,
        duration_seconds=round(time.perf_counter() - started, 3),
        findings=findings,
        test_results=tests,
        metrics={"passed": passed, "failed": len(failed), "total": len(tests)},
    )
