from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


RESUMES_PATH = Path("data/resumes.yaml")
PROJECT_KEY = "project"


@dataclass
class ResumeScore:
    key: str
    title: str
    hh_resume_id: str
    score: int
    strengths: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    rationale: str = ""


@dataclass
class ResumeMatchDecision:
    action: str
    selected_resume_key: str
    selected_resume_title: str
    selected_resume_id: str
    match_score: int
    target_title: str
    rationale: str
    scores: list[ResumeScore] = field(default_factory=list)

    @property
    def hh_resume_id(self) -> str:
        return self.selected_resume_id


def load_resumes_config() -> dict[str, Any]:
    if not RESUMES_PATH.exists():
        raise FileNotFoundError(f"Не найден {RESUMES_PATH}")

    with RESUMES_PATH.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    resumes = data.get("resumes")
    if not isinstance(resumes, dict) or not resumes:
        raise ValueError(
            "В data/resumes.yaml должен быть непустой блок 'resumes'."
        )

    project = resumes.get(PROJECT_KEY)
    if not isinstance(project, dict):
        raise ValueError(
            "В data/resumes.yaml отсутствует единое резюме 'project'."
        )
    if not project.get("title"):
        raise ValueError("У резюме 'project' отсутствует title.")
    if not project.get("hh_resume_id"):
        raise ValueError("У резюме 'project' отсутствует hh_resume_id.")

    return data


def match_resume(
    vacancy_title: str,
    vacancy_description: str,
    vacancy_score: int = 0,
) -> ResumeMatchDecision:
    """Return the single canonical Project Manager resume.

    Resume ranking was removed after the positioning was fixed to
    «Руководитель проектов». Keeping this function preserves the existing
    pipeline API without spending an LLM call or inventing per-vacancy CV
    variants.
    """

    config = load_resumes_config()
    project = config["resumes"][PROJECT_KEY]
    title = str(project["title"])
    resume_id = str(project["hh_resume_id"])
    rationale = (
        "Single-resume policy: fixed positioning «Руководитель проектов»; "
        "resume ranking is disabled."
    )

    score = ResumeScore(
        key=PROJECT_KEY,
        title=title,
        hh_resume_id=resume_id,
        score=0,
        strengths=[],
        gaps=[],
        rationale=rationale,
    )

    return ResumeMatchDecision(
        action="use_existing",
        selected_resume_key=PROJECT_KEY,
        selected_resume_title=title,
        selected_resume_id=resume_id,
        match_score=0,
        target_title=title,
        rationale=rationale,
        scores=[score],
    )


select_resume = match_resume
select_best_resume = match_resume
