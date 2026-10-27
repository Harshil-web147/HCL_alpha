"""Output contract of the Business Analyst agent.

The Designer agent receives AnalysisSpec as its input, so keep this file
shared with the team and change it only by agreement.
"""

from typing import Literal

from pydantic import BaseModel, Field


class EntityField(BaseModel):
    name: str
    type: str = Field(description="str, int, float, bool, date, datetime, email, phone, enum[...]")
    required: bool = True
    rules: list[str] = Field(default_factory=list, description="e.g. 'unique', 'max 100 chars', 'E.164 format'")


class Entity(BaseModel):
    name: str
    description: str
    fields: list[EntityField]


class Requirement(BaseModel):
    id: str = Field(description="REQ-01, REQ-02, ...")
    text: str
    priority: Literal["must", "should", "could"] = "must"
    source: Literal["user", "inferred", "answer"] = Field(
        description="user = stated by user, inferred = analyst default, answer = from a clarifying answer"
    )


class AcceptanceCriterion(BaseModel):
    id: str = Field(description="AC-01, AC-02, ...")
    requirement_id: str = Field(description="Must match an existing REQ id")
    given: str
    when: str
    then: str = Field(description="Concrete, testable outcome incl. HTTP status code")


class Question(BaseModel):
    id: str = Field(description="Q-01, Q-02, ...")
    question: str
    why_it_matters: str
    options: list[str] = Field(min_length=2, max_length=4)
    default_option: int = Field(0, description="Index of recommended option")
    affects: list[str] = Field(default_factory=list, description="REQ ids impacted")


class AnalysisSpec(BaseModel):
    title: str
    summary: str
    domain: str
    actors: list[str]
    entities: list[Entity]
    requirements: list[Requirement]
    acceptance_criteria: list[AcceptanceCriterion]
    non_functional: list[str]
    assumptions: list[str]
    out_of_scope: list[str]
    questions: list[Question] = Field(default_factory=list)
    status: Literal["needs_answers", "ready_for_design"] = "needs_answers"
