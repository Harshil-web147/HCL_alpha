"""Multi-agent orchestration: a custom Python state machine over ``WorkflowState``.

Every stage of the 19-step workflow in the brief is a handler that reads the
state, calls exactly one kind of worker (an agent, a deterministic tool, a gate
evaluation or a human) and returns the next stage.  The engine persists the
state after every step so a workflow can be paused for human approval, resumed
later, inspected by the dashboard or replayed from the event log.

    AI generates (agents)  ->  tools validate (tools/*, gates)  ->  humans govern (approvals)
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable

from app.agents import (
    BusinessAnalystAgent,
    CodeReviewerAgent,
    DefectResolutionAgent,
    DeveloperAgent,
    ReleaseAssuranceAgent,
    SecurityReviewAgent,
    SolutionDesignAgent,
    TestEngineerAgent,
)
from app.config import Settings, get_settings
from app.llm.client import LLMClient, LLMProvider, build_provider
from app.models import (
    PAUSE_STAGES,
    Approval,
    ApprovalRequest,
    FeatureRequest,
    Finding,
    GeneratedFile,
    ModificationRecord,
    Severity,
    Stage,
    StageTransition,
    ToolRun,
    ValidationSnapshot,
    WorkflowState,
    utcnow,
)
from app.observability import (
    EVENT_ERROR,
    EVENT_GATE,
    EVENT_HUMAN,
    EVENT_MODIFICATION,
    EVENT_POLICY,
    EVENT_STAGE,
    EVENT_TOOL,
    EventLog,
    Tracer,
)
from app.orchestration.gates import GateConfig, GateEngine
from app.orchestration.traceability import build_traceability
from app.persistence import Store
from app.tools.build import run_build
from app.tools.pytest_runner import run_pytest
from app.tools.security_scanners import run_bandit, run_pip_audit, run_secret_scan
from app.tools.static_analysis import run_ast_analysis, run_ruff
from app.tools.workspace import Workspace


class WorkflowError(RuntimeError):
    pass


class WorkflowEngine:
    def __init__(
        self,
        store: Store,
        settings: Settings | None = None,
        provider: LLMProvider | None = None,
        gate_config: GateConfig | None = None,
    ):
        self.settings = settings or get_settings()
        self.store = store
        self.provider = provider or build_provider(self.settings)
        self.gates = GateEngine(gate_config or GateConfig.load(self.settings.gates_config))
        self.tracer = Tracer(self.settings)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._handlers: dict[Stage, Callable[[WorkflowState, _Context], Stage]] = {
            Stage.RECEIVED: self._h_received,
            Stage.ANALYZING_REQUIREMENT: self._h_analyze,
            Stage.REQUIREMENT_GATE: self._h_requirement_gate,
            Stage.DESIGNING: self._h_design,
            Stage.GENERATING_CODE: self._h_generate_code,
            Stage.GENERATING_TESTS: self._h_generate_tests,
            Stage.EXECUTING_TESTS: self._h_execute_tests,
            Stage.STATIC_ANALYSIS: self._h_static_analysis,
            Stage.SECURITY_VALIDATION: self._h_security_validation,
            Stage.AI_CODE_REVIEW: self._h_ai_review,
            Stage.EVALUATING_GATES: self._h_evaluate_gates,
            Stage.REMEDIATING: self._h_remediate,
            Stage.REVALIDATING: self._h_revalidate,
            Stage.TRACEABILITY_CHECK: self._h_traceability,
            Stage.RELEASE_ASSESSMENT: self._h_release_assessment,
        }

    # ------------------------------------------------------------------ infra
    def _lock(self, feature_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(feature_id, threading.Lock())

    def _context(self, state: WorkflowState) -> _Context:
        events = EventLog(self.store, state.feature_id, self.tracer)
        llm = LLMClient(self.settings, provider=self.provider, observer=events.llm_observer)
        return _Context(
            events=events,
            llm=llm,
            workspace=Workspace(self.settings.workspace_root, state.feature_id),
        )

    def _transition(self, state: WorkflowState, ctx: _Context, to: Stage, note: str = "") -> None:
        state.transitions.append(StageTransition(from_stage=state.stage, to_stage=to, note=note))
        ctx.events.emit(EVENT_STAGE, "orchestrator", f"{state.stage.value} -> {to.value}" + (f": {note}" if note else ""), from_stage=state.stage.value, to_stage=to.value)
        state.stage = to
        self.store.save_state(state)

    # ------------------------------------------------------------- public API
    def create(self, request: FeatureRequest) -> WorkflowState:
        state = WorkflowState(feature_id=f"feat-{uuid.uuid4().hex[:8]}", request=request)
        self.store.save_state(state)
        ctx = self._context(state)
        ctx.events.emit(EVENT_STAGE, "intake", f"feature received: {request.title}", request=request.model_dump())
        return state

    def load(self, feature_id: str) -> WorkflowState:
        state = self.store.load_state(feature_id)
        if state is None:
            raise WorkflowError(f"unknown feature {feature_id}")
        return state

    def run(self, feature_id: str, max_steps: int = 60) -> WorkflowState:
        """Advance the workflow until it pauses for a human, finishes or fails."""
        with self._lock(feature_id):
            state = self.load(feature_id)
            ctx = self._context(state)
            steps = 0
            while state.stage not in PAUSE_STAGES and not state.is_terminal() and steps < max_steps:
                steps += 1
                handler = self._handlers[state.stage]
                with self.tracer.span(f"stage.{state.stage.value}", feature_id=feature_id):
                    try:
                        next_stage = handler(state, ctx)
                    except Exception as exc:  # noqa: BLE001 - any failure must be recorded, never swallowed
                        state.error = f"{state.stage.value}: {exc}"
                        ctx.events.emit(EVENT_ERROR, "orchestrator", state.error)
                        self._transition(state, ctx, Stage.FAILED, note=str(exc)[:200])
                        break
                self._transition(state, ctx, next_stage)
            return state

    def approve(self, feature_id: str, checkpoint: str, request: ApprovalRequest) -> WorkflowState:
        """Record a human decision at a pause point and move the workflow on (does not run it)."""
        with self._lock(feature_id):
            state = self.load(feature_id)
            ctx = self._context(state)
            expected = {
                "design": Stage.AWAITING_DESIGN_APPROVAL,
                "remediation": Stage.AWAITING_REMEDIATION_APPROVAL,
                "release": Stage.AWAITING_RELEASE_APPROVAL,
            }
            if checkpoint not in expected:
                raise WorkflowError(f"unknown checkpoint {checkpoint}")
            if state.stage != expected[checkpoint]:
                raise WorkflowError(f"feature is at stage {state.stage.value}, not awaiting {checkpoint} approval")
            approval = Approval(checkpoint=checkpoint, decision=request.decision, approver=request.approver, comment=request.comment, iteration=state.iteration)  # type: ignore[arg-type]
            state.approvals.append(approval)
            ctx.events.emit(EVENT_HUMAN, request.approver, f"{checkpoint} {request.decision}: {request.comment or '(no comment)'}", **approval.model_dump())

            if checkpoint == "design":
                if request.decision == "approve":
                    self._transition(state, ctx, Stage.GENERATING_CODE, note="design approved")
                elif request.comment:
                    state.human_feedback.append(request.comment)
                    self._transition(state, ctx, Stage.DESIGNING, note="design rejected with feedback - redesigning")
                else:
                    self._transition(state, ctx, Stage.REJECTED, note="design rejected")
            elif checkpoint == "remediation":
                if request.decision == "approve":
                    self._apply_proposal(state, ctx, approved_by=request.approver)
                    self._transition(state, ctx, Stage.REVALIDATING, note="correction applied")
                else:
                    state.remediation_history.append(f"iteration {state.iteration}: proposal rejected by {request.approver}: {request.comment}")
                    state.remediation_proposal = None
                    self._transition(state, ctx, Stage.TRACEABILITY_CHECK, note="correction rejected - escalating to release assessment")
            else:  # release
                self._transition(state, ctx, Stage.COMPLETED if request.decision == "approve" else Stage.REJECTED, note=f"release {request.decision}d")
            return state

    # ----------------------------------------------------------- stage handlers
    def _h_received(self, state: WorkflowState, ctx: _Context) -> Stage:
        return Stage.ANALYZING_REQUIREMENT

    def _h_analyze(self, state: WorkflowState, ctx: _Context) -> Stage:
        state.requirement_analysis = BusinessAnalystAgent(ctx.llm).run(state)
        return Stage.REQUIREMENT_GATE

    def _h_requirement_gate(self, state: WorkflowState, ctx: _Context) -> Stage:
        result = self._evaluate(state, ctx, "requirement")
        if result.passed:
            return Stage.DESIGNING
        retries = sum(1 for t in state.transitions if t.from_stage == Stage.REQUIREMENT_GATE and t.to_stage == Stage.ANALYZING_REQUIREMENT)
        if retries < 1:
            state.human_feedback.append("Requirement gate failed: " + "; ".join(c.evidence for c in result.checks if not c.passed))
            return Stage.ANALYZING_REQUIREMENT
        raise WorkflowError("requirement gate failed after retry: " + "; ".join(c.evidence for c in result.checks if not c.passed))

    def _h_design(self, state: WorkflowState, ctx: _Context) -> Stage:
        state.technical_design = SolutionDesignAgent(ctx.llm).run(state)
        return Stage.AWAITING_DESIGN_APPROVAL

    def _h_generate_code(self, state: WorkflowState, ctx: _Context) -> Stage:
        state.code_bundle = DeveloperAgent(ctx.llm).run(state)
        written = ctx.workspace.write_files(state.code_bundle.files)
        ctx.events.emit(EVENT_TOOL, "workspace", f"wrote {len(written)} application file(s) to isolated workspace", files=written)
        return Stage.GENERATING_TESTS

    def _h_generate_tests(self, state: WorkflowState, ctx: _Context) -> Stage:
        state.test_bundle = TestEngineerAgent(ctx.llm).run(state)
        written = ctx.workspace.write_files(state.test_bundle.files)
        ctx.events.emit(EVENT_TOOL, "workspace", f"wrote {len(written)} test file(s) to isolated workspace", files=written)
        return Stage.EXECUTING_TESTS

    def _h_execute_tests(self, state: WorkflowState, ctx: _Context) -> Stage:
        self._run_tool(state, ctx, lambda: run_build(ctx.workspace, timeout=self.settings.tool_timeout_seconds))
        self._run_tool(state, ctx, lambda: run_pytest(ctx.workspace, self._ac_mapping(state), timeout=self.settings.tool_timeout_seconds))
        return Stage.STATIC_ANALYSIS

    def _h_static_analysis(self, state: WorkflowState, ctx: _Context) -> Stage:
        prefixes = tuple(self.gates.cfg.gate("code").get("critical_ruff_prefixes", ["E9", "F63", "F7", "F82"]))
        self._run_tool(state, ctx, lambda: run_ruff(ctx.workspace, prefixes, timeout=self.settings.tool_timeout_seconds))
        self._run_tool(state, ctx, lambda: run_ast_analysis(ctx.workspace))
        return Stage.SECURITY_VALIDATION

    def _h_security_validation(self, state: WorkflowState, ctx: _Context) -> Stage:
        self._run_tool(state, ctx, lambda: run_bandit(ctx.workspace, timeout=self.settings.tool_timeout_seconds))
        self._run_tool(state, ctx, lambda: run_secret_scan(ctx.workspace))
        self._run_tool(state, ctx, lambda: run_pip_audit(ctx.workspace, self.settings.enable_pip_audit, timeout=self.settings.tool_timeout_seconds))
        return Stage.AI_CODE_REVIEW

    def _h_ai_review(self, state: WorkflowState, ctx: _Context) -> Stage:
        self._ai_reviews(state, ctx)
        return Stage.EVALUATING_GATES

    def _h_evaluate_gates(self, state: WorkflowState, ctx: _Context) -> Stage:
        results = [self._evaluate(state, ctx, g) for g in ("code", "test", "security")]
        self._snapshot(state, ctx)
        if all(r.passed for r in results):
            return Stage.TRACEABILITY_CHECK
        failed = [r.gate for r in results if not r.passed]
        if state.iteration >= self.gates.cfg.max_iterations:
            state.remediation_history.append(f"remediation budget exhausted ({state.iteration}/{self.gates.cfg.max_iterations}); gates still failing: {failed}")
            ctx.events.emit(EVENT_POLICY, "orchestrator", f"remediation budget exhausted; escalating with failed gates {failed}")
            return Stage.TRACEABILITY_CHECK
        ctx.events.emit(EVENT_GATE, "orchestrator", f"gates failed {failed} - routing to defect remediation (iteration {state.iteration + 1})")
        return Stage.REMEDIATING

    def _h_remediate(self, state: WorkflowState, ctx: _Context) -> Stage:
        state.iteration += 1
        blocking = [f for f in state.open_findings() if f.severity in (Severity.CRITICAL, Severity.HIGH)]
        failed_runs = [r for r in state.latest_tool_runs().values() if r.status in ("failed", "error")]
        proposal = DefectResolutionAgent(ctx.llm).run(state, failures=blocking, failed_runs=failed_runs)
        state.remediation_proposal = proposal
        if not proposal.changes:
            state.remediation_history.append(f"iteration {state.iteration}: no safe automated fix ({proposal.confidence} confidence) - {proposal.probable_cause}")
            ctx.events.emit(EVENT_POLICY, "defect_resolver", "no automated correction proposed - escalating to manual review")
            return Stage.TRACEABILITY_CHECK
        # Record every proposed modification with its diff BEFORE anything is applied (FR-17/FR-18).
        for change in proposal.changes:
            state.modifications.append(
                ModificationRecord(
                    id=f"MOD-{len(state.modifications) + 1:02d}",
                    iteration=state.iteration,
                    file=change.path,
                    diff=ctx.workspace.diff(change.path, change.content),
                    reason=proposal.rationale,
                    before_hash=ctx.workspace.file_hash(change.path),
                )
            )
        ctx.events.emit(EVENT_MODIFICATION, "defect_resolver", f"proposed correction for {proposal.affected_files} addressing {proposal.addressed_finding_ids}", confidence=proposal.confidence, files=proposal.affected_files)
        if self.gates.cfg.require_remediation_approval:
            return Stage.AWAITING_REMEDIATION_APPROVAL
        self._apply_proposal(state, ctx, approved_by="auto-policy")
        return Stage.REVALIDATING

    def _h_revalidate(self, state: WorkflowState, ctx: _Context) -> Stage:
        before = state.snapshots[-1] if state.snapshots else None
        self._h_execute_tests(state, ctx)
        self._h_static_analysis(state, ctx)
        self._h_security_validation(state, ctx)
        if self.gates.cfg.rerun_ai_review:
            self._ai_reviews(state, ctx)
        self._resolve_addressed_findings(state)
        after = self._snapshot(state, ctx, record=False)
        if before:
            comparison = (
                f"iteration {state.iteration}: tests {before.tests_passed}p/{before.tests_failed}f -> {after.tests_passed}p/{after.tests_failed}f; "
                f"findings {before.findings_by_severity} -> {after.findings_by_severity}"
            )
            state.remediation_history.append(comparison)
            ctx.events.emit(EVENT_POLICY, "orchestrator", "before/after comparison: " + comparison, before=before.model_dump(), after=after.model_dump())
        return Stage.EVALUATING_GATES

    def _h_traceability(self, state: WorkflowState, ctx: _Context) -> Stage:
        state.traceability = build_traceability(state)
        self._evaluate(state, ctx, "traceability")
        return Stage.RELEASE_ASSESSMENT

    def _h_release_assessment(self, state: WorkflowState, ctx: _Context) -> Stage:
        self._evaluate(state, ctx, "release")
        assessment = ReleaseAssuranceAgent(ctx.llm).run(state)
        assessment = self.gates.apply_release_policy(assessment, state)
        state.release_assessment = assessment
        ctx.events.emit(
            EVENT_POLICY,
            "release_policy",
            f"release recommendation {assessment.decision.value}" + (f" (policy override: {assessment.policy_override})" if assessment.policy_override else ""),
            decision=assessment.decision.value,
            override=assessment.policy_override,
        )
        return Stage.AWAITING_RELEASE_APPROVAL

    # ---------------------------------------------------------------- helpers
    def _evaluate(self, state: WorkflowState, ctx: _Context, gate: str):
        result = self.gates.evaluate(gate, state)
        state.gate_results.append(result)
        ctx.events.emit(
            EVENT_GATE,
            "gate_engine",
            f"{gate} gate {'PASSED' if result.passed else 'FAILED'}: " + "; ".join(f"{c.name}={'ok' if c.passed else 'FAIL'}" for c in result.checks),
            **result.model_dump(),
        )
        self.store.save_state(state)
        return result

    def _run_tool(self, state: WorkflowState, ctx: _Context, fn: Callable[[], ToolRun]) -> ToolRun:
        run = fn()
        for f in run.findings:
            f.iteration = state.iteration
        state.tool_runs.append(run)
        self._merge_findings(state, run.tool, run.findings)
        ctx.events.emit(EVENT_TOOL, run.tool, f"{run.tool} {run.status}: {run.summary} ({run.duration_seconds}s)", command=run.command, status=run.status, metrics=run.metrics, findings=[f.id for f in run.findings])
        self.store.save_state(state)
        return run

    def _ai_reviews(self, state: WorkflowState, ctx: _Context) -> None:
        state.review_report = CodeReviewerAgent(ctx.llm).run(state)
        for f in state.review_report.findings:
            f.source, f.iteration = "ai_review", state.iteration
        self._merge_findings(state, "ai_review", state.review_report.findings)
        state.security_report = SecurityReviewAgent(ctx.llm).run(state)
        for f in state.security_report.findings:
            f.source, f.iteration = "security_agent", state.iteration
        self._merge_findings(state, "security_agent", state.security_report.findings)
        self.store.save_state(state)

    @staticmethod
    def _finding_key(f: Finding) -> tuple[str, str, str, str]:
        return (f.source, f.category, f.file, f.finding[:80])

    def _merge_findings(self, state: WorkflowState, source: str, new: list[Finding]) -> None:
        """Reconcile a tool/agent's fresh findings with the running list: findings that are
        no longer detected become *resolved*, re-detected ones keep their id, new ones are added."""
        new_keys = {self._finding_key(f) for f in new}
        existing_ids = {f.id for f in state.findings}
        for f in state.findings:
            if f.source == source and f.status == "open" and self._finding_key(f) not in new_keys:
                f.status = "resolved"
        open_keys = {self._finding_key(f) for f in state.findings if f.source == source and f.status == "open"}
        for f in new:
            if self._finding_key(f) in open_keys:
                continue
            if f.id in existing_ids:
                f.id = f"{f.id}@{state.iteration}"
            existing_ids.add(f.id)
            state.findings.append(f)

    def _resolve_addressed_findings(self, state: WorkflowState) -> None:
        """Findings the remediation claimed to address are only closed if the tools no longer
        detect them (handled by _merge_findings).  AI-review findings are re-evaluated only when
        the AI review re-runs; otherwise the claim is recorded but the finding stays open."""
        if not state.remediation_proposal:
            return
        state.remediation_history.append(
            f"iteration {state.iteration}: applied fix for {state.remediation_proposal.affected_files}; claimed to address {state.remediation_proposal.addressed_finding_ids}"
        )

    def _apply_proposal(self, state: WorkflowState, ctx: _Context, approved_by: str) -> None:
        proposal = state.remediation_proposal
        if proposal is None:
            raise WorkflowError("no remediation proposal to apply")
        ctx.workspace.snapshot(f"before-iteration-{state.iteration}")
        ctx.workspace.write_files(proposal.changes)
        now = utcnow()
        for mod in state.modifications:
            if mod.iteration == state.iteration and not mod.applied_at:
                mod.approved_by = approved_by
                mod.applied_at = now
                mod.after_hash = ctx.workspace.file_hash(mod.file)
        # keep the recorded code bundle in sync with the workspace so later prompts see the fix
        if state.code_bundle:
            by_path = {f.path: f for f in state.code_bundle.files}
            for change in proposal.changes:
                if change.path in by_path:
                    by_path[change.path].content = change.content
                    if change.implements:
                        by_path[change.path].implements = sorted(set(by_path[change.path].implements) | set(change.implements))
                elif not change.path.startswith("tests"):
                    state.code_bundle.files.append(GeneratedFile(path=change.path, content=change.content, implements=change.implements))
        if state.test_bundle:
            by_path = {f.path: f for f in state.test_bundle.files}
            for change in proposal.changes:
                if change.path in by_path:
                    by_path[change.path].content = change.content
        ctx.events.emit(EVENT_MODIFICATION, approved_by, f"applied AI correction to {proposal.affected_files} (iteration {state.iteration})", files=proposal.affected_files)
        self.store.save_state(state)

    def _snapshot(self, state: WorkflowState, ctx: _Context, record: bool = True) -> ValidationSnapshot:
        run = state.latest_tool_runs().get("pytest")
        by_sev: dict[str, int] = {}
        for f in state.open_findings():
            by_sev[f.severity.value] = by_sev.get(f.severity.value, 0) + 1
        snap = ValidationSnapshot(
            iteration=state.iteration,
            tests_passed=run.metrics.get("passed", 0) if run else 0,
            tests_failed=run.metrics.get("failed", 0) if run else 0,
            findings_by_severity=dict(sorted(by_sev.items())),
            gate_status={g: r.passed for g, r in state.latest_gate_results().items()},
        )
        if record:
            state.snapshots.append(snap)
        return snap

    @staticmethod
    def _ac_mapping(state: WorkflowState) -> dict[str, list[str]]:
        if not state.test_bundle:
            return {}
        return {tc.name: tc.acceptance_criteria_ids for tc in state.test_bundle.test_cases}


class _Context:
    def __init__(self, events: EventLog, llm: LLMClient, workspace: Workspace):
        self.events = events
        self.llm = llm
        self.workspace = workspace
