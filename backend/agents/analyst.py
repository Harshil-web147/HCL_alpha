"""Business Analyst agent.

Flow:
  1. analyze(prompt)          -> AnalysisSpec with up to 5 multiple-choice questions
  2. user picks options
  3. refine(spec, answers)    -> AnalysisSpec with status ready_for_design

Both steps validate LLM output (schema + traceability rules) and retry
with the error message if the output is wrong.
"""

import json
import re

from pydantic import ValidationError

from backend.agents.analyst_schemas import AnalysisSpec
from backend.services import llm

MAX_QUESTIONS = 5
MAX_ATTEMPTS = 3

CHECKLIST = """\
1. Actors and roles: who calls the API? Is authentication needed? Admin vs normal user?
2. Entities: every noun in the request. Fields, types, required/optional, unique keys,
   formats (email, phone, date, currency, enum values), length and range limits.
3. Operations: every verb in the request. Also implied ones: create, read one, list,
   update, delete, search, filter, sort, pagination.
4. Validation and errors: what is invalid input? Status codes 400, 404, 409, 422.
5. Business rules: calculations, state transitions, limits, ownership, duplicates.
6. Data storage: in-memory or SQLite, persistence across restarts, timestamps, IDs.
7. Edge cases: empty input, duplicates, missing resource, max sizes, concurrent updates.
8. Security: input sanitisation, no secrets in code, auth on write endpoints, PII handling.
9. Non-functional: response format (JSON), OpenAPI docs, logging, simple performance target.
10. Out of scope: anything a reader might expect but we will NOT build.
"""

ANALYZE_SYSTEM = f"""You are a senior Business Analyst in an automated SDLC pipeline.
Users type short, vague, incomplete requests. Your job: turn the request into a
complete, testable specification for a SMALL Python FastAPI REST API that a
Designer agent will turn into an API design.

METHOD
- Treat every noun as a possible entity and every verb as a possible operation.
- Walk through this checklist. Every item must end up as a requirement,
  an assumption, an out_of_scope line, or a question:
{CHECKLIST}
RULES FOR DEFAULTS VS QUESTIONS
- Prefer a sensible industry-standard default and record it in assumptions.
- Ask a question ONLY if a wrong guess would materially change the data model,
  the endpoints, or the business rules.
- At most {MAX_QUESTIONS} questions. Fewer is better. Zero is fine if the request is clear.
- Every question is multiple choice: 2 to 4 short, mutually exclusive options.
  Put the recommended option first and set default_option to 0.
- No open-ended questions. No questions about tech stack (fixed: Python, FastAPI, SQLite).
- Fill why_it_matters and affects (REQ ids) for every question.

RULES FOR REQUIREMENTS
- IDs: REQ-01, REQ-02 ... ; AC-01, AC-02 ... ; Q-01, Q-02 ... (two digits).
- source = "user" only if the user actually said it, otherwise "inferred".
- Every requirement has at least one acceptance criterion.
- Acceptance criteria are concrete and testable: real example values, ONE exact
  HTTP method and ONE exact status code. Never write "PATCH or PUT" or
  "422 or 400"; pick one. Include negative cases (invalid input, not found).
- Keep scope small: one FastAPI app, no frontend, no external services,
  no payments, no email sending, unless the user explicitly asked.

OUTPUT
Return ONLY one JSON object, no markdown, matching this JSON schema:
{json.dumps(AnalysisSpec.model_json_schema())}
Set status to "needs_answers" if questions is non-empty, else "ready_for_design".
"""

REFINE_SYSTEM = f"""You are a senior Business Analyst finalising a specification.
You receive the current specification (JSON) and the user's decisions for each
open question. Produce the FINAL specification.

RULES
- Apply every decision. Add or change requirements and acceptance criteria so
  the decision is fully reflected. New items from decisions use source "answer".
- Keep existing IDs unchanged. New IDs continue the numbering.
- Remove requirements that a decision made irrelevant, and their criteria.
- Add one assumption per decision: "Q-0X decided: <decision>".
- questions must be an empty list. status must be "ready_for_design".
- Every requirement has at least one testable acceptance criterion.

OUTPUT
Return ONLY one JSON object, no markdown, matching this JSON schema:
{json.dumps(AnalysisSpec.model_json_schema())}
"""

REQ_RE = re.compile(r"^REQ-\d{2,}$")
AC_RE = re.compile(r"^AC-\d{2,}$")
Q_RE = re.compile(r"^Q-\d{2,}$")


class AnalystError(RuntimeError):
    pass


def _extract_json(raw: str) -> str:
    text = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return text
    return text[start : end + 1]


def check_spec(spec: AnalysisSpec, final: bool = False) -> list[str]:
    """Deterministic quality rules. Empty list = spec is valid."""
    problems: list[str] = []
    req_ids = [r.id for r in spec.requirements]
    ac_ids = [a.id for a in spec.acceptance_criteria]
    q_ids = [q.id for q in spec.questions]

    if not req_ids:
        problems.append("No requirements produced.")
    if not spec.entities:
        problems.append("No entities identified.")
    for name, ids, rx in (("REQ", req_ids, REQ_RE), ("AC", ac_ids, AC_RE), ("Q", q_ids, Q_RE)):
        if len(ids) != len(set(ids)):
            problems.append(f"Duplicate {name} ids: {ids}")
        bad = [i for i in ids if not rx.match(i)]
        if bad:
            problems.append(f"Bad {name} id format: {bad}")

    for ac in spec.acceptance_criteria:
        if ac.requirement_id not in req_ids:
            problems.append(f"{ac.id} points to unknown requirement {ac.requirement_id}.")
    covered = {ac.requirement_id for ac in spec.acceptance_criteria}
    missing = [r for r in req_ids if r not in covered]
    if missing:
        problems.append(f"Requirements without acceptance criteria: {missing}")

    if len(spec.questions) > MAX_QUESTIONS:
        problems.append(f"Too many questions ({len(spec.questions)}), max {MAX_QUESTIONS}.")
    for q in spec.questions:
        if not 0 <= q.default_option < len(q.options):
            problems.append(f"{q.id} default_option out of range.")
        unknown = [r for r in q.affects if r not in req_ids]
        if unknown:
            problems.append(f"{q.id} affects unknown requirements {unknown}.")

    if final and spec.questions:
        problems.append("Final spec must have zero open questions.")
    expected = "ready_for_design" if not spec.questions else "needs_answers"
    if spec.status != expected:
        problems.append(f"status must be '{expected}'.")
    return problems


def _run(system: str, user: str, final: bool) -> AnalysisSpec:
    last_error = ""
    for _ in range(MAX_ATTEMPTS):
        prompt = user
        if last_error:
            prompt += (
                "\n\nYour previous answer was rejected for these reasons:\n"
                f"{last_error}\nFix them and return the corrected JSON only."
            )
        raw = llm.complete_json(system, prompt)
        try:
            spec = AnalysisSpec.model_validate_json(_extract_json(raw))
        except ValidationError as exc:
            last_error = str(exc)[:3000]
            continue
        problems = check_spec(spec, final=final)
        if not problems:
            return spec
        last_error = "\n".join(problems)
    raise AnalystError(f"Analyst failed after {MAX_ATTEMPTS} attempts: {last_error}")


def analyze(prompt: str) -> AnalysisSpec:
    """Step 1: vague user prompt -> expanded spec + clarifying questions."""
    if not prompt or not prompt.strip():
        raise ValueError("Prompt is empty.")
    user = f'User request:\n"""\n{prompt.strip()}\n"""'
    return _run(ANALYZE_SYSTEM, user, final=False)


def resolve_answers(spec: AnalysisSpec, answers: dict[str, str]) -> list[dict]:
    """Turn {Q-01: '1'} or {Q-01: '<exact option text>'} into explicit decisions.
    Only listed options are allowed. Unanswered questions get the default option.
    Raises ValueError for an invalid answer."""
    decisions = []
    for q in spec.questions:
        given = (answers.get(q.id) or "").strip()
        if not given:
            choice, how = q.options[q.default_option], "default (not answered)"
        elif given.isdigit() and int(given) < len(q.options):
            choice, how = q.options[int(given)], "user"
        elif given in q.options:
            choice, how = given, "user"
        else:
            raise ValueError(
                f"{q.id}: invalid answer {given!r}. Use 0 to {len(q.options) - 1} or leave empty."
            )
        decisions.append({"question_id": q.id, "question": q.question, "decision": choice, "decided_by": how})
    return decisions


def refine(spec: AnalysisSpec, answers: dict[str, str]) -> AnalysisSpec:
    """Step 2: apply user's choices -> final spec ready for Designer agent."""
    if not spec.questions:
        return spec.model_copy(update={"status": "ready_for_design"})
    decisions = resolve_answers(spec, answers)
    user = (
        "Current specification:\n"
        f"{spec.model_dump_json(indent=2)}\n\n"
        "User decisions:\n"
        f"{json.dumps(decisions, indent=2)}"
    )
    return _run(REFINE_SYSTEM, user, final=True)
