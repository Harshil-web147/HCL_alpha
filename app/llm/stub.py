"""Deterministic stand-in for the LLM.

``LLM_PROVIDER=stub`` makes the whole platform runnable with no API key: every
agent receives a realistic artifact for the reference feature from the hackathon
brief ("customers update their contact information").  The stub is deliberately
*reactive* in the two places where the real model has to reason about its input:

* the Security Review agent only reports the hard-coded signing secret while it
  is still present in the code it is shown, and
* the Defect Resolution agent proposes the matching fix only when that finding is
  in the failures it is asked to remediate.

That is enough to exercise every stage of the workflow - including the
remediation loop, human approvals and the release policy - end to end, which is
how the platform's own test-suite runs in CI.  The generated application lives in
``stub_assets/contact_service`` and is itself a working FastAPI service with a
passing pytest suite.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

ASSETS = Path(__file__).parent / "stub_assets" / "contact_service"
PLANTED_SECRET_PATTERN = re.compile(r'JWT_SECRET\s*=\s*"hardcoded')


def _read(rel: str) -> str:
    return (ASSETS / rel).read_text(encoding="utf-8")


def _title_from_prompt(prompt: str) -> str:
    m = re.search(r"Title:\s*(.+)", prompt)
    return m.group(1).strip() if m else "Customer contact update API"


class StubProvider:
    name = "stub"

    def complete_json(self, *, agent: str, system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]:
        handler = getattr(self, f"_{schema.__name__}", None)
        if handler is None:
            raise ValueError(f"StubProvider has no canned response for schema {schema.__name__}")
        return handler(user)

    # ------------------------------------------------------------------ UC-01
    def _RequirementAnalysis(self, prompt: str) -> dict[str, Any]:
        return {
            "summary": (
                f"{_title_from_prompt(prompt)}: expose an authenticated API that lets a customer replace "
                "their contact information (email, phone, postal address), validates mandatory fields, "
                "rejects modification by anyone other than the owner, writes an immutable audit record for "
                "every change and returns a consistent machine-readable error envelope."
            ),
            "functional_requirements": [
                {"id": "FR-01", "description": "Expose an API endpoint that updates a customer's contact information (email, phone, address).", "priority": "must", "rationale": "Core feature request."},
                {"id": "FR-02", "description": "Validate mandatory fields (email, phone, address line 1, city, postal code, country) and their formats before persisting.", "priority": "must", "rationale": "Explicitly requested."},
                {"id": "FR-03", "description": "Authenticate the caller and only allow a customer to modify their own contact information.", "priority": "must", "rationale": "Prevent unauthorized modification."},
                {"id": "FR-04", "description": "Record an audit entry (actor, timestamp, request id, before/after state) for every successful update.", "priority": "must", "rationale": "Maintain an audit record."},
                {"id": "FR-05", "description": "Return appropriate HTTP status codes (401, 403, 404, 422) with a uniform error envelope.", "priority": "must", "rationale": "Appropriate API error responses."},
            ],
            "non_functional_requirements": [
                {"id": "NFR-01", "description": "Security: credentials and signing keys must come from configuration, not source; logs must not contain personal data.", "priority": "must"},
                {"id": "NFR-02", "description": "Audit records are append-only and cannot be modified through the API.", "priority": "must"},
                {"id": "NFR-03", "description": "Performance: p95 latency for the update endpoint under 300 ms at 100 req/s.", "priority": "should"},
                {"id": "NFR-04", "description": "Logging/observability: every request carries a request id that is logged and stored with the audit entry.", "priority": "should"},
            ],
            "business_rules": [
                {"id": "BR-01", "description": "A customer may only update their own contact information."},
                {"id": "BR-02", "description": "Email, phone, address line 1, city, postal code and country are mandatory; the update is a full replacement."},
                {"id": "BR-03", "description": "Audit entries are immutable once written."},
            ],
            "user_stories": [
                {"id": "US-01", "role": "customer", "goal": "update my contact information through the API", "benefit": "my records stay current without contacting support", "requirement_ids": ["FR-01", "FR-02"]},
                {"id": "US-02", "role": "security officer", "goal": "have unauthenticated or non-owner update attempts rejected", "benefit": "customer data cannot be tampered with", "requirement_ids": ["FR-03"]},
                {"id": "US-03", "role": "compliance officer", "goal": "see who changed which contact fields and when", "benefit": "regulatory audit requests can be answered", "requirement_ids": ["FR-04"]},
                {"id": "US-04", "role": "API consumer", "goal": "receive consistent, descriptive error responses", "benefit": "client applications can handle failures predictably", "requirement_ids": ["FR-05"]},
            ],
            "acceptance_criteria": [
                {"id": "AC-01", "story_id": "US-01", "given": "an authenticated customer with a valid payload", "when": "they PUT their contact information", "then": "the API returns 200 with the updated record and the change is persisted"},
                {"id": "AC-02", "story_id": "US-01", "given": "a payload with a missing or malformed mandatory field", "when": "the customer submits the update", "then": "the API returns 422 listing each invalid field and nothing is persisted"},
                {"id": "AC-03", "story_id": "US-02", "given": "a request without a valid bearer token", "when": "the update is attempted", "then": "the API returns 401 UNAUTHENTICATED"},
                {"id": "AC-04", "story_id": "US-02", "given": "a valid token belonging to a different customer", "when": "the update is attempted", "then": "the API returns 403 FORBIDDEN and the target record is unchanged"},
                {"id": "AC-05", "story_id": "US-03", "given": "a successful update", "when": "the audit history is read", "then": "exactly one entry exists with actor, request id, timestamp and before/after contact state"},
                {"id": "AC-06", "story_id": "US-04", "given": "any failing request (unknown customer, validation, auth)", "when": "the error is returned", "then": "the body is {error:{code,message,details}} with the matching HTTP status (404 for unknown customers)"},
            ],
            "assumptions": [
                {"id": "AS-01", "description": "Customer records already exist; customer creation is out of scope.", "needs_confirmation": True},
                {"id": "AS-02", "description": "Bearer tokens are issued by the existing identity provider; this service only validates them.", "needs_confirmation": True},
                {"id": "AS-03", "description": "The update is a full replacement of contact details (no PATCH / partial update).", "needs_confirmation": True},
                {"id": "AS-04", "description": "An in-memory repository is acceptable for the prototype; production will use PostgreSQL.", "needs_confirmation": False},
            ],
            "dependencies": ["Identity provider (token issuance)", "Customer master data store", "Audit log storage / retention policy"],
            "technical_questions": [
                "Should email changes trigger a verification flow before taking effect?",
                "Is rate limiting required on authentication failures?",
                "What is the retention period for audit records?",
            ],
            "ambiguities": [
                {"id": "AMB-01", "description": "The request does not list which contact fields are mandatory.", "impact": "high", "clarification_question": "Which of email, phone, address line 2 are mandatory?", "proposed_default": "email, phone, line1, city, postal_code, country mandatory; line2 optional."},
                {"id": "AMB-02", "description": "It is unclear whether support agents or admins may update on a customer's behalf.", "impact": "high", "clarification_question": "Are there privileged roles that may modify any customer's contact data?", "proposed_default": "Owner-only; a privileged role can be added later."},
                {"id": "AMB-03", "description": "Email uniqueness across customers is not specified.", "impact": "medium", "clarification_question": "Must an email be unique per customer?", "proposed_default": "Not enforced in this iteration."},
                {"id": "AMB-04", "description": "Who may read the audit trail is not specified.", "impact": "medium", "clarification_question": "Is the audit history visible to the customer, compliance only, or both?", "proposed_default": "Owner can read their own history."},
            ],
        }

    # ------------------------------------------------------------------ UC-02
    def _TechnicalDesign(self, prompt: str) -> dict[str, Any]:
        return {
            "overview": (
                "Layered FastAPI service: a thin API layer delegates to a ContactService that enforces ownership "
                "authorization, validates through Pydantic schemas, persists via a repository abstraction and "
                "records before/after state through an AuditService. Errors are raised as ApiError and rendered "
                "by central exception handlers into a uniform envelope."
            ),
            "components": [
                {"name": "Customer API", "responsibility": "HTTP routing, request parsing, response models (FastAPI)", "requirement_ids": ["FR-01", "FR-05"]},
                {"name": "Authentication", "responsibility": "Validate bearer tokens (HMAC-SHA256) and resolve the caller identity", "requirement_ids": ["FR-03"]},
                {"name": "Profile Service", "responsibility": "Business logic: ownership authorization, update orchestration, PII-safe logging", "requirement_ids": ["FR-01", "FR-03", "FR-04"]},
                {"name": "Validation Schemas", "responsibility": "Mandatory-field and format validation for the request payload", "requirement_ids": ["FR-02"]},
                {"name": "Repository", "responsibility": "Data-access layer for customer records (in-memory for the prototype)", "requirement_ids": ["FR-01"]},
                {"name": "Audit Service", "responsibility": "Append-only record of every contact change with before/after state", "requirement_ids": ["FR-04", "NFR-02"]},
                {"name": "Error Handling", "responsibility": "Map ApiError / validation errors to the standard error envelope", "requirement_ids": ["FR-05"]},
                {"name": "Configuration & Logging", "responsibility": "Environment-driven settings and masked structured logging", "requirement_ids": ["NFR-01", "NFR-04"]},
            ],
            "component_flow": ["UI", "Customer API", "Authentication", "Profile Service", "Repository", "Audit Service"],
            "api": [
                {
                    "method": "PUT", "path": "/customers/{customer_id}/contact",
                    "description": "Replace the customer's contact information.",
                    "request_schema": {"email": "string (RFC-5322-like)", "phone": "string (+?7-15 digits)", "address": {"line1": "string", "line2": "string?", "city": "string", "postal_code": "string", "country": "ISO-3166-1 alpha-2"}},
                    "response_schema": {"customer_id": "string", "email": "string", "phone": "string", "address": "Address", "updated_at": "ISO-8601"},
                    "error_responses": [{"status": 401, "code": "UNAUTHENTICATED"}, {"status": 403, "code": "FORBIDDEN"}, {"status": 404, "code": "CUSTOMER_NOT_FOUND"}, {"status": 422, "code": "VALIDATION_ERROR"}],
                    "acceptance_criteria_ids": ["AC-01", "AC-02", "AC-03", "AC-04", "AC-06"],
                },
                {
                    "method": "GET", "path": "/customers/{customer_id}/contact",
                    "description": "Read the customer's current contact information.",
                    "request_schema": {}, "response_schema": {"customer_id": "string", "email": "string", "phone": "string", "address": "Address", "updated_at": "ISO-8601"},
                    "error_responses": [{"status": 401, "code": "UNAUTHENTICATED"}, {"status": 403, "code": "FORBIDDEN"}, {"status": 404, "code": "CUSTOMER_NOT_FOUND"}],
                    "acceptance_criteria_ids": ["AC-01", "AC-04"],
                },
                {
                    "method": "GET", "path": "/customers/{customer_id}/contact/audit",
                    "description": "List audit entries for the customer's contact changes (owner only).",
                    "request_schema": {}, "response_schema": {"items": "AuditEntry[]"},
                    "error_responses": [{"status": 401, "code": "UNAUTHENTICATED"}, {"status": 403, "code": "FORBIDDEN"}],
                    "acceptance_criteria_ids": ["AC-05"],
                },
            ],
            "data_model": [
                {"name": "Customer", "fields": [
                    {"name": "customer_id", "type": "string", "required": True, "constraints": "primary key"},
                    {"name": "email", "type": "string", "required": True, "constraints": "max 254, lower-cased"},
                    {"name": "phone", "type": "string", "required": True, "constraints": "+?[0-9]{7,15}"},
                    {"name": "address", "type": "Address", "required": True},
                    {"name": "updated_at", "type": "datetime", "required": True},
                ]},
                {"name": "Address", "fields": [
                    {"name": "line1", "type": "string", "required": True, "constraints": "1-120 chars"},
                    {"name": "line2", "type": "string", "required": False, "constraints": "<=120 chars"},
                    {"name": "city", "type": "string", "required": True, "constraints": "1-80 chars"},
                    {"name": "postal_code", "type": "string", "required": True, "constraints": "3-12 chars"},
                    {"name": "country", "type": "string", "required": True, "constraints": "ISO alpha-2, upper-cased"},
                ]},
                {"name": "AuditEntry", "fields": [
                    {"name": "audit_id", "type": "uuid", "required": True},
                    {"name": "customer_id", "type": "string", "required": True},
                    {"name": "actor_id", "type": "string", "required": True},
                    {"name": "action", "type": "string", "required": True, "constraints": "CONTACT_UPDATED"},
                    {"name": "before", "type": "json", "required": True},
                    {"name": "after", "type": "json", "required": True},
                    {"name": "request_id", "type": "string", "required": True},
                    {"name": "recorded_at", "type": "datetime", "required": True},
                ]},
            ],
            "validation_rules": [
                "email: mandatory, matches a basic RFC-5322 pattern, normalised to lower case",
                "phone: mandatory, optional leading +, 7-15 digits after removing spaces",
                "address.line1/city/postal_code/country: mandatory with length limits; country upper-cased ISO alpha-2",
                "Unknown fields are ignored; the payload is a full replacement",
            ],
            "error_handling": "Domain errors raise ApiError(status, code, message, details); a global handler renders {error:{code,message,details}}. Pydantic validation errors are converted to 422 VALIDATION_ERROR with one detail per field.",
            "logging": "Structured log line per update with masked email/phone and the request id; authorization denials logged at WARNING. No raw PII in logs.",
            "security": [
                "Bearer token validated with HMAC-SHA256 and constant-time comparison",
                "Ownership check in the service layer (actor_id == customer_id) before any read or write",
                "Signing key loaded from environment configuration, never from source",
                "Audit log is append-only; no API deletes or edits entries",
            ],
            "file_plan": [
                {"path": "app/main.py", "purpose": "FastAPI app factory and routes", "implements": ["FR-01", "FR-05", "AC-01", "AC-06"]},
                {"path": "app/auth.py", "purpose": "Bearer token verification", "implements": ["FR-03", "AC-03"]},
                {"path": "app/service.py", "purpose": "Business logic and ownership authorization", "implements": ["FR-01", "FR-03", "FR-04", "AC-04", "AC-05"]},
                {"path": "app/schemas.py", "purpose": "Request/response validation", "implements": ["FR-02", "AC-02"]},
                {"path": "app/repository.py", "purpose": "Data-access layer", "implements": ["FR-01", "FR-04"]},
                {"path": "app/audit.py", "purpose": "Audit record creation", "implements": ["FR-04", "NFR-02", "AC-05"]},
                {"path": "app/errors.py", "purpose": "Error envelope and handlers", "implements": ["FR-05", "AC-06"]},
                {"path": "app/config.py", "purpose": "Environment configuration", "implements": ["NFR-01"]},
                {"path": "app/logging_config.py", "purpose": "Masked structured logging", "implements": ["NFR-01", "NFR-04"]},
            ],
        }

    # ------------------------------------------------------------------ UC-03
    def _CodeBundle(self, prompt: str) -> dict[str, Any]:
        implements = {
            "app/main.py": ["FR-01", "FR-05", "AC-01", "AC-06"],
            "app/auth.py": ["FR-03", "AC-03"],
            "app/service.py": ["FR-01", "FR-03", "FR-04", "AC-01", "AC-04", "AC-05", "AC-06"],
            "app/schemas.py": ["FR-02", "AC-02"],
            "app/repository.py": ["FR-01", "FR-04"],
            "app/audit.py": ["FR-04", "NFR-02", "AC-05"],
            "app/errors.py": ["FR-05", "AC-06"],
            "app/config.py": ["NFR-01"],
            "app/logging_config.py": ["NFR-01", "NFR-04"],
            "app/__init__.py": [],
            "requirements.txt": [],
            "pytest.ini": [],
        }
        files = [{"path": p, "content": _read(p), "implements": ids} for p, ids in implements.items()]
        return {
            "language": "python",
            "framework": "fastapi",
            "files": files,
            "run_instructions": "export JWT_SECRET=<key>; pip install -r requirements.txt; uvicorn app.main:app --reload",
            "notes": "Generated code is a proposal for review - it is not production-ready until every quality gate and the human approvals pass.",
        }

    # ------------------------------------------------------------------ UC-04
    def _TestBundle(self, prompt: str) -> dict[str, Any]:
        cases = [
            ("test_update_contact_success_returns_updated_contact", "positive", ["AC-01"]),
            ("test_update_contact_persists_and_is_readable", "positive", ["AC-01"]),
            ("test_missing_mandatory_email_returns_422", "validation", ["AC-02", "AC-06"]),
            ("test_invalid_email_format_returns_422", "validation", ["AC-02"]),
            ("test_missing_address_city_returns_422", "validation", ["AC-02"]),
            ("test_phone_boundary_min_and_max_length_accepted", "boundary", ["AC-02"]),
            ("test_phone_outside_boundary_rejected", "boundary", ["AC-02"]),
            ("test_missing_token_returns_401", "authorization", ["AC-03"]),
            ("test_tampered_token_returns_401", "authorization", ["AC-03"]),
            ("test_other_customer_cannot_modify_returns_403_and_no_change", "authorization", ["AC-04"]),
            ("test_audit_endpoint_requires_owner", "authorization", ["AC-04", "AC-05"]),
            ("test_unknown_customer_returns_404", "negative", ["AC-06"]),
            ("test_error_response_envelope_shape", "api", ["AC-06"]),
            ("test_audit_record_created_with_before_and_after", "positive", ["AC-05"]),
            ("test_failed_update_creates_no_audit_record", "negative", ["AC-04", "AC-05"]),
        ]
        return {
            "files": [
                {"path": "tests/__init__.py", "content": _read("tests/__init__.py"), "implements": []},
                {"path": "tests/conftest.py", "content": _read("tests/conftest.py"), "implements": []},
                {"path": "tests/test_contact_api.py", "content": _read("tests/test_contact_api.py"), "implements": ["AC-01", "AC-02", "AC-03", "AC-04", "AC-05", "AC-06"]},
            ],
            "test_cases": [
                {"name": n, "category": c, "acceptance_criteria_ids": acs, "description": n.replace("_", " ")}
                for n, c, acs in cases
            ],
        }

    # ------------------------------------------------------------------ UC-05
    def _ReviewReport(self, prompt: str) -> dict[str, Any]:
        findings = [
            {
                "id": "REV-01", "severity": "MEDIUM", "component": "Profile Service",
                "finding": "Authorization and lookup are repeated in get_contact and update_contact; a shared _load_owned_customer helper would remove the duplicate 404 handling.",
                "evidence": "app/service.py: both methods call _authorize() then customers.get() inside try/except CustomerNotFound.",
                "recommended_fix": "Extract a private helper that authorizes and loads the record, used by both methods.",
                "source": "ai_review", "category": "duplicate_logic", "file": "app/service.py", "line": 36,
            },
            {
                "id": "REV-02", "severity": "LOW", "component": "Repository",
                "finding": "Module-level singletons (customers, audits) make the data layer hard to swap in tests and for a future PostgreSQL implementation.",
                "evidence": "app/repository.py: `customers = CustomerRepository()` / `audits = AuditRepository()` imported directly by main.py.",
                "recommended_fix": "Provide the repositories through FastAPI dependencies or an app-state container.",
                "source": "ai_review", "category": "testability", "file": "app/repository.py", "line": 117,
            },
            {
                "id": "REV-03", "severity": "LOW", "component": "Customer API",
                "finding": "The GET audit endpoint returns the full list; for long-lived customers this grows unbounded (potential performance concern).",
                "evidence": "app/main.py audit_history returns every entry from service.audit_history().",
                "recommended_fix": "Add limit/offset pagination query parameters.",
                "source": "ai_review", "category": "performance", "file": "app/main.py", "line": 55,
            },
        ]
        if "import sys" in prompt:
            findings.append(
                {
                    "id": "REV-04", "severity": "LOW", "component": "Configuration & Logging",
                    "finding": "Dead import: `sys` is imported but never used.",
                    "evidence": "app/config.py line 7: import sys",
                    "recommended_fix": "Remove the unused import.",
                    "source": "ai_review", "category": "dead_code", "file": "app/config.py", "line": 7,
                }
            )
        return {
            "summary": "Logic is correct and error handling is consistent; findings concern maintainability (duplicate authorization/lookup), testability of module singletons and unbounded audit listing. No blocking defects.",
            "findings": findings,
            "verdict": "comment",
        }

    # ------------------------------------------------------------------ UC-06
    def _SecurityReport(self, prompt: str) -> dict[str, Any]:
        findings = []
        if PLANTED_SECRET_PATTERN.search(prompt):
            findings.append(
                {
                    "id": "SEC-01", "severity": "HIGH", "component": "Configuration & Logging",
                    "finding": "The bearer-token signing key is hard-coded in source (secrets exposure). Anyone with repository access can forge tokens for any customer, defeating the authorization control.",
                    "evidence": 'app/config.py: JWT_SECRET = "hardcoded-dev-secret-..."',
                    "recommended_fix": "Load the key from the JWT_SECRET environment variable (or a secrets manager) and fail fast when it is missing.",
                    "source": "security_agent", "category": "secrets_exposure", "file": "app/config.py", "line": 14,
                }
            )
        findings.extend(
            [
                {
                    "id": "SEC-02", "severity": "MEDIUM", "component": "Authentication",
                    "finding": "No rate limiting or lockout on repeated invalid-token attempts; signature guessing is impractical with HMAC-SHA256 but the endpoint can be abused for enumeration of customer ids via 403 vs 404 responses.",
                    "evidence": "app/auth.py verify_token raises 401 immediately; app/service.py returns 403 before 404 so an attacker with a valid token cannot enumerate ids, but unauthenticated callers receive distinct 401/422 behaviour.",
                    "recommended_fix": "Add a rate limiter (e.g. per token / per IP) at the gateway and keep the 403-before-404 ordering.",
                    "source": "security_agent", "category": "authentication_controls", "file": "app/auth.py", "line": 30,
                },
                {
                    "id": "SEC-03", "severity": "LOW", "component": "Audit Service",
                    "finding": "Audit entries store full before/after contact details (PII); retention and access controls for the audit store must be defined.",
                    "evidence": "app/audit.py _contact_view stores email, phone and address verbatim.",
                    "recommended_fix": "Apply the organisation's PII retention policy to the audit store and restrict read access.",
                    "source": "security_agent", "category": "logging_sensitive_information", "file": "app/audit.py", "line": 40,
                },
            ]
        )
        summary = (
            "Authentication uses HMAC-SHA256 with constant-time comparison and ownership is enforced before every read/write; "
            "input validation is strict; no SQL/command injection or unsafe deserialization surfaces. "
        )
        summary += (
            "BLOCKING: the signing secret is hard-coded in app/config.py."
            if findings and findings[0]["id"] == "SEC-01"
            else "No blocking findings; residual items are hardening recommendations."
        )
        return {"summary": summary, "findings": findings}

    # ------------------------------------------------------------------ UC-07
    def _RemediationProposal(self, prompt: str) -> dict[str, Any]:
        section = prompt.split("BLOCKING FINDINGS TO REMEDIATE", 1)[-1].split("PREVIOUS REMEDIATION ATTEMPTS", 1)[0]
        mentions_secret = "secrets_exposure" in section or '"source": "secrets"' in section
        if mentions_secret:
            return {
                "failure_summary": "Security gate failed: a hard-coded signing secret was detected in app/config.py by the secret scanner, Bandit (B105) and the Security Review agent.",
                "probable_cause": "The configuration module defines JWT_SECRET as a string literal instead of reading it from the deployment environment.",
                "affected_files": ["app/config.py"],
                "changes": [{"path": "app/config.py", "content": _read("fixes/config_fixed.py"), "implements": ["NFR-01"]}],
                "addressed_finding_ids": [fid for fid in re.findall(r"\b(SEC-\d+|SECRETS-\d+|BANDIT-\d+|REV-04)\b", prompt) if fid != "SEC-02" and fid != "SEC-03"],
                "rationale": "Read JWT_SECRET lazily from the environment through a Settings property so the module stays importable for builds and tests while the literal secret is removed; also drops the unused `sys` import flagged by static analysis.",
                "confidence": "high",
            }
        return {
            "failure_summary": "The reported failures could not be mapped to a safe automated correction.",
            "probable_cause": "Unknown - the failure pattern is not one the agent can fix confidently.",
            "affected_files": [],
            "changes": [],
            "addressed_finding_ids": [],
            "rationale": "Escalate to a developer for manual review.",
            "confidence": "low",
        }

    # ------------------------------------------------------------------ Agent 7
    def _ReleaseAssessment(self, prompt: str) -> dict[str, Any]:
        failed = int((re.search(r"MANDATORY_GATES_FAILED:\s*(\d+)", prompt) or ["", "0"])[1])
        blocking = int((re.search(r"OPEN_BLOCKING_FINDINGS:\s*(\d+)", prompt) or ["", "0"])[1])
        medium = int((re.search(r"OPEN_MEDIUM_FINDINGS:\s*(\d+)", prompt) or ["", "0"])[1])
        evidence = re.findall(r"^- (.+)$", prompt, flags=re.MULTILINE)[:12]
        if failed or blocking:
            return {
                "decision": "NOT_READY",
                "rationale": f"{failed} mandatory gate(s) failed and {blocking} blocking finding(s) remain open; the change must not be released.",
                "evidence": evidence,
                "conditions": [],
                "residual_risks": ["Unresolved blocking findings"],
            }
        if medium:
            return {
                "decision": "CONDITIONAL",
                "rationale": "All mandatory gates passed, tests are green and no blocking findings remain. Medium-severity hardening items (rate limiting, duplicate authorization logic) should be tracked before go-live.",
                "evidence": evidence,
                "conditions": ["Track SEC-02 (rate limiting) as a follow-up ticket", "Track REV-01 (duplicate authorization/lookup) for refactoring"],
                "residual_risks": ["Audit store retention policy still to be defined (SEC-03)"],
            }
        return {
            "decision": "READY",
            "rationale": "All mandatory gates passed with no open findings.",
            "evidence": evidence,
            "conditions": [],
            "residual_risks": [],
        }
