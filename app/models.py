"""Pydantic schemas for every artifact that flows between agents.

These schemas are the contract: every LLM call is forced to return JSON that
validates against one of them, which is what turns "Prompt -> LLM -> text"
into typed artifacts the deterministic tooling can act on.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Intake
# --------------------------------------------------------------------------- #
class FeatureRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    description: str = Field(..., min_length=10)
    context: str = Field("", description="Optional extra context: existing system, constraints, stakeholders")
    requested_by: str = "developer"
    language: Literal["python"] = "python"
    framework: Literal["fastapi"] = "fastapi"


# --------------------------------------------------------------------------- #
# UC-01 Requirement Intelligence
# --------------------------------------------------------------------------- #
class Requirement(BaseModel):
    id: str = Field(..., description="e.g. FR-01 / NFR-01")
    description: str
    priority: Literal["must", "should", "could"] = "must"
    rationale: str = ""


class BusinessRule(BaseModel):
    id: str = Field(..., description="e.g. BR-01")
    description: str


class UserStory(BaseModel):
    id: str = Field(..., description="e.g. US-01")
    role: str
    goal: str
    benefit: str
    requirement_ids: list[str] = Field(default_factory=list)


class AcceptanceCriterion(BaseModel):
    id: str = Field(..., description="e.g. AC-01")
    story_id: str
    given: str
    when: str
    then: str
    measurable: bool = True


class Assumption(BaseModel):
    id: str = Field(..., description="e.g. AS-01")
    description: str
    needs_confirmation: bool = True


class Ambiguity(BaseModel):
    id: str = Field(..., description="e.g. AMB-01")
    description: str
    impact: Literal["high", "medium", "low"] = "medium"
    clarification_question: str
    proposed_default: str = ""


class RequirementAnalysis(BaseModel):
    summary: str
    functional_requirements: list[Requirement]
    non_functional_requirements: list[Requirement]
    business_rules: list[BusinessRule]
    user_stories: list[UserStory]
    acceptance_criteria: list[AcceptanceCriterion]
    assumptions: list[Assumption] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    technical_questions: list[str] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# UC-02 Technical Design
# --------------------------------------------------------------------------- #
class Component(BaseModel):
    name: str
    responsibility: str
    requirement_ids: list[str] = Field(default_factory=list)


class ApiEndpoint(BaseModel):
    method: str
    path: str
    description: str
    request_schema: dict[str, Any] = Field(default_factory=dict)
    response_schema: dict[str, Any] = Field(default_factory=dict)
    error_responses: list[dict[str, Any]] = Field(default_factory=list)
    acceptance_criteria_ids: list[str] = Field(default_factory=list)


class DataField(BaseModel):
    name: str
    type: str
    required: bool = True
    constraints: str = ""


class DataEntity(BaseModel):
    name: str
    fields: list[DataField]


class FilePlan(BaseModel):
    path: str
    purpose: str
    implements: list[str] = Field(default_factory=list, description="Requirement / AC ids this file realises")


class TechnicalDesign(BaseModel):
    overview: str
    components: list[Component]
    component_flow: list[str] = Field(default_factory=list, description="Ordered component names")
    api: list[ApiEndpoint]
    data_model: list[DataEntity]
    validation_rules: list[str] = Field(default_factory=list)
    error_handling: str = ""
    logging: str = ""
    security: list[str] = Field(default_factory=list)
    file_plan: list[FilePlan] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# UC-03 / UC-04 Code & Tests
# --------------------------------------------------------------------------- #
class GeneratedFile(BaseModel):
    path: str = Field(..., description="Path relative to the workspace root, e.g. app/main.py")
    content: str
    implements: list[str] = Field(default_factory=list, description="Requirement / AC ids this file realises")


class CodeBundle(BaseModel):
    language: str = "python"
    framework: str = "fastapi"
    files: list[GeneratedFile]
    run_instructions: str = ""
    notes: str = ""


class TestCase(BaseModel):
    name: str = Field(..., description="pytest function name")
    category: Literal["positive", "negative", "boundary", "validation", "authorization", "exception", "api"]
    acceptance_criteria_ids: list[str] = Field(default_factory=list)
    description: str = ""


class TestBundle(BaseModel):
    files: list[GeneratedFile]
    test_cases: list[TestCase]


# --------------------------------------------------------------------------- #
# Findings (shared by review, security, scanners)
# --------------------------------------------------------------------------- #
class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


SEVERITY_ORDER = {s: i for i, s in enumerate([Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO])}


class Finding(BaseModel):
    id: str
    severity: Severity
    component: str
    finding: str
    evidence: str
    recommended_fix: str
    source: str = Field(..., description="ai_review | security_agent | bandit | ruff | secrets | ast | pytest | pip_audit | build")
    category: str = ""
    file: str = ""
    line: int | None = None
    status: Literal["open", "resolved", "accepted"] = "open"
    iteration: int = 0


class ReviewReport(BaseModel):
    summary: str
    findings: list[Finding]
    verdict: Literal["approve", "request_changes", "comment"] = "comment"


class SecurityReport(BaseModel):
    summary: str
    findings: list[Finding]


# --------------------------------------------------------------------------- #
# Deterministic tool results
# --------------------------------------------------------------------------- #
class TestResult(BaseModel):
    __test__: ClassVar[bool] = False  # not a pytest test class

    name: str
    outcome: Literal["passed", "failed", "error", "skipped"]
    duration: float = 0.0
    message: str = ""
    acceptance_criteria_ids: list[str] = Field(default_factory=list)


class ToolRun(BaseModel):
    tool: str
    status: Literal["passed", "failed", "error", "skipped"]
    summary: str
    started_at: str = Field(default_factory=utcnow)
    duration_seconds: float = 0.0
    command: str = ""
    raw_output: str = ""
    findings: list[Finding] = Field(default_factory=list)
    test_results: list[TestResult] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# UC-07 Remediation
# --------------------------------------------------------------------------- #
class RemediationProposal(BaseModel):
    failure_summary: str
    probable_cause: str
    affected_files: list[str]
    changes: list[GeneratedFile]
    addressed_finding_ids: list[str] = Field(default_factory=list)
    rationale: str = ""
    confidence: Literal["high", "medium", "low"] = "medium"


class ModificationRecord(BaseModel):
    id: str
    iteration: int
    file: str
    diff: str
    reason: str
    proposed_at: str = Field(default_factory=utcnow)
    approved_by: str = ""
    applied_at: str = ""
    before_hash: str = ""
    after_hash: str = ""


# --------------------------------------------------------------------------- #
# Quality gates & release
# --------------------------------------------------------------------------- #
class GateCheck(BaseModel):
    name: str
    passed: bool
    evidence: str


class GateResult(BaseModel):
    gate: str
    mandatory: bool
    passed: bool
    checks: list[GateCheck]
    evaluated_at: str = Field(default_factory=utcnow)
    iteration: int = 0


class ReleaseDecision(str, Enum):
    READY = "READY"
    CONDITIONAL = "CONDITIONAL"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    NOT_READY = "NOT_READY"


class ReleaseAssessment(BaseModel):
    decision: ReleaseDecision
    rationale: str
    evidence: list[str]
    conditions: list[str] = Field(default_factory=list)
    residual_risks: list[str] = Field(default_factory=list)
    policy_override: str = Field("", description="Set when the deterministic policy overrode the agent's decision")


class Approval(BaseModel):
    checkpoint: Literal["design", "remediation", "release"]
    decision: Literal["approve", "reject"]
    approver: str
    comment: str = ""
    recorded_at: str = Field(default_factory=utcnow)
    iteration: int = 0


class ApprovalRequest(BaseModel):
    decision: Literal["approve", "reject"]
    approver: str = "developer"
    comment: str = ""


# --------------------------------------------------------------------------- #
# Workflow state
# --------------------------------------------------------------------------- #
class Stage(str, Enum):
    RECEIVED = "received"
    ANALYZING_REQUIREMENT = "analyzing_requirement"
    REQUIREMENT_GATE = "requirement_gate"
    DESIGNING = "designing"
    AWAITING_DESIGN_APPROVAL = "awaiting_design_approval"
    GENERATING_CODE = "generating_code"
    GENERATING_TESTS = "generating_tests"
    EXECUTING_TESTS = "executing_tests"
    STATIC_ANALYSIS = "static_analysis"
    SECURITY_VALIDATION = "security_validation"
    AI_CODE_REVIEW = "ai_code_review"
    EVALUATING_GATES = "evaluating_gates"
    REMEDIATING = "remediating"
    AWAITING_REMEDIATION_APPROVAL = "awaiting_remediation_approval"
    REVALIDATING = "revalidating"
    TRACEABILITY_CHECK = "traceability_check"
    RELEASE_ASSESSMENT = "release_assessment"
    AWAITING_RELEASE_APPROVAL = "awaiting_release_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


PAUSE_STAGES = {
    Stage.AWAITING_DESIGN_APPROVAL,
    Stage.AWAITING_REMEDIATION_APPROVAL,
    Stage.AWAITING_RELEASE_APPROVAL,
}
TERMINAL_STAGES = {Stage.COMPLETED, Stage.REJECTED, Stage.FAILED}


class StageTransition(BaseModel):
    from_stage: Stage
    to_stage: Stage
    at: str = Field(default_factory=utcnow)
    note: str = ""


class ValidationSnapshot(BaseModel):
    """Before/after comparison material for the remediation loop."""

    iteration: int
    tests_passed: int = 0
    tests_failed: int = 0
    findings_by_severity: dict[str, int] = Field(default_factory=dict)
    gate_status: dict[str, bool] = Field(default_factory=dict)
    captured_at: str = Field(default_factory=utcnow)


class TraceabilityRow(BaseModel):
    requirement_id: str
    story_ids: list[str] = Field(default_factory=list)
    acceptance_criteria_ids: list[str] = Field(default_factory=list)
    design_components: list[str] = Field(default_factory=list)
    code_files: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    test_outcomes: dict[str, str] = Field(default_factory=dict)


class TraceabilityMatrix(BaseModel):
    rows: list[TraceabilityRow]
    requirement_coverage: float = Field(0.0, description="% of requirements mapped to code")
    acceptance_criteria_coverage: float = Field(0.0, description="% of AC mapped to at least one test")
    tests_with_results: float = Field(0.0, description="% of mapped tests with an execution result")
    unmapped_requirements: list[str] = Field(default_factory=list)
    unmapped_acceptance_criteria: list[str] = Field(default_factory=list)


class WorkflowState(BaseModel):
    feature_id: str
    request: FeatureRequest
    stage: Stage = Stage.RECEIVED
    iteration: int = 0
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)
    error: str = ""

    # artifacts
    requirement_analysis: RequirementAnalysis | None = None
    technical_design: TechnicalDesign | None = None
    code_bundle: CodeBundle | None = None
    test_bundle: TestBundle | None = None
    review_report: ReviewReport | None = None
    security_report: SecurityReport | None = None
    remediation_proposal: RemediationProposal | None = None
    release_assessment: ReleaseAssessment | None = None
    traceability: TraceabilityMatrix | None = None

    # deterministic evidence
    tool_runs: list[ToolRun] = Field(default_factory=list)
    gate_results: list[GateResult] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    modifications: list[ModificationRecord] = Field(default_factory=list)
    approvals: list[Approval] = Field(default_factory=list)
    transitions: list[StageTransition] = Field(default_factory=list)
    snapshots: list[ValidationSnapshot] = Field(default_factory=list)
    remediation_history: list[str] = Field(default_factory=list)
    human_feedback: list[str] = Field(default_factory=list, description="Comments from rejected approvals, fed back to agents")

    # ------------------------------------------------------------------ helpers
    def latest_tool_runs(self) -> dict[str, ToolRun]:
        """Most recent run per tool (later runs supersede earlier ones)."""
        latest: dict[str, ToolRun] = {}
        for run in self.tool_runs:
            latest[run.tool] = run
        return latest

    def latest_gate_results(self) -> dict[str, GateResult]:
        latest: dict[str, GateResult] = {}
        for g in self.gate_results:
            latest[g.gate] = g
        return latest

    def open_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.status == "open"]

    def is_paused(self) -> bool:
        return self.stage in PAUSE_STAGES

    def is_terminal(self) -> bool:
        return self.stage in TERMINAL_STAGES


class FeatureSummary(BaseModel):
    feature_id: str
    title: str
    stage: Stage
    iteration: int
    decision: ReleaseDecision | None = None
    created_at: str
    updated_at: str
    open_findings: int = 0
    gates_passed: int = 0
    gates_total: int = 0
