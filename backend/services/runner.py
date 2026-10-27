"""Execute generated Python only inside constrained, disposable Docker sandboxes."""

import json
import os
import selectors
import shutil
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

IMAGE = os.environ.get("RUNNER_IMAGE", "forge-runner:local")
TIMEOUT = 90
COMMANDS = {
    "syntax": ["python", "-I", "-m", "compileall", "-q", "."],
    "pytest": [
        "python",
        "-I",
        "-m",
        "pytest",
        "-q",
        "--override-ini=addopts=",
        "--override-ini=pythonpath=.",
        "--junitxml=/tmp/results.xml",
    ],
    "ruff": ["ruff", "check", "--isolated", "--select", "E4,E7,E9,F", "."],
    "bandit": ["bandit", "-r", ".", "--exclude", "./tests", "-f", "json", "--ignore-nosec"],
    "pip-audit": ["pip-audit", "prepared-offline-snapshot"],
}

# This wrapper is constant source, never assembled from generated code or paths.
WRAPPER = r"""
import datetime, importlib.metadata, json, pathlib, shutil, subprocess, sys
from packaging.requirements import Requirement
root = pathlib.Path('/tmp/project')
shutil.copytree('/input', root)
command = json.loads(sys.argv[1])
requirements = root / 'requirements.txt'
if requirements.exists():
    for line in requirements.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        try:
            req = Requirement(line)
            if req.url or req.extras:
                raise ValueError('URL dependencies and extras are not supported')
            if req.marker and not req.marker.evaluate():
                continue
            installed = importlib.metadata.version(req.name)
            if req.specifier and installed not in req.specifier:
                raise ValueError('prepared dependency version does not match ' + line)
        except (ValueError, importlib.metadata.PackageNotFoundError) as exc:
            print(json.dumps({'status': 'not_executed', 'exit_code': None, 'output': 'Dependency preparation required: ' + str(exc)}))
            sys.exit(0)
if command[0] == 'pip-audit':
    meta = json.loads(pathlib.Path('/opt/audit-meta.json').read_text())
    current = sorted((d.metadata['Name'].lower(), d.version) for d in importlib.metadata.distributions())
    age = datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(meta['created_at'])
    if current != [tuple(x) for x in meta['inventory']] or age.days > 7:
        result = {'status': 'not_executed', 'exit_code': None, 'output': 'Prepared vulnerability snapshot is stale or dependency inventory changed. Rebuild runner image.'}
    else:
        code = meta['exit_code']
        result = {'status': 'passed' if code == 0 else 'failed' if code == 1 else 'not_executed', 'exit_code': code, 'output': 'Offline pip-audit snapshot, created ' + meta['created_at'] + '; installed inventory matched.\n' + pathlib.Path('/opt/audit.json').read_text()[:16000]}
else:
    process = subprocess.run(command, cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    result = {'status': 'passed' if process.returncode == 0 else 'failed', 'exit_code': process.returncode, 'output': process.stdout[-16000:]}
    if 'pytest' in command:
        report = pathlib.Path('/tmp/results.xml')
        result['junit'] = report.read_text()[:200000] if report.exists() else ''
print(json.dumps(result))
"""


def parse_junit(xml: str) -> dict[str, str]:
    """Map pytest JUnit records to exact test node identifiers."""
    if not xml:
        return {}
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return {}
    tests = {}
    for case in root.iter("testcase"):
        classname = case.get("classname", "")
        parts = classname.split(".")
        test_index = next(
            (index for index, value in enumerate(parts) if value.startswith("Test")), len(parts)
        )
        filename = "/".join(parts[:test_index]) + ".py"
        node = "::".join([filename, *parts[test_index:], case.get("name", "")])
        status = (
            "FAILED"
            if case.find("failure") is not None or case.find("error") is not None
            else "SKIPPED"
            if case.find("skipped") is not None
            else "PASSED"
        )
        tests[node] = status
    return tests


def _not_executed(name: str, version: int, reason: str) -> dict:
    return {
        "name": name,
        "command": " ".join(COMMANDS[name]),
        "status": "not_executed",
        "exit_code": None,
        "output": reason,
        "version": version,
        "tests": {},
    }


def _run_bounded(command: list[str]) -> subprocess.CompletedProcess:
    """Bound Docker client output as well as the container's runtime and memory."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = bytearray()
    deadline = time.monotonic() + TIMEOUT
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, TIMEOUT)
                if not selector.select(min(remaining, 1)):
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > 262144:
                    raise ValueError("Sandbox exceeded 256 KB output limit")
        code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        return subprocess.CompletedProcess(command, code, output.decode(errors="replace"), "")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        if process.stdout:
            process.stdout.close()


def run_checks(path: Path, version: int) -> list[dict]:
    """Run mandatory controls; unavailable infrastructure never counts as passing."""
    docker = shutil.which(os.environ.get("DOCKER_BIN", "docker"))
    if not docker:
        return [
            _not_executed(name, version, "Docker is unavailable; generated code was not executed.")
            for name in COMMANDS
        ]
    try:
        mount_path = str(path.resolve())
        if docker.lower().endswith(".exe"):
            converted = subprocess.run(
                ["wslpath", "-w", mount_path], capture_output=True, text=True, timeout=5, check=True
            )
            mount_path = converted.stdout.strip()
        available = subprocess.run(
            [docker, "image", "inspect", IMAGE],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if available.returncode:
            reason = f"Runner image or Docker engine unavailable. Build {IMAGE} before validation. {available.stderr[-1000:]}"
            return [_not_executed(name, version, reason) for name in COMMANDS]
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        return [_not_executed(name, version, f"Docker unavailable: {exc}") for name in COMMANDS]
    checks = []
    for name, command in COMMANDS.items():
        container = f"forge-check-{uuid.uuid4().hex}"
        args = [
            docker,
            "run",
            "--name",
            container,
            "--network=none",
            "--read-only",
            "--memory=384m",
            "--memory-swap=384m",
            "--cpus=1",
            "--pids-limit=96",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user=65534:65534",
            "--tmpfs=/tmp:rw,nosuid,nodev,size=128m,mode=1777",
            "--mount",
            f"type=bind,src={mount_path},dst=/input,readonly",
            "--env=HOME=/tmp",
            "--env=PYTHONDONTWRITEBYTECODE=1",
            "--env=PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
            IMAGE,
            "python",
            "-I",
            "-c",
            WRAPPER,
            json.dumps(command),
        ]
        try:
            result = _run_bounded(args)
            if result.returncode:
                check = _not_executed(
                    name,
                    version,
                    f"Sandbox failed (exit {result.returncode}): {result.stderr[-2000:]} {result.stdout[-2000:]}",
                )
            else:
                data = json.loads(result.stdout)
                check = {"name": name, "command": " ".join(command), "version": version, **data}
                check["tests"] = parse_junit(check.pop("junit", ""))
                if name == "pytest" and check["status"] == "passed" and not check["tests"]:
                    check = _not_executed(
                        name, version, "Pytest returned no machine-readable execution evidence."
                    )
        except subprocess.TimeoutExpired:
            check = _not_executed(
                name, version, f"Sandbox exceeded {TIMEOUT}s timeout and was terminated."
            )
        except (OSError, ValueError) as exc:
            check = _not_executed(name, version, f"Sandbox could not complete: {exc}")
        finally:
            try:
                cleanup = subprocess.run(
                    [docker, "rm", "-f", container], capture_output=True, timeout=15, check=False
                )
                if cleanup.returncode:
                    check = _not_executed(
                        name,
                        version,
                        f"Sandbox cleanup failed for {container}; inspect Docker before retrying.",
                    )
            except (OSError, subprocess.TimeoutExpired) as exc:
                check = _not_executed(
                    name, version, f"Sandbox cleanup could not complete for {container}: {exc}"
                )
        checks.append(check)
    return checks
