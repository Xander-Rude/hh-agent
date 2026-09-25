from dataclasses import dataclass
from pathlib import Path

import yaml


CONFIG_PATH = Path(
    "data/resumes.yaml"
)


@dataclass
class ResumeMatch:
    key: str
    title: str
    hh_resume_id: str
    role_family: str
    source: str
    score: int
    matched_strong: list[str]
    matched_secondary: list[str]


@dataclass
class ResumeDecision:
    action: str

    # use_existing / create_new

    selected_resume_key: str | None
    selected_resume_title: str | None
    selected_resume_id: str | None

    match_score: int

    target_title: str

    reason: str

    vacancy_score: int | None

    best_existing_match: ResumeMatch | None


def normalize(
    value: str | None,
) -> str:
    if not value:
        return ""

    return (
        value
        .lower()
        .replace("ё", "е")
        .replace("—", "-")
        .replace("–", "-")
        .strip()
    )


def load_resume_config() -> dict:
    with CONFIG_PATH.open(
        "r",
        encoding="utf-8",
    ) as file:
        return yaml.safe_load(
            file
        )


def all_resumes(
    config: dict,
) -> dict:
    """
    Возвращает единый словарь:

    базовые резюме
    +
    ранее созданные targeted CV.
    """

    result = dict(
        config.get(
            "resumes",
            {}
        )
    )

    generated = config.get(
        "generated_resumes",
        []
    )

    for index, resume in enumerate(
        generated
    ):
        key = resume.get(
            "key",
            f"generated_{index}",
        )

        result[key] = resume

    return result


def score_resume(
    resume_key: str,
    resume: dict,
    vacancy_title: str,
    vacancy_description: str,
) -> ResumeMatch:
    """
    Это первая версия скоринга.

    Название вакансии имеет гораздо больший вес,
    чем случайные слова в описании.

    Позже поверх этого добавим LLM-проверку.
    """

    title = normalize(
        vacancy_title
    )

    description = normalize(
        vacancy_description
    )

    strong_markers = resume.get(
        "strong_for",
        []
    )

    secondary_markers = resume.get(
        "secondary_for",
        []
    )

    matched_strong = []
    matched_secondary = []

    score = 25

    #
    # STRONG MARKERS
    #

    for marker in strong_markers:
        marker_normalized = normalize(
            marker
        )

        if not marker_normalized:
            continue

        if marker_normalized in title:
            matched_strong.append(
                marker
            )

            score += 55

        elif marker_normalized in description:
            matched_strong.append(
                marker
            )

            score += 12

    #
    # SECONDARY MARKERS
    #

    for marker in secondary_markers:
        marker_normalized = normalize(
            marker
        )

        if not marker_normalized:
            continue

        if marker_normalized in title:
            matched_secondary.append(
                marker
            )

            score += 12

        elif marker_normalized in description:
            matched_secondary.append(
                marker
            )

            score += 4

    #
    # Ограничиваем бонус за большое количество
    # слов в длинном описании.
    #

    if len(matched_strong) > 3:
        score -= (
            len(matched_strong) - 3
        ) * 4

    if len(matched_secondary) > 5:
        score -= (
            len(matched_secondary) - 5
        ) * 2

    score = max(
        0,
        min(
            100,
            score,
        )
    )

    return ResumeMatch(
        key=resume_key,
        title=resume[
            "title"
        ],
        hh_resume_id=resume[
            "hh_resume_id"
        ],
        role_family=resume.get(
            "role_family",
            resume_key,
        ),
        source=resume.get(
            "source",
            "generated",
        ),
        score=score,
        matched_strong=matched_strong,
        matched_secondary=matched_secondary,
    )


def find_best_existing_resume(
    vacancy_title: str,
    vacancy_description: str,
    config: dict,
) -> ResumeMatch | None:
    resumes = all_resumes(
        config
    )

    matches = []

    for key, resume in resumes.items():
        if not resume.get(
            "hh_resume_id"
        ):
            continue

        match = score_resume(
            resume_key=key,
            resume=resume,
            vacancy_title=vacancy_title,
            vacancy_description=vacancy_description,
        )

        matches.append(
            match
        )

    if not matches:
        return None

    matches.sort(
        key=lambda item: item.score,
        reverse=True,
    )

    return matches[0]


def make_target_title(
    vacancy_title: str,
) -> str:
    """
    Пока используем название вакансии.

    На этапе генерации targeted resume
    Gemma будет нормализовывать кривые/длинные
    названия HH.
    """

    title = " ".join(
        vacancy_title.split()
    )

    if len(title) <= 100:
        return title

    return title[:100].rstrip()


def get_fallback_resume(
    config: dict,
) -> ResumeMatch | None:
    strategy = config.get(
        "strategy",
        {}
    )

    fallback_key = strategy.get(
        "fallback_resume",
        "project",
    )

    resumes = all_resumes(
        config
    )

    resume = resumes.get(
        fallback_key
    )

    if not resume:
        return None

    return ResumeMatch(
        key=fallback_key,
        title=resume[
            "title"
        ],
        hh_resume_id=resume[
            "hh_resume_id"
        ],
        role_family=resume.get(
            "role_family",
            fallback_key,
        ),
        source=resume.get(
            "source",
            "base",
        ),
        score=0,
        matched_strong=[],
        matched_secondary=[],
    )


def choose_resume(
    vacancy_title: str,
    vacancy_description: str = "",
    vacancy_score: int | None = None,
) -> ResumeDecision:
    """Compatibility wrapper for the fixed single-resume strategy."""

    config = load_resume_config()
    resumes = all_resumes(config)
    selected = resumes.get("project")

    if not selected:
        raise ValueError(
            "В data/resumes.yaml отсутствует единое резюме 'project'."
        )

    title = str(selected.get("title") or "Руководитель проектов")

    match = ResumeMatch(
        key="project",
        title=title,
        hh_resume_id=str(selected.get("hh_resume_id") or ""),
        role_family=str(selected.get("role_family") or "project"),
        source=str(selected.get("source") or "base"),
        score=0,
        matched_strong=[],
        matched_secondary=[],
    )

    return ResumeDecision(
        action="use_existing",
        selected_resume_key="project",
        selected_resume_title=title,
        selected_resume_id=match.hh_resume_id or None,
        match_score=0,
        target_title=title,
        reason=(
            "Single-resume policy: fixed positioning «Руководитель проектов»; "
            "per-vacancy resume selection is disabled."
        ),
        vacancy_score=vacancy_score,
        best_existing_match=match,
    )
