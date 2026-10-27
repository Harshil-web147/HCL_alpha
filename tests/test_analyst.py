"""Offline tests: the LLM is faked, so no API key or cost needed."""

import json

import pytest

from backend.agents import analyst
from backend.agents.analyst_schemas import AnalysisSpec

GOOD = {
    "title": "Contact update API",
    "summary": "Customers update phone number.",
    "domain": "CRM",
    "actors": ["customer"],
    "entities": [{"name": "Customer", "description": "A customer",
                  "fields": [{"name": "phone", "type": "phone", "required": True, "rules": ["E.164"]}]}],
    "requirements": [{"id": "REQ-01", "text": "Customer can update phone", "priority": "must", "source": "user"}],
    "acceptance_criteria": [{"id": "AC-01", "requirement_id": "REQ-01", "given": "customer 1 exists",
                             "when": "PUT /customers/1/phone with +919876543210", "then": "HTTP 200"}],
    "non_functional": ["JSON responses"],
    "assumptions": ["SQLite storage"],
    "out_of_scope": ["SMS verification"],
    "questions": [{"id": "Q-01", "question": "Verify phone by OTP?", "why_it_matters": "adds endpoint",
                   "options": ["No", "Yes"], "default_option": 0, "affects": ["REQ-01"]}],
    "status": "needs_answers",
}


def fake_llm(responses):
    calls = []

    def _fake(system, user, temperature=0.2):
        calls.append(user)
        return responses[len(calls) - 1]

    return _fake, calls


def test_analyze_valid(monkeypatch):
    fake, calls = fake_llm([json.dumps(GOOD)])
    monkeypatch.setattr(analyst.llm, "complete_json", fake)
    spec = analyst.analyze("let customers change phone")
    assert spec.requirements[0].id == "REQ-01"
    assert len(calls) == 1


def test_retry_on_broken_traceability(monkeypatch):
    bad = json.loads(json.dumps(GOOD))
    bad["acceptance_criteria"][0]["requirement_id"] = "REQ-99"
    fake, calls = fake_llm(["```json\n" + json.dumps(bad) + "\n```", json.dumps(GOOD)])
    monkeypatch.setattr(analyst.llm, "complete_json", fake)
    spec = analyst.analyze("x")
    assert len(calls) == 2
    assert "REQ-99" in calls[1]
    assert spec.acceptance_criteria[0].requirement_id == "REQ-01"


def test_gives_up_after_max_attempts(monkeypatch):
    fake, _ = fake_llm(["not json"] * analyst.MAX_ATTEMPTS)
    monkeypatch.setattr(analyst.llm, "complete_json", fake)
    with pytest.raises(analyst.AnalystError):
        analyst.analyze("x")


def test_empty_prompt_rejected():
    with pytest.raises(ValueError):
        analyst.analyze("   ")


def test_unanswered_question_uses_default():
    spec = AnalysisSpec.model_validate(GOOD)
    d = analyst.resolve_answers(spec, {})
    assert d[0]["decision"] == "No"
    assert d[0]["decided_by"].startswith("default")
    assert analyst.resolve_answers(spec, {"Q-01": "1"})[0]["decision"] == "Yes"


def test_refine_requires_zero_questions(monkeypatch):
    final = json.loads(json.dumps(GOOD))
    still_open = json.dumps(final)
    final["questions"], final["status"] = [], "ready_for_design"
    fake, calls = fake_llm([still_open, json.dumps(final)])
    monkeypatch.setattr(analyst.llm, "complete_json", fake)
    out = analyst.refine(AnalysisSpec.model_validate(GOOD), {"Q-01": "0"})
    assert out.status == "ready_for_design" and len(calls) == 2


def test_invalid_answer_rejected():
    spec = AnalysisSpec.model_validate(GOOD)
    with pytest.raises(ValueError):
        analyst.resolve_answers(spec, {"Q-01": "asdkjh^Z98"})
    with pytest.raises(ValueError):
        analyst.resolve_answers(spec, {"Q-01": "7"})
    assert analyst.resolve_answers(spec, {"Q-01": "Yes"})[0]["decision"] == "Yes"
