"""Configurable quality gates (FR-15) and the deterministic release policy.

Each gate is a list of named checks evaluated against the workflow state. The
checks are plain Python functions over evidence produced by the tools - never
over the LLM's opinion of itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.models import (
    GateCheck,
    GateResult,
    ReleaseAssessment,
    ReleaseDecision,
    Severity,
    WorkflowState,
)


class GateConfig:
    def __init__(self, raw: dict[str, Any]):
        self.raw = raw
        self.gates: dict[str, dict[str, Any]] = raw.get("gates", {})
        self.remediation: dict[str, Any] = raw.get("remediation", {})
        self.release_policy: dict[str, Any] = raw.get("release_policy", {})

    @classmethod
    def load(cls, path: Path) -> GateConfig:
        with open(path, encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh) or {})

    def gate(self, name: str) -> dict[str, Any]:
        return self.gates.get(name, {})

    def blocking_severities(self) -> set[Severity]:
        names = self.gate("security").get("blocking_severities", ["CRITICAL", "HIGH"])
        return {Severity(n) for n in names}

    @property
    def max_iterations(self) -> int:
        return int(self.remediation.get("max_iterations", 3))

    @property
    def require_remediation_approval(self) -> bool:
        return bool(self.remediation.get("require_human_approval", True))

    @property
    def rerun_ai_review(self) -> bool:
        return bool(self.remediation.get("rerun_ai_review", True))


# --------------------------------------------------------------------------- #
# individual checks
# --------------------------------------------------------------------------- #
def _blocking_findings(state: WorkflowState, cfg: GateConfig, sources: set[str] | None = None) -> list:
    blocking = cfg.blocking_severities()
    return [f for f in state.open_findings() if f.severity in blocking and (sources is None or f.source in sources)]


def check_requirement(state: WorkflowState, cfg: GateConfig) -> list[GateCheck]:
    ra = state.requirement_analysis
    checks: list[GateCheck] = []
    if ra is None:
        return [GateCheck(name="requirement_analysis_present", passed=False, evidence="no analysis")]
    stories_without_ac = [s.id for s in ra.user_stories if not any(ac.story_id == s.id for ac in ra.acceptance_criteria)]
    checks.append(
        GateCheck(
            name="acceptance_criteria_defined",
            passed=bool(ra.acceptance_criteria) and not stories_without_ac and all(ac.measurable for ac in ra.acceptance_criteria),
            evidence=f"{len(ra.acceptance_criteria)} AC for {len(ra.user_stories)} stories"
            + (f"; stories without AC: {stories_without_ac}" if stories_without_ac else ""),
        )
    )
    checks.append(
        GateCheck(
            name="ambiguities_identified",
            passed=True,
            evidence=f"{len(ra.ambiguities)} ambiguities, {len(ra.assumptions)} assumptions, {len(ra.technical_questions)} questions surfaced for human confirmation",
        )
    )
    keywords = [k.lower() for k in cfg.gate("requirement").get("required_nfr_keywords", [])]
    text = " ".join(n.description.lower() for n in ra.non_functional_requirements)
    missing = [k for k in keywords if k not in text]
    checks.append(
        GateCheck(
            name="mandatory_nfrs_considered",
            passed=not missing,
            evidence=f"{len(ra.non_functional_requirements)} NFRs" + (f"; missing categories: {missing}" if missing else "; all required categories covered"),
        )
    )
    return checks


def check_code(state: WorkflowState, cfg: GateConfig) -> list[GateCheck]:
    runs = state.latest_tool_runs()
    build, ruff = runs.get("build"), runs.get("ruff")
    checks = [
        GateCheck(name="build_succeeds", passed=bool(build and build.status == "passed"), evidence=build.summary if build else "build not run"),
        GateCheck(name="static_analysis_completed", passed=bool(ruff and ruff.status in ("passed", "failed")), evidence=ruff.summary if ruff else "ruff not run"),
    ]
    critical = [f for f in state.open_findings() if f.source in ("ruff", "ast") and f.severity in (Severity.HIGH, Severity.CRITICAL)]
    checks.append(
        GateCheck(
            name="no_critical_violations",
            passed=not critical,
            evidence=f"{len(critical)} critical static-analysis finding(s)" + (": " + ", ".join(f.id for f in critical) if critical else ""),
        )
    )
    return checks


def check_test(state: WorkflowState, cfg: GateConfig) -> list[GateCheck]:
    run = state.latest_tool_runs().get("pytest")
    minimum = int(cfg.gate("test").get("minimum_tests", 1))
    total = run.metrics.get("total", 0) if run else 0
    failed = run.metrics.get("failed", 0) if run else 0
    checks = [
        GateCheck(name="mandatory_tests_executed", passed=bool(run) and total >= minimum, evidence=f"{total} tests executed (minimum {minimum})" if run else "pytest not run"),
        GateCheck(name="required_tests_pass", passed=bool(run) and run.status == "passed" and failed == 0, evidence=run.summary if run else "pytest not run"),
    ]
    ra = state.requirement_analysis
    mapped: set[str] = set()
    if run:
        for t in run.test_results:
            mapped.update(t.acceptance_criteria_ids)
    if state.test_bundle:
        for tc in state.test_bundle.test_cases:
            mapped.update(tc.acceptance_criteria_ids)
    all_ac = [ac.id for ac in ra.acceptance_criteria] if ra else []
    unmapped = [ac for ac in all_ac if ac not in mapped]
    checks.append(
        GateCheck(
            name="acceptance_criteria_have_tests",
            passed=bool(all_ac) and not unmapped,
            evidence=f"{len(all_ac) - len(unmapped)}/{len(all_ac)} acceptance criteria mapped to tests" + (f"; unmapped: {unmapped}" if unmapped else ""),
        )
    )
    return checks


def check_security(state: WorkflowState, cfg: GateConfig) -> list[GateCheck]:
    runs = state.latest_tool_runs()
    security_sources = {"bandit", "secrets", "pip_audit", "security_agent"}
    blocking = _blocking_findings(state, cfg, security_sources)
    secrets_run = runs.get("secrets")
    exposed = [f for f in state.open_findings() if f.source == "secrets" and f.severity in (Severity.HIGH, Severity.CRITICAL)]
    required = cfg.gate("security").get("required_scanners", [])
    missing = [name for name in required if name not in runs or runs[name].status == "error"]
    return [
        GateCheck(name="no_blocking_security_findings", passed=not blocking, evidence=f"{len(blocking)} open blocking security finding(s)" + (": " + ", ".join(f.id for f in blocking) if blocking else "")),
        GateCheck(name="no_exposed_credentials", passed=bool(secrets_run) and not exposed, evidence=secrets_run.summary if secrets_run else "secret scan not run"),
        GateCheck(name="required_security_checks_completed", passed=not missing, evidence=f"required scanners {required}" + (f"; missing/errored: {missing}" if missing else " all completed")),
    ]


def check_traceability(state: WorkflowState, cfg: GateConfig) -> list[GateCheck]:
    t = state.traceability
    if t is None:
        return [GateCheck(name="traceability_matrix_present", passed=False, evidence="not computed")]
    min_req = float(cfg.gate("traceability").get("minimum_requirement_coverage", 100))
    min_ac = float(cfg.gate("traceability").get("minimum_acceptance_criteria_coverage", 100))
    return [
        GateCheck(name="requirements_mapped_to_code", passed=t.requirement_coverage >= min_req, evidence=f"{t.requirement_coverage:.0f}% of requirements mapped to code (min {min_req:.0f}%)" + (f"; unmapped: {t.unmapped_requirements}" if t.unmapped_requirements else "")),
        GateCheck(name="acceptance_criteria_mapped_to_tests", passed=t.acceptance_criteria_coverage >= min_ac, evidence=f"{t.acceptance_criteria_coverage:.0f}% of AC mapped to tests (min {min_ac:.0f}%)" + (f"; unmapped: {t.unmapped_acceptance_criteria}" if t.unmapped_acceptance_criteria else "")),
        GateCheck(name="tests_have_execution_results", passed=t.tests_with_results >= 100, evidence=f"{t.tests_with_results:.0f}% of mapped tests have execution results"),
    ]


def check_release(state: WorkflowState, cfg: GateConfig) -> list[GateCheck]:
    latest = state.latest_gate_results()
    failed = [name for name, g in latest.items() if g.mandatory and not g.passed and name != "release"]
    blocking = _blocking_findings(state, cfg)
    design_ok = any(a.checkpoint == "design" and a.decision == "approve" for a in state.approvals)
    pending_mods = [m for m in state.modifications if not m.approved_by] if cfg.require_remediation_approval else []
    return [
        GateCheck(name="mandatory_gates_passed", passed=not failed, evidence="all mandatory gates passed" if not failed else f"failed gates: {failed}"),
        GateCheck(name="no_unresolved_blocking_defects", passed=not blocking, evidence=f"{len(blocking)} open CRITICAL/HIGH finding(s)" + (": " + ", ".join(f.id for f in blocking) if blocking else "")),
        GateCheck(name="required_human_approvals_recorded", passed=design_ok and not pending_mods, evidence=("design approved" if design_ok else "design approval missing") + (f"; {len(pending_mods)} AI modification(s) without approval" if pending_mods else "")),
    ]


CHECKS = {
    "requirement": check_requirement,
    "code": check_code,
    "test": check_test,
    "security": check_security,
    "traceability": check_traceability,
    "release": check_release,
}


class GateEngine:
    def __init__(self, cfg: GateConfig):
        self.cfg = cfg

    def evaluate(self, gate: str, state: WorkflowState) -> GateResult:
        spec = self.cfg.gate(gate)
        checks = CHECKS[gate](state, self.cfg)
        enabled = set(spec.get("checks", [])) or {c.name for c in checks}
        checks = [c for c in checks if c.name in enabled or c.name.endswith("_present")]
        return GateResult(
            gate=gate,
            mandatory=bool(spec.get("mandatory", True)),
            passed=all(c.passed for c in checks),
            checks=checks,
            iteration=state.iteration,
        )

    def evaluate_many(self, gates: list[str], state: WorkflowState) -> list[GateResult]:
        return [self.evaluate(g, state) for g in gates]

    # ------------------------------------------------------------------ policy
    def apply_release_policy(self, assessment: ReleaseAssessment, state: WorkflowState) -> ReleaseAssessment:
        """The agent recommends; the policy decides.  A recommendation can only be made
        *less* optimistic by the policy, never more."""
        latest = state.latest_gate_results()
        failed = [name for name, g in latest.items() if g.mandatory and not g.passed]
        blocking = _blocking_findings(state, self.cfg)
        human_rejected = any(a.decision == "reject" and a.checkpoint in ("design", "remediation") for a in state.approvals)
        exhausted = state.iteration >= self.cfg.max_iterations and (failed or blocking)

        forced: ReleaseDecision | None = None
        reason = ""
        if self.cfg.release_policy.get("block_on_failed_mandatory_gates", True) and (failed or blocking):
            forced, reason = ReleaseDecision.NOT_READY, f"failed mandatory gates {failed}; open blocking findings {[f.id for f in blocking]}"
        elif human_rejected or exhausted:
            forced, reason = ReleaseDecision.MANUAL_REVIEW, "a human rejected an AI change or remediation attempts are exhausted"
        elif assessment.decision == ReleaseDecision.READY:
            medium = [f for f in state.open_findings() if f.severity == Severity.MEDIUM]
            if medium and self.cfg.release_policy.get("conditional_allowed_when_open_medium_findings", True):
                forced, reason = ReleaseDecision.CONDITIONAL, f"open MEDIUM findings remain: {[f.id for f in medium]}"

        order = [ReleaseDecision.READY, ReleaseDecision.CONDITIONAL, ReleaseDecision.MANUAL_REVIEW, ReleaseDecision.NOT_READY]
        if forced is not None and order.index(forced) > order.index(assessment.decision):
            assessment.policy_override = f"Agent recommended {assessment.decision.value}; policy enforced {forced.value}: {reason}"
            assessment.decision = forced
        return assessment
