"""Run the real HTTP and Docker reference workflow against a running backend."""

import json
import time
import urllib.request

BASE = "http://localhost:8000/api"


def request(path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)["data"]


def wait(run_id: str) -> dict:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        run = request("/runs/" + run_id)
        if run["current_stage"] not in ("analyzing", "generating", "validating"):
            print(run["current_stage"], "version", run["version"], flush=True)
            return run
        time.sleep(1)
    raise TimeoutError("Workflow did not reach its approval pause")


def main() -> None:
    run = request(
        "/runs",
        {
            "requirement": "A customer can update their phone number with validation, ownership checks and an audit record.",
            "mode": "demo",
        },
    )
    rid = run["id"]
    print("Run:", rid, flush=True)
    for kind in ("design", "fix", "release"):
        run = wait(rid)
        expected = {
            "design": "design_approval",
            "fix": "fix_approval",
            "release": "release_review",
        }[kind]
        if run["current_stage"] != expected:
            print(
                json.dumps(
                    {
                        "checks": run["checks"],
                        "events": run["events"][-3:],
                        "report": run["report"],
                    },
                    indent=2,
                )
            )
            raise AssertionError(f"Expected {expected}, got {run['current_stage']}")
        request(f"/runs/{rid}/approve", {"kind": kind, "version": run["version"]})
    run = wait(rid)
    checks = run["checks"]
    assert any(
        c["name"] == "pytest" and c["version"] == 1 and c["status"] == "failed" for c in checks
    )
    assert all(c["status"] == "passed" for c in checks if c["version"] == 2)
    assert len(run["traceability"]) == 4
    assert all(t["execution"] == "PASSED" for t in run["traceability"])
    assert run["report"]["recommendation"] == "CONDITIONAL"
    print(
        "Real Docker demo passed: initial failure, approved correction, 5 passing checks, 4 passing ACs, final CONDITIONAL."
    )


if __name__ == "__main__":
    main()
