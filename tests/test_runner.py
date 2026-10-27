import subprocess
from unittest.mock import patch

import pytest

from backend.services import workspace
from backend.services.runner import parse_junit, run_checks


def test_missing_docker_never_passes(tmp_path):
    with patch("backend.services.runner.shutil.which", return_value=None):
        checks = run_checks(tmp_path, 1)
    assert len(checks) == 5
    assert all(c["status"] == "not_executed" for c in checks)


def test_timeout_cleans_up_container(tmp_path):
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        if "run" in command:
            raise subprocess.TimeoutExpired(command, 90)
        return subprocess.CompletedProcess(command, 0, "", "")

    with (
        patch("backend.services.runner.shutil.which", return_value="docker"),
        patch("backend.services.runner.subprocess.run", side_effect=execute),
        patch("backend.services.runner._run_bounded", side_effect=execute),
    ):
        checks = run_checks(tmp_path, 2)
    assert all(c["status"] == "not_executed" for c in checks)
    assert any("rm" in command and "-f" in command for command in calls)
    execution = next(c for c in calls if "run" in c)
    assert "--network=none" in execution
    assert "--read-only" in execution
    assert "--cap-drop=ALL" in execution
    assert "--env-file" not in execution


def test_junit_status_is_per_executed_test():
    xml = '<testsuites><testsuite><testcase classname="tests.test_contact" name="test_ok"/><testcase classname="tests.test_contact" name="test_invalid"><failure/></testcase><testcase classname="tests.test_contact" name="test_skip"><skipped/></testcase></testsuite></testsuites>'
    assert parse_junit(xml) == {
        "tests/test_contact.py::test_ok": "PASSED",
        "tests/test_contact.py::test_invalid": "FAILED",
        "tests/test_contact.py::test_skip": "SKIPPED",
    }


@pytest.mark.parametrize(
    "name",
    [
        "../main.py",
        "/tmp/main.py",
        "app/../../main.py",
        "app\\main.py",
        ".git/config",
        "app//main.py",
    ],
)
def test_workspace_rejects_unsafe_generated_paths(tmp_path, name):
    with patch.object(workspace, "ROOT", tmp_path), pytest.raises(ValueError):
        workspace.write_project("run", 1, [{"path": name, "content": "x"}])


def test_workspace_versions_are_immutable(tmp_path):
    with patch.object(workspace, "ROOT", tmp_path):
        workspace.write_project("run", 1, [{"path": "app/main.py", "content": "x = 1"}])
        workspace.write_project("run", 1, [{"path": "app/main.py", "content": "x = 1"}])
        with pytest.raises(FileExistsError):
            workspace.write_project("run", 1, [{"path": "app/main.py", "content": "x = 2"}])
        assert workspace.read_project("run", 1)[0]["content"] == "x = 1"
