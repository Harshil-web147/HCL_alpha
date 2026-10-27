"""Deterministic engineering tools: workspace isolation, scanners, test runner."""

from __future__ import annotations

import pytest

from app.models import GeneratedFile, Severity
from app.tools.build import run_build
from app.tools.pytest_runner import acceptance_mapping_from_source, run_pytest
from app.tools.security_scanners import run_bandit, run_secret_scan
from app.tools.static_analysis import run_ast_analysis, run_ruff
from app.tools.workspace import Workspace, WorkspaceError

APP = '''"""demo"""
import hashlib

API_TOKEN = "sk-live-0123456789abcdef"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
'''
TESTS = '''from app.main import digest


def test_digest_is_stable():
    """AC: AC-01, AC-02"""
    assert digest("a") == digest("a")


def test_digest_changes():
    """AC: AC-02"""
    assert digest("a") != digest("b")
'''
PYTEST_INI = "[pytest]\ntestpaths = tests\npythonpath = .\naddopts = -p no:cacheprovider\n"


@pytest.fixture
def ws(tmp_path):
    w = Workspace(tmp_path, "feat-x")
    w.write_files(
        [
            GeneratedFile(path="app/__init__.py", content=""),
            GeneratedFile(path="app/main.py", content=APP),
            GeneratedFile(path="tests/__init__.py", content=""),
            GeneratedFile(path="tests/test_demo.py", content=TESTS),
            GeneratedFile(path="pytest.ini", content=PYTEST_INI),
        ]
    )
    return w


def test_workspace_refuses_path_traversal(ws):
    with pytest.raises(WorkspaceError):
        ws.write_files([GeneratedFile(path="../escape.py", content="x")])
    with pytest.raises(WorkspaceError):
        ws.write_files([GeneratedFile(path="/etc/passwd", content="x")])


def test_workspace_diff_and_hash(ws):
    before = ws.file_hash("app/main.py")
    diff = ws.diff("app/main.py", APP.replace("sk-live", "redacted"))
    assert "-API_TOKEN" in diff and "+API_TOKEN" in diff
    assert before and before == ws.file_hash("app/main.py")


def test_secret_scanner_blocks_exposed_credentials_only_in_app_code(ws):
    run = run_secret_scan(ws)
    assert run.status == "failed"
    assert [f.severity for f in run.findings] == [Severity.HIGH]
    ws.write_files([GeneratedFile(path="app/main.py", content=APP.replace('"sk-live-0123456789abcdef"', 'os.environ["API_TOKEN"]').replace("import hashlib", "import hashlib\nimport os"))])
    assert run_secret_scan(ws).status == "passed"


def test_secret_scanner_downgrades_test_fixtures(ws):
    ws.write_files([GeneratedFile(path="tests/conftest.py", content='SECRET = "test-only-signing-key-123"\n')])
    run = run_secret_scan(ws)
    low = [f for f in run.findings if f.file == "tests/conftest.py"]
    assert low and low[0].severity == Severity.LOW


def test_pytest_runner_parses_results_and_maps_acceptance_criteria(ws):
    run = run_pytest(ws, {"test_digest_changes": ["AC-02", "AC-03"]})
    assert run.status == "passed" and run.metrics == {"passed": 2, "failed": 0, "total": 2}
    by_name = {t.name: t for t in run.test_results}
    assert by_name["test_digest_is_stable"].acceptance_criteria_ids == ["AC-01", "AC-02"]  # from docstring
    assert by_name["test_digest_changes"].acceptance_criteria_ids == ["AC-02", "AC-03"]  # explicit mapping wins
    assert acceptance_mapping_from_source(ws)["test_digest_is_stable"] == ["AC-01", "AC-02"]


def test_pytest_runner_reports_failures_as_high_findings(ws):
    ws.write_files([GeneratedFile(path="tests/test_demo.py", content=TESTS.replace('!= digest("b")', '== digest("b")'))])
    run = run_pytest(ws)
    assert run.status == "failed" and run.metrics["failed"] == 1
    assert run.findings and run.findings[0].severity == Severity.HIGH and "test_digest_changes" in run.findings[0].finding


def test_build_detects_import_errors(ws):
    assert run_build(ws).status == "passed"
    ws.write_files([GeneratedFile(path="app/main.py", content="import does_not_exist\n")])
    run = run_build(ws)
    assert run.status == "failed" and run.findings[0].severity == Severity.CRITICAL


def test_ruff_flags_undefined_names_as_critical(ws):
    assert run_ruff(ws).status == "passed"
    ws.write_files([GeneratedFile(path="app/main.py", content="def f():\n    return undefined_name\n")])
    run = run_ruff(ws)
    assert run.status == "failed"
    assert any(f.category == "F821" and f.severity == Severity.HIGH for f in run.findings)


def test_ast_analysis_flags_eval_and_bare_except(ws):
    ws.write_files([GeneratedFile(path="app/main.py", content="def f(x):\n    try:\n        return eval(x)\n    except:\n        return None\n")])
    run = run_ast_analysis(ws)
    categories = {f.category for f in run.findings}
    assert {"dangerous_call", "exception_handling"} <= categories
    assert run.status == "failed"


def test_bandit_runs_and_reports(ws):
    run = run_bandit(ws)
    assert run.status in ("passed", "failed")
    assert any(f.category == "B105" for f in run.findings)
