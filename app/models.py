from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AI_PROJECT_URL = "https://rudenko.one/hh-agent.html"


def _with_ai_project_context(text: str) -> str:
    body = (text or "").strip()

    if not body or AI_PROJECT_URL in body:
        return body

    cyrillic = sum(
        1
        for char in body
        if "\u0400" <= char <= "\u04ff"
    )
    latin = sum(
        1
        for char in body
        if ("a" <= char.lower() <= "z")
    )
    is_russian = cyrillic >= latin

    if is_russian:
        project_context = (
            "Также развиваю собственный AI-agent проект, который автоматизирует "
            "полный workflow работы с вакансиями: "
            f"{AI_PROJECT_URL}"
        )
        signature_marker = "\n\nС уважением"
    else:
        project_context = (
            "I also develop my own AI-agent project that automates the full "
            "vacancy workflow: "
            f"{AI_PROJECT_URL}"
        )
        signature_marker = "\n\nBest regards"

    if signature_marker in body:
        prefix, suffix = body.split(
            signature_marker,
            1,
        )
        return (
            prefix.rstrip()
            + "\n\n"
            + project_context
            + signature_marker
            + suffix
        )

    return body + "\n\n" + project_context


class VacancyEvaluation(BaseModel):
    model_config = ConfigDict(
        validate_assignment=True,
    )

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
    ai_relevant: bool = Field(
        default=False,
        description=(
            "True only when AI/ML/LLM/GenAI/RAG/AI agents or AI products "
            "are a substantial part of the vacancy's tasks, requirements, "
            "product, or direction; incidental mentions are false."
        ),
    )
    cover_letter: str

    @model_validator(mode="after")
    def enforce_ai_project_site(self):
        """
        AI-relevant non-reject evaluations must always carry the public
        project-page URL in the final cover letter.

        This invariant is intentionally enforced at the model boundary so it
        survives LLM enrichment failures, fallback letters, cached results and
        later cover-letter assignments in any pipeline entry point.
        """
        if (
            not self.ai_relevant
            or self.decision == "reject"
            or not (self.cover_letter or "").strip()
        ):
            return self

        guarded = _with_ai_project_context(
            self.cover_letter
        )

        if guarded != self.cover_letter:
            object.__setattr__(
                self,
                "cover_letter",
                guarded,
            )

        return self
