from backend.services.gates import assess


def ready_run():
    return {
        "version": 2,
        "checks": [
            {"name": name, "version": 2, "status": "passed"}
            for name in ("syntax", "pytest", "ruff", "bandit", "pip-audit")
        ],
        "traceability": [
            {
                "execution": "PASSED",
                "implementation": "app/main.py → route",
                "test": "tests/test_api.py::test_api",
            }
        ],
        "approvals": [{"kind": "design", "version": 1}, {"kind": "release", "version": 2}],
    }


def test_readiness_requires_actual_current_evidence():
    run = ready_run()
    assert assess(run)["recommendation"] == "READY"
    run["checks"][1]["version"] = 1
    assert assess(run)["recommendation"] == "MANUAL REVIEW"


def test_failed_tests_override_missing_approval():
    run = ready_run()
    run["checks"][1]["status"] = "failed"
    run["approvals"] = []
    assert assess(run)["recommendation"] == "NOT READY"


def test_security_blocks_and_nonblocking_findings_conditional():
    run = ready_run()
    run["findings"] = [{"severity": "high", "status": "open", "version": 2}]
    assert assess(run)["recommendation"] == "NOT READY"
    run["findings"][0]["severity"] = "low"
    assert assess(run)["recommendation"] == "CONDITIONAL"


def test_code_change_invalidates_final_approval_and_test_mapping_is_not_execution():
    run = ready_run()
    run["approvals"][-1]["version"] = 1
    assert assess(run)["recommendation"] == "MANUAL REVIEW"
    run = ready_run()
    run["traceability"][0]["execution"] = "NOT EXECUTED"
    assert assess(run)["recommendation"] == "MANUAL REVIEW"
