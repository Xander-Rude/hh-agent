from __future__ import annotations

from typing import Protocol


class ApplicationLike(Protocol):
    status: str
    cover_letter: str | None


class EvaluationLike(Protocol):
    cover_letter: str | None


def approved_for_dispatch(application: ApplicationLike) -> bool:
    """Telegram/user approval is the final dispatch authority.

    External-site workers must not reinterpret the vacancy's latest evaluation
    after the user has explicitly approved the Application.
    """

    return (application.status or "").strip().lower() == "approved"


def resolve_application_text(
    application: ApplicationLike,
    evaluation: EvaluationLike | None,
) -> str:
    """Use the approved Application snapshot first; Evaluation is fallback only."""

    application_text = (application.cover_letter or "").strip()
    if application_text:
        return application_text

    if evaluation is None:
        return ""

    return (evaluation.cover_letter or "").strip()
