from __future__ import annotations

import difflib
import os
import re

from sqlalchemy import and_, func, or_, select

from app.clean_live_guard import (
    CLEAN_ELIGIBLE_ROUTES,
    assessment_version_filters,
    clean_eligibility,
    current_policy_context,
)
from app.cover_letter_runtime import (
    build_clean_cover_letter,
    build_legacy_vacancy_cover_letter,
    clean_cover_fact_bank,
    legacy_cover_fact_bank,
    parse_strengths,
)
from app.cover_letter_writer import (
    COVER_WRITER_VERSION,
    write_human_cover_letter,
)
from app.db import (
    Application,
    CleanShadowAssessment,
    Evaluation,
    HhVacancyDiscovery,
    SessionLocal,
    Vacancy,
)


RECOMMENDED_DECISIONS = ("apply", "review")
MIN_SCORE = int(os.getenv("TELEGRAM_MIN_SCORE", "72"))
MAX_PER_ACCOUNT = max(
    1,
    int(os.getenv("HH_COVER_PREPARE_MAX_PER_ACCOUNT", "30")),
)
MAX_FINAL_SIMILARITY = float(
    os.getenv("HH_COVER_MAX_FINAL_SIMILARITY", "0.90")
)


def _similarity_body(text: str | None) -> str:
    body = (text or "").strip().lower().replace("ё", "е")
    body = re.sub(
        r"(?is)\n*\s*(?:с уважением\s*,?\s*)?александр\s+руденко\s*$",
        "",
        body,
    )
    body = re.sub(
        r"(?is)\n*\s*(?:best regards\s*,?\s*)?aleksandr\s+rudenko\s*$",
        "",
        body,
    )
    body = re.sub(
        r"^\s*(?:здравствуйте|hello)\s*[,.:;!?-]*\s*",
        "",
        body,
    )
    return re.sub(r"[^0-9a-zа-я]+", " ", body).strip()


def _similarity(left: str | None, right: str | None) -> float:
    first = _similarity_body(left)
    second = _similarity_body(right)
    if not first or not second:
        return 0.0
    return difflib.SequenceMatcher(None, first, second).ratio()


def _recent_letters(session, account_key: str) -> list[str]:
    return [
        str(value).strip()
        for value in session.scalars(
            select(Application.cover_letter)
            .where(Application.account_key == account_key)
            .where(Application.cover_letter.is_not(None))
            .where(func.length(func.trim(Application.cover_letter)) > 0)
            .order_by(Application.id.desc())
            .limit(6)
        ).all()
        if str(value or "").strip()
    ]


def _remember(recent: list[str], letter: str) -> None:
    recent.insert(0, letter)
    del recent[6:]


def _acceptable_final(letter: str, recent: list[str]) -> bool:
    if not recent:
        return True
    return max(_similarity(letter, previous) for previous in recent) <= MAX_FINAL_SIMILARITY


def _write_letter(
    *,
    account_key: str,
    vacancy: Vacancy,
    safe_draft: str,
    facts: list[str],
    recent: list[str],
) -> str | None:
    letter = write_human_cover_letter(
        account_key=account_key,
        vacancy_title=vacancy.title,
        vacancy_company=vacancy.company,
        vacancy_description=vacancy.description or "",
        safe_draft=safe_draft,
        allowed_facts=facts,
        recent_letters=recent,
    ).strip()
    if not letter:
        return None
    if not _acceptable_final(letter, recent):
        print(
            "[COVER PREP] rejected final near-duplicate: "
            f"vacancy={vacancy.id} account={account_key}"
        )
        return None
    return letter


def _prepare_old(session) -> tuple[int, int]:
    latest_evaluation_id = (
        select(Evaluation.id)
        .where(Evaluation.vacancy_id == Vacancy.id)
        .where(~Evaluation.model.startswith("hard-filter/"))
        .order_by(Evaluation.created_at.desc(), Evaluation.id.desc())
        .limit(1)
        .correlate(Vacancy)
        .scalar_subquery()
    )
    has_old_application = (
        select(Application.id)
        .where(
            Application.vacancy_id == Vacancy.id,
            Application.account_key == "old",
        )
        .exists()
    )
    has_any_application = (
        select(Application.id)
        .where(Application.vacancy_id == Vacancy.id)
        .exists()
    )
    rows = session.execute(
        select(Vacancy, Evaluation)
        .join(Evaluation, Evaluation.id == latest_evaluation_id)
        .where(
            or_(
                and_(Vacancy.source == "hh", ~has_old_application),
                and_(Vacancy.source != "hh", ~has_any_application),
            )
        )
        .where(Evaluation.decision.in_(RECOMMENDED_DECISIONS))
        .where(Evaluation.score >= MIN_SCORE)
        .order_by(
            Evaluation.score.desc(),
            Evaluation.responsibility_match.desc(),
            Evaluation.id.desc(),
        )
        .limit(MAX_PER_ACCOUNT)
    ).all()

    recent = _recent_letters(session, "old")
    prepared = 0
    skipped = 0

    for vacancy, evaluation in rows:
        current = (evaluation.cover_letter or "").strip()
        if (
            evaluation.cover_letter_version == COVER_WRITER_VERSION
            and current
        ):
            _remember(recent, current)
            skipped += 1
            continue

        strengths = parse_strengths(evaluation.strengths)
        safe_draft = build_legacy_vacancy_cover_letter(
            vacancy_title=vacancy.title,
            vacancy_company=vacancy.company,
            vacancy_description=vacancy.description or "",
            stored_text=evaluation.cover_letter,
            strengths=strengths,
        ).strip()
        facts = legacy_cover_fact_bank(
            vacancy_title=vacancy.title,
            vacancy_description=vacancy.description or "",
            stored_text=evaluation.cover_letter,
            strengths=strengths,
        )
        letter = _write_letter(
            account_key="old",
            vacancy=vacancy,
            safe_draft=safe_draft,
            facts=facts,
            recent=recent,
        )
        if not letter:
            continue

        evaluation.cover_letter = letter
        evaluation.cover_letter_version = COVER_WRITER_VERSION
        session.commit()
        _remember(recent, letter)
        prepared += 1
        print(
            "[COVER PREP] OLD "
            f"vacancy={vacancy.id} evaluation={evaluation.id}"
        )

    return prepared, skipped


def _prepare_clean(session) -> tuple[int, int]:
    context = current_policy_context()
    latest_shadow_id = (
        select(func.max(CleanShadowAssessment.id))
        .where(
            CleanShadowAssessment.vacancy_id == Vacancy.id,
            *assessment_version_filters(context),
        )
        .correlate(Vacancy)
        .scalar_subquery()
    )
    has_clean_application = (
        select(Application.id)
        .where(
            Application.vacancy_id == Vacancy.id,
            Application.account_key == "clean",
        )
        .exists()
    )
    has_clean_discovery = (
        select(HhVacancyDiscovery.id)
        .where(
            HhVacancyDiscovery.vacancy_id == Vacancy.id,
            HhVacancyDiscovery.account_key == "clean",
        )
        .exists()
    )
    rows = session.execute(
        select(Vacancy, CleanShadowAssessment, Evaluation)
        .join(
            CleanShadowAssessment,
            CleanShadowAssessment.id == latest_shadow_id,
        )
        .join(
            Evaluation,
            Evaluation.id == CleanShadowAssessment.legacy_evaluation_id,
        )
        .where(~has_clean_application)
        .where(Vacancy.source == "hh")
        .where(has_clean_discovery)
        .where(
            CleanShadowAssessment.routing_class.in_(
                CLEAN_ELIGIBLE_ROUTES
            )
        )
        .order_by(
            (
                CleanShadowAssessment.routing_class == "CLEAN_STRONG"
            ).desc(),
            CleanShadowAssessment.invite_score.desc(),
            CleanShadowAssessment.fit_score.desc(),
            CleanShadowAssessment.id.desc(),
        )
        .limit(MAX_PER_ACCOUNT * 2)
    ).all()

    recent = _recent_letters(session, "clean")
    prepared = 0
    skipped = 0

    for vacancy, assessment, evaluation in rows:
        if prepared + skipped >= MAX_PER_ACCOUNT:
            break

        eligibility = clean_eligibility(
            session,
            vacancy.id,
            context=context,
        )
        if (
            not eligibility.eligible
            or eligibility.assessment is None
            or eligibility.assessment.id != assessment.id
        ):
            continue

        current = (assessment.cover_letter or "").strip()
        if (
            assessment.cover_letter_version == COVER_WRITER_VERSION
            and current
        ):
            _remember(recent, current)
            skipped += 1
            continue

        safe_draft = build_clean_cover_letter(
            vacancy_title=vacancy.title,
            vacancy_company=vacancy.company,
            vacancy_description=vacancy.description or "",
            extraction_json=assessment.extraction_json,
        ).strip()
        facts = clean_cover_fact_bank(
            vacancy_title=vacancy.title,
            vacancy_description=vacancy.description or "",
            extraction_json=assessment.extraction_json,
        )
        letter = _write_letter(
            account_key="clean",
            vacancy=vacancy,
            safe_draft=safe_draft,
            facts=facts,
            recent=recent,
        )
        if not letter:
            continue

        assessment.cover_letter = letter
        assessment.cover_letter_version = COVER_WRITER_VERSION
        session.commit()
        _remember(recent, letter)
        prepared += 1
        print(
            "[COVER PREP] CLEAN "
            f"vacancy={vacancy.id} assessment={assessment.id} "
            f"evaluation={evaluation.id}"
        )

    return prepared, skipped


def main() -> int:
    session = SessionLocal()
    try:
        old_prepared, old_skipped = _prepare_old(session)
        clean_prepared, clean_skipped = _prepare_clean(session)
        print(
            "[COVER PREP] done "
            f"version={COVER_WRITER_VERSION} "
            f"old_prepared={old_prepared} old_ready={old_skipped} "
            f"clean_prepared={clean_prepared} clean_ready={clean_skipped}"
        )
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
