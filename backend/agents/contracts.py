"""Structured contracts shared by deterministic and hosted agents."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Requirement(Contract):
    id: str = Field(pattern=r"^REQ-\d{2,}$")
    text: str = Field(min_length=1)


class AcceptanceCriterion(Contract):
    id: str = Field(pattern=r"^AC-\d{2,}$")
    requirement_id: str
    text: str = Field(min_length=1)


class Analysis(Contract):
    requirements: list[Requirement] = Field(min_length=1)
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1)
    assumptions: list[str]
    questions: list[str]

    @model_validator(mode="after")
    def validate_ids(self) -> "Analysis":
        requirement_ids = [item.id for item in self.requirements]
        criterion_ids = [item.id for item in self.acceptance_criteria]
        if len(set(requirement_ids)) != len(requirement_ids):
            raise ValueError("Duplicate requirement IDs")
        if len(set(criterion_ids)) != len(criterion_ids):
            raise ValueError("Duplicate acceptance criterion IDs")
        if any(item.requirement_id not in requirement_ids for item in self.acceptance_criteria):
            raise ValueError("Acceptance criterion references unknown requirement")
        return self


class Endpoint(Contract):
    method: str
    path: str
    description: str


class Design(Contract):
    summary: str
    endpoints: list[Endpoint]
    models: list[str]
    file_structure: list[str]
    security: list[str]


class ProjectFile(Contract):
    path: str
    content: str


class GeneratedCode(Contract):
    files: list[ProjectFile] = Field(min_length=1)
    summary: str


class Mapping(Contract):
    requirement_id: str
    ac_id: str
    implementation: str
    test: str


class GeneratedTests(GeneratedCode):
    mapping: list[Mapping] = Field(min_length=1)


class Finding(Contract):
    severity: Literal["critical", "high", "medium", "low", "info"]
    category: Literal["quality", "security", "test", "requirements"]
    file: str
    evidence: str
    proposed_fix: str


class Review(Contract):
    findings: list[Finding]
    summary: str


class ReleaseExplanation(Contract):
    explanation: str
