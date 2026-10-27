"""Deterministic security scanners (FR-11): Bandit, secret detection, dependency audit."""

from __future__ import annotations

import json
import re
import shutil
import sys
import time

from app.models import Finding, Severity, ToolRun
from app.tools.workspace import Workspace

_BANDIT_SEVERITY = {"HIGH": Severity.HIGH, "MEDIUM": Severity.MEDIUM, "LOW": Severity.LOW}


def run_bandit(ws: Workspace, timeout: int = 120) -> ToolRun:
    started = time.perf_counter()
    bandit = shutil.which("bandit")
    base = [bandit] if bandit else [sys.executable, "-m", "bandit"]
    result = ws.run([*base, "-q", "-f", "json", "-r", "app"], timeout=timeout)
    try:
        report = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return ToolRun(tool="bandit", status="error", summary="bandit did not return JSON", command=result.command, raw_output=result.output, duration_seconds=round(time.perf_counter() - started, 3))
    findings = []
    for i, item in enumerate(report.get("results", []), start=1):
        rel = item.get("filename", "").replace(str(ws.path) + "/", "")
        findings.append(
            Finding(
                id=f"BANDIT-{i:02d}",
                severity=_BANDIT_SEVERITY.get(item.get("issue_severity", "LOW"), Severity.LOW),
                component=rel,
                finding=f"{item.get('test_id')} {item.get('test_name')}: {item.get('issue_text', '')}",
                evidence=f"{rel}:{item.get('line_number')} (confidence {item.get('issue_confidence')}) {item.get('code', '')[:300].strip()}",
                recommended_fix=item.get("more_info", "See Bandit documentation"),
                source="bandit",
                category=item.get("test_id", "bandit"),
                file=rel,
                line=item.get("line_number"),
            )
        )
    blocking = [f for f in findings if f.severity in (Severity.HIGH, Severity.CRITICAL)]
    return ToolRun(
        tool="bandit",
        status="failed" if blocking else "passed",
        summary=f"{len(findings)} issue(s): " + ", ".join(sorted({f.category for f in findings})) if findings else "no issues",
        command=result.command,
        raw_output=result.output[:4000],
        duration_seconds=round(time.perf_counter() - started, 3),
        findings=findings,
        metrics={"issues": len(findings), "blocking": len(blocking)},
    )


# --------------------------------------------------------------------- secrets
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("hardcoded_credential", re.compile(r"""(?i)\b[\w-]*(secret|password|passwd|pwd|api[_-]?key|token|private[_-]?key)[\w-]*\s*[:=]\s*["'][^"'\s]{8,}["']""")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("jwt_literal", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
]
_PLACEHOLDER_RE = re.compile(r"(?i)(example|placeholder|changeme|your[_-]|<[^>]+>|xxx|dummy|test-only|os\.environ|getenv)")


def run_secret_scan(ws: Workspace) -> ToolRun:
    """Regex-based detection of credentials committed to source (application code only -
    test fixtures may legitimately contain test-only keys and are flagged at LOW)."""
    started = time.perf_counter()
    findings = []
    counter = 0
    for rel in ws.list_files():
        if not rel.endswith((".py", ".env", ".cfg", ".ini", ".yaml", ".yml", ".json", ".toml", ".txt")):
            continue
        content = ws.read_file(rel)
        for lineno, line in enumerate(content.splitlines(), start=1):
            for category, pattern in _SECRET_PATTERNS:
                if pattern.search(line):
                    in_tests = rel.startswith("tests")
                    placeholder = bool(_PLACEHOLDER_RE.search(line))
                    if in_tests or placeholder:
                        severity = Severity.LOW
                    else:
                        severity = Severity.HIGH
                    counter += 1
                    findings.append(
                        Finding(
                            id=f"SECRETS-{counter:02d}",
                            severity=severity,
                            component=rel,
                            finding=f"Possible {category.replace('_', ' ')} committed to source",
                            evidence=f"{rel}:{lineno}: {line.strip()[:160]}",
                            recommended_fix="Load the value from an environment variable or secrets manager and rotate the exposed credential",
                            source="secrets",
                            category=category,
                            file=rel,
                            line=lineno,
                        )
                    )
                    break
    exposed = [f for f in findings if f.severity in (Severity.HIGH, Severity.CRITICAL)]
    return ToolRun(
        tool="secrets",
        status="failed" if exposed else "passed",
        summary=f"{len(exposed)} exposed credential(s), {len(findings) - len(exposed)} low-risk match(es)",
        command="secret-scan (regex, workspace files)",
        duration_seconds=round(time.perf_counter() - started, 3),
        findings=findings,
        metrics={"exposed": len(exposed), "matches": len(findings)},
    )


# --------------------------------------------------------------- dependencies
def run_pip_audit(ws: Workspace, enabled: bool, timeout: int = 180) -> ToolRun:
    started = time.perf_counter()
    if not enabled:
        return ToolRun(tool="pip_audit", status="skipped", summary="dependency audit disabled (SDLC_ENABLE_PIP_AUDIT=false)", command="pip-audit", duration_seconds=0.0)
    if not ws.exists("requirements.txt"):
        return ToolRun(tool="pip_audit", status="skipped", summary="no requirements.txt in workspace", command="pip-audit", duration_seconds=0.0)
    tool = shutil.which("pip-audit")
    base = [tool] if tool else [sys.executable, "-m", "pip_audit"]
    result = ws.run([*base, "-r", "requirements.txt", "-f", "json", "--progress-spinner", "off"], timeout=timeout)
    try:
        report = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return ToolRun(tool="pip_audit", status="error", summary="pip-audit unavailable or failed (network?)", command=result.command, raw_output=result.output[-2000:], duration_seconds=round(time.perf_counter() - started, 3))
    findings = []
    counter = 0
    for dep in report.get("dependencies", []):
        for vuln in dep.get("vulns", []):
            counter += 1
            findings.append(
                Finding(
                    id=f"DEP-{counter:02d}",
                    severity=Severity.HIGH,
                    component=dep.get("name", "dependency"),
                    finding=f"{dep.get('name')} {dep.get('version')}: {vuln.get('id')}",
                    evidence=(vuln.get("description") or "")[:300],
                    recommended_fix=f"Upgrade to {', '.join(vuln.get('fix_versions', [])) or 'a patched version'}",
                    source="pip_audit",
                    category="dependency_vulnerability",
                    file="requirements.txt",
                )
            )
    return ToolRun(
        tool="pip_audit",
        status="failed" if findings else "passed",
        summary=f"{len(findings)} known vulnerability(ies)",
        command=result.command,
        raw_output=result.output[:2000],
        duration_seconds=round(time.perf_counter() - started, 3),
        findings=findings,
    )
