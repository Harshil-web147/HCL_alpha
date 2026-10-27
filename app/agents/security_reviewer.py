"""Agent 6 - Security Review Agent (UC-06)."""

from __future__ import annotations

from typing import Any

from app.agents.base import Agent, code_block, dumps, feature_block
from app.models import SecurityReport, WorkflowState


class SecurityReviewAgent(Agent[SecurityReport]):
    name = "security_reviewer"
    role = "Security Review Agent"
    schema = SecurityReport

    def instructions(self) -> str:
        return """
Perform a security review of the proposed implementation. Check: authentication controls,
authorization checks (ownership / role enforcement on every path), input validation, secrets
exposure (hard-coded keys, tokens, passwords), SQL injection, command injection, unsafe
deserialization, dependency vulnerabilities, logging of sensitive information, insecure
configuration (debug flags, permissive CORS, binding to 0.0.0.0, weak crypto).
Each finding MUST contain Severity | Component | Finding | Evidence (file:line + code) |
Recommended Fix, ids SEC-01, SEC-02 ..., source="security_agent" and a category from:
authentication_controls, authorization_checks, input_validation, secrets_exposure, sql_injection,
command_injection, unsafe_deserialization, dependency_vulnerability,
logging_sensitive_information, insecure_configuration.
Severity CRITICAL/HIGH blocks release readiness - use them only for exploitable weaknesses.
The deterministic scanner results below are authoritative evidence; corroborate rather than
contradict them, and add anything they cannot see (business-logic authorization, data exposure).
"""

    def user_prompt(self, state: WorkflowState, **kwargs: Any) -> str:
        scanner_findings = [f for f in state.open_findings() if f.source in {"bandit", "secrets", "pip_audit"}]
        return (
            feature_block(state)
            + "\nTECHNICAL DESIGN (security considerations)\n"
            + dumps(state.technical_design.security if state.technical_design else [])
            + "\n\nDETERMINISTIC SCANNER FINDINGS\n"
            + (dumps([f.model_dump() for f in scanner_findings]) if scanner_findings else "(none)")
            + "\n\nPROPOSED IMPLEMENTATION\n"
            + code_block(state)
            + "\n\nProduce the SecurityReport artifact."
        )
