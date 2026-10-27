"""Command-line runner.

    python -m app.cli run "Develop an API ..." --title "Contact update" --auto-approve
    python -m app.cli run --example --auto-approve
    python -m app.cli report <feature_id>

``--auto-approve`` records approvals on behalf of the named approver at every
human checkpoint - handy for demos and CI, but note that the platform itself
never approves anything on its own.
"""

from __future__ import annotations

import argparse
import json
import sys

from app.config import get_settings
from app.models import ApprovalRequest, FeatureRequest, WorkflowState
from app.orchestration.engine import WorkflowEngine
from app.persistence import Store
from app.tools.product import build_handoff

EXAMPLE = FeatureRequest(
    title="Customer contact update API",
    description=(
        "Develop an API allowing customers to update their contact information. Validate mandatory "
        "fields, prevent unauthorized modification, maintain an audit record and return appropriate "
        "API error responses."
    ),
)

CHECKPOINT_FOR_STAGE = {
    "awaiting_design_approval": "design",
    "awaiting_remediation_approval": "remediation",
    "awaiting_release_approval": "release",
}


def _print_status(state: WorkflowState) -> None:
    gates = state.latest_gate_results()
    print(f"\n== stage: {state.stage.value}  (iteration {state.iteration})")
    for g in gates.values():
        print(f"   gate {g.gate:<13} {'PASS' if g.passed else 'FAIL'}  " + "; ".join(f"{c.name}={'ok' if c.passed else 'FAIL'}" for c in g.checks))
    for run in state.latest_tool_runs().values():
        print(f"   tool {run.tool:<9} {run.status:<7} {run.summary}")
    open_findings = state.open_findings()
    if open_findings:
        print(f"   open findings ({len(open_findings)}):")
        for f in open_findings:
            print(f"     - {f.id:<12} {f.severity.value:<8} {f.source:<15} {f.file}: {f.finding[:80]}")


def run_feature(engine: WorkflowEngine, request: FeatureRequest, auto_approve: bool, approver: str) -> WorkflowState:
    state = engine.create(request)
    print(f"feature {state.feature_id} created for '{request.title}' (provider: {engine.settings.llm_provider})")
    while True:
        state = engine.run(state.feature_id)
        _print_status(state)
        if state.is_terminal():
            break
        checkpoint = CHECKPOINT_FOR_STAGE[state.stage.value]
        if checkpoint == "remediation" and state.remediation_proposal and not state.remediation_proposal.changes:
            print(f"\n   AI could not fix this safely: {state.remediation_proposal.failure_summary}")
            print(f"   it needs to know: {state.remediation_proposal.probable_cause}")
            if auto_approve:
                print("   [auto-approve] guidance cannot be invented - skipping")
            guidance = "" if auto_approve else input("\n   your guidance (empty to skip): ").strip()
            decision = "approve" if guidance else "reject"
            state = engine.approve(state.feature_id, checkpoint, ApprovalRequest(decision=decision, approver=approver, comment=guidance))  # type: ignore[arg-type]
            continue
        if checkpoint == "remediation" and state.remediation_proposal:
            print(f"\n   AI proposes changes to {state.remediation_proposal.affected_files}: {state.remediation_proposal.rationale}")
            for mod in state.modifications:
                if mod.iteration == state.iteration:
                    print(mod.diff)
        if checkpoint == "release" and state.release_assessment:
            ra = state.release_assessment
            print(f"\n   release recommendation: {ra.decision.value} - {ra.rationale}")
            if ra.policy_override:
                print(f"   policy override: {ra.policy_override}")
        if auto_approve:
            decision = "approve"
            print(f"\n   [auto-approve] {checkpoint} approved by {approver}")
        else:
            answer = input(f"\n   {checkpoint} approval - approve? [y/N] ").strip().lower()
            decision = "approve" if answer in ("y", "yes") else "reject"
        state = engine.approve(state.feature_id, checkpoint, ApprovalRequest(decision=decision, approver=approver, comment="cli"))  # type: ignore[arg-type]
    print(f"\n== finished: {state.stage.value}")
    if state.release_assessment:
        print(f"   decision: {state.release_assessment.decision.value}")
    if state.traceability:
        t = state.traceability
        print(f"   traceability: requirements {t.requirement_coverage}%, AC {t.acceptance_criteria_coverage}%, tests with results {t.tests_with_results}%")
    print(f"   workspace: {engine.settings.workspace_root / state.feature_id}")
    if state.stage.value == "completed":
        _print_handoff(build_handoff(state, engine.settings, None))
    return state


def _print_handoff(h: dict) -> None:
    built = h["what_was_built"]
    print("\n== your product")
    print(f"   {built['summary']}\n   {built['kind']}")
    for e in built["endpoints"]:
        print(f"     {e['method']:<6} {e['path']:<40} {e['description']}")
    print("   checked: " + "; ".join(h["what_was_verified"]))
    print(f"   code:    {h['where_the_code_is']}")
    print(f"   run it:  {h['run_it_yourself']}")
    print("            then open http://localhost:8001/docs (or use the dashboard's \"Launch app\" button)")
    print("   good to know:")
    for item in h["limitations"]:
        print(f"     - {item}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sdlc-copilot")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the SDLC workflow for a feature request")
    run.add_argument("description", nargs="?", help="feature request text")
    run.add_argument("--title", default="Feature request")
    run.add_argument("--context", default="")
    run.add_argument("--example", action="store_true", help="use the example request from the hackathon brief")
    run.add_argument("--auto-approve", action="store_true")
    run.add_argument("--approver", default="developer")
    run.add_argument("--report", help="write the final workflow state as JSON to this path")
    rep = sub.add_parser("report", help="print the stored workflow state of a feature")
    rep.add_argument("feature_id")
    args = parser.parse_args(argv)

    settings = get_settings()
    engine = WorkflowEngine(Store(settings.db_path), settings)
    if args.command == "report":
        state = engine.load(args.feature_id)
        print(state.model_dump_json(indent=2))
        return 0
    request = EXAMPLE if args.example or not args.description else FeatureRequest(title=args.title, description=args.description, context=args.context)
    state = run_feature(engine, request, args.auto_approve, args.approver)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(state.model_dump(mode="json"), fh, indent=2)
        print(f"   report written to {args.report}")
    return 0 if state.stage.value == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
