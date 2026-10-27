"""Deterministic release decisions; agents may explain but never override."""

import os

REQUIRED_CHECKS = ("syntax", "pytest", "ruff", "bandit")


def assess(run: dict) -> dict:
    """Calculate readiness using current-version checks and recorded approvals."""
    version = run.get("version", 1)
    checks = {
        item["name"]: item for item in run.get("checks", []) if item.get("version") == version
    }
    gates = []
    required = list(REQUIRED_CHECKS)
    if os.environ.get("REQUIRE_DEPENDENCY_AUDIT", "true").lower() == "true":
        required.append("pip-audit")
    for name in required:
        check = checks.get(name)
        status = check.get("status", "not_executed") if check else "not_executed"
        gates.append(
            {
                "name": name,
                "status": status,
                "reason": (
                    "Current project version passed."
                    if status == "passed"
                    else "Current project version failed; see command evidence."
                    if status == "failed"
                    else (
                        check.get("output", "Required check missing.")[:400]
                        if check
                        else "Required check missing."
                    )
                ),
            }
        )
    findings = [
        f
        for f in run.get("findings", [])
        if f.get("status") == "open" and f.get("version", version) == version
    ]
    blocking = [f for f in findings if f.get("severity", "").lower() in ("high", "critical")]
    gates.append(
        {
            "name": "security findings",
            "status": "failed" if blocking else "passed",
            "reason": f"{len(blocking)} unresolved high/critical findings.",
        }
    )
    trace = run.get("traceability", [])
    coverage_passed = bool(trace) and all(
        t.get("execution") == "PASSED"
        and t.get("implementation") not in (None, "", "Unmapped")
        and t.get("test") not in (None, "", "Unmapped")
        for t in trace
    )
    gates.append(
        {
            "name": "traceability",
            "status": "passed" if coverage_passed else "not_executed",
            "reason": "Every acceptance criterion maps to implementation and a passing test."
            if coverage_passed
            else "Acceptance criteria need implementation mapping and current passing execution evidence.",
        }
    )
    approvals = run.get("approvals", [])
    design_ok = any(a.get("kind") == "design" for a in approvals)
    release_ok = any(a.get("kind") == "release" and a.get("version") == version for a in approvals)
    for name, approved in [("design approval", design_ok), ("final approval", release_ok)]:
        gates.append(
            {
                "name": name,
                "status": "passed" if approved else "not_executed",
                "reason": "Human approval recorded." if approved else "Human approval required.",
            }
        )
    if any(g["status"] == "failed" for g in gates):
        decision = "NOT READY"
    elif any(g["status"] != "passed" for g in gates):
        decision = "MANUAL REVIEW"
    elif findings:
        decision = "CONDITIONAL"
    else:
        decision = "READY"
    explanation = {
        "READY": "All mandatory checks, traceability and human approvals passed.",
        "CONDITIONAL": "Mandatory gates passed; nonblocking findings require documented follow-up.",
        "MANUAL REVIEW": "Required execution evidence or human approval is missing.",
        "NOT READY": "A mandatory check failed or blocking findings remain.",
    }[decision]
    return {"recommendation": decision, "explanation": explanation, "gates": gates}
