from typing import Literal

from pydantic import BaseModel, Field


class VacancyEvaluation(BaseModel):
    score: int = Field(ge=0, le=100)

    decision: Literal[
        "reject",
        "review",
        "apply",
    ]

    role_match: int = Field(ge=0, le=100)
    seniority_match: int = Field(ge=0, le=100)
    domain_match: int = Field(ge=0, le=100)
    responsibility_match: int = Field(ge=0, le=100)

    must_have_missing: list[str]
    nice_to_have_missing: list[str]

    strengths: list[str]
    gaps: list[str]
    red_flags: list[str]

    summary: str
    recommendation: str
    cover_letter: str