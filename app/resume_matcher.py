from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from app.llm import LLMProvider


RESUMES_PATH = Path("data/resumes.yaml")


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
    # Kept compatible with the existing project API.
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


class _LLMResumeScore(BaseModel):
    key: str
    score: int = Field(ge=0, le=100)
    strengths: list[str] = Field(default_factory=list, max_length=4)
    gaps: list[str] = Field(default_factory=list, max_length=4)
    rationale: str = ""


class _LLMRanking(BaseModel):
    scores: list[_LLMResumeScore] = Field(min_length=4, max_length=4)
    selected_key: str
    rationale: str


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

    required = {
        "project",
        "delivery",
        "technical_project",
        "product",
    }

    missing = required - set(resumes)

    if missing:
        raise ValueError(
            "В resumes.yaml отсутствуют обязательные резюме: "
            + ", ".join(sorted(missing))
        )

    for key in required:
        item = resumes[key]

        if not item.get("title"):
            raise ValueError(f"У резюме {key!r} отсутствует title.")

        if not item.get("hh_resume_id"):
            raise ValueError(f"У резюме {key!r} отсутствует hh_resume_id.")

    return data


def _extract_response_text(response: Any) -> str:
    if hasattr(response, "message"):
        message = response.message
        if hasattr(message, "content"):
            return message.content or ""

    if isinstance(response, dict):
        message = response.get("message", {})
        if isinstance(message, dict):
            return message.get("content", "") or ""

    raise RuntimeError("Не удалось получить текст из ответа Ollama.")



def _normalize_ranking_scale(
    ranking: _LLMRanking,
) -> _LLMRanking:
    """
    Gemma occasionally returns a 0-10 scale even though the schema says 0-100.
    If ALL four scores are <= 10, treat the response as 0-10 and scale it to 0-100.
    Mixed ranges are left untouched because they are more likely intentional.
    """
    if (
        ranking.scores
        and all(
            0 <= item.score <= 10
            for item in ranking.scores
        )
    ):
        for item in ranking.scores:
            item.score = min(
                100,
                item.score * 10,
            )

    return ranking


def _ask_ranking(
    vacancy_title: str,
    vacancy_description: str,
    resumes: dict[str, Any],
) -> _LLMRanking:
    resume_blocks = []

    for key, item in resumes.items():
        resume_blocks.append(
            "\n".join(
                [
                    f"KEY: {key}",
                    f"TITLE: {item.get('title', '')}",
                    f"POSITIONING: {item.get('positioning', '')}",
                    "BEST FOR: "
                    + "; ".join(item.get("best_for", []) or []),
                    "STRENGTHS: "
                    + "; ".join(item.get("strengths", []) or []),
                ]
            )
        )

    prompt = f"""
Ты выбираешь ЛУЧШЕЕ ИЗ ЧЕТЫРЁХ УЖЕ СУЩЕСТВУЮЩИХ резюме кандидата
для отклика на конкретную вакансию.

ВАЖНО:
- новые резюме создавать нельзя;
- адаптировать/переписывать резюме нельзя;
- selected_key ОБЯЗАН быть одним из:
  project, delivery, technical_project, product;
- оценивай именно позиционирование существующего CV под вакансию;
- не придумывай опыт кандидата;
- не считай отсутствие узкой технологии критичным, если она не является
  центральной частью роли;
- главное: тип роли, зона ответственности, seniority, delivery/program/product/
  technical focus и доменный/технический контекст;
- даже если все четыре резюме подходят слабо, всё равно выбери лучшее из них;
- score — относительное качество именно этого готового CV для этой вакансии;
- score ОБЯЗАТЕЛЬНО ставь по шкале 0-100, а не 0-10;
- примеры: слабое соответствие = 35-50, среднее = 60-75, хорошее = 80-89, отличное = 90-100.

ВАКАНСИЯ
Название:
{vacancy_title}

Описание:
{vacancy_description}

СУЩЕСТВУЮЩИЕ РЕЗЮМЕ

{chr(10).join(chr(10) + block for block in resume_blocks)}

Верни JSON строго по схеме.
Для каждого из четырёх key дай score, strengths, gaps и краткое rationale.
selected_key должен соответствовать максимальному score.
""".strip()

    schema = _LLMRanking.model_json_schema()

    llm = LLMProvider()

    last_error: Exception | None = None

    for _ in range(3):
        try:
            response = llm.chat(
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                format_schema=schema,
            )

            raw = _extract_response_text(response).strip()

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                start = raw.find("{")
                end = raw.rfind("}")

                if start == -1 or end == -1:
                    raise

                data = json.loads(raw[start:end + 1])

            result = _LLMRanking.model_validate(data)
            result = _normalize_ranking_scale(
                result
            )

            received_keys = {
                item.key
                for item in result.scores
            }

            expected_keys = set(resumes)

            if received_keys != expected_keys:
                raise ValueError(
                    "LLM вернула не тот набор resume keys: "
                    f"{sorted(received_keys)}"
                )

            # Never trust selected_key blindly. Python chooses the max score.
            result.selected_key = max(
                result.scores,
                key=lambda item: item.score,
            ).key

            return result

        except Exception as exc:
            last_error = exc

    raise RuntimeError(
        "Не удалось получить корректный ranking резюме от LLM."
    ) from last_error


def _fallback_key(
    vacancy_title: str,
    vacancy_description: str,
    fallback: str,
) -> str:
    """
    Deterministic fallback only if Ollama fails completely.
    It does NOT replace the LLM matcher in normal operation.
    """
    text = f"{vacancy_title}\n{vacancy_description}".lower()

    delivery_markers = [
        "program manager",
        "programme manager",
        "delivery manager",
        "project office",
        "проектного офиса",
        "проектный офис",
        "pmo",
        "портфел",
        "portfolio",
        "программ",
    ]

    technical_markers = [
        "technical project",
        "техническ",
        "highload",
        "архитектур",
        "интеграц",
        "инфраструктур",
        "engineering",
    ]

    product_markers = [
        "product manager",
        "product owner",
        "менеджер продукта",
        "продуктов",
        "product roadmap",
        "p&l",
    ]

    if any(marker in text for marker in delivery_markers):
        return "delivery"

    if any(marker in text for marker in product_markers):
        return "product"

    if any(marker in text for marker in technical_markers):
        return "technical_project"

    return fallback if fallback in {
        "project",
        "delivery",
        "technical_project",
        "product",
    } else "project"


def match_resume(
    vacancy_title: str,
    vacancy_description: str,
    vacancy_score: int = 0,
) -> ResumeMatchDecision:
    """
    Select the best of the four existing HH resumes.

    This function NEVER returns tailor_existing or create_new.
    The only possible action is use_existing.
    """
    config = load_resumes_config()
    resumes = config["resumes"]

    fallback = (
        config.get("strategy", {})
        .get("fallback_resume", "project")
    )

    try:
        ranking = _ask_ranking(
            vacancy_title=vacancy_title or "",
            vacancy_description=vacancy_description or "",
            resumes=resumes,
        )

        llm_scores = {
            item.key: item
            for item in ranking.scores
        }

        selected_key = ranking.selected_key

        scores: list[ResumeScore] = []

        for key in resumes:
            item = resumes[key]
            llm_item = llm_scores[key]

            scores.append(
                ResumeScore(
                    key=key,
                    title=item["title"],
                    hh_resume_id=item["hh_resume_id"],
                    score=llm_item.score,
                    strengths=list(llm_item.strengths),
                    gaps=list(llm_item.gaps),
                    rationale=llm_item.rationale,
                )
            )

        selected_score = next(
            item
            for item in scores
            if item.key == selected_key
        )

        rationale = ranking.rationale or selected_score.rationale

    except Exception as exc:
        selected_key = _fallback_key(
            vacancy_title=vacancy_title or "",
            vacancy_description=vacancy_description or "",
            fallback=fallback,
        )

        item = resumes[selected_key]

        selected_score = ResumeScore(
            key=selected_key,
            title=item["title"],
            hh_resume_id=item["hh_resume_id"],
            score=0,
            strengths=[],
            gaps=[],
            rationale=(
                "Ollama matcher недоступен; использован "
                "детерминированный fallback."
            ),
        )

        scores = [selected_score]

        rationale = (
            f"Fallback resume selection because matcher failed: "
            f"{type(exc).__name__}: {exc}"
        )

    selected = resumes[selected_key]

    return ResumeMatchDecision(
        action="use_existing",
        selected_resume_key=selected_key,
        selected_resume_title=selected["title"],
        selected_resume_id=selected["hh_resume_id"],
        match_score=selected_score.score,
        target_title=selected["title"],
        rationale=rationale,
        scores=scores,
    )


# Friendly aliases for future code.
select_resume = match_resume
select_best_resume = match_resume
