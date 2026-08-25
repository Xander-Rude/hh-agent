import json
import os

from sqlalchemy import select

from app.db import (
    Application,
    SessionLocal,
    Vacancy,
    Evaluation,
)
from app.evaluation_policy import apply_management_policy
from app.evaluator import VacancyEvaluator
from app.hard_filters import apply_hard_filters
from app.preferences import load_preferences
from app.resume_matcher import match_resume


def create_hard_reject_evaluation(
    session,
    vacancy: Vacancy,
    reason: str,
    model_name: str,
) -> None:
    evaluation = Evaluation(
        vacancy_id=vacancy.id,

        score=0,
        decision="reject",

        role_match=0,
        seniority_match=0,
        domain_match=0,
        responsibility_match=0,

        must_have_missing=json.dumps(
            [],
            ensure_ascii=False,
        ),

        nice_to_have_missing=json.dumps(
            [],
            ensure_ascii=False,
        ),

        strengths=json.dumps(
            [],
            ensure_ascii=False,
        ),

        gaps=json.dumps(
            [],
            ensure_ascii=False,
        ),

        red_flags=json.dumps(
            [reason],
            ensure_ascii=False,
        ),

        summary=(
            "Вакансия отклонена "
            "жёстким фильтром до анализа LLM."
        ),

        recommendation=(
            f"Пропустить. Причина: {reason}"
        ),

        cover_letter="",

        model=f"hard-filter/{model_name}",
    )

    session.add(
        evaluation
    )

    vacancy.processed = True

    session.commit()


def ensure_yandex_application(
    session,
    vacancy: Vacancy,
    evaluation: Evaluation,
) -> Application | None:
    """Создаёт approved-заявку для подходящей Yandex-вакансии.

    Существующие Application не меняются: это защищает applied/manual_required/
    apply_error и ручные решения от случайного перезаписывания.
    """
    if (vacancy.source or "").strip().lower() != "yandex":
        return None

    if (evaluation.decision or "").strip().lower() == "reject":
        return None

    cover_letter = (evaluation.cover_letter or "").strip()
    resume_key = (evaluation.selected_resume_key or "").strip()

    if not cover_letter:
        print("  [WARN] YANDEX APPLICATION: нет сопроводительного — не создаю")
        return None

    if not resume_key:
        print("  [WARN] YANDEX APPLICATION: не выбрано резюме — не создаю")
        return None

    existing = session.scalars(
        select(Application)
        .where(Application.vacancy_id == vacancy.id)
        .order_by(Application.created_at.asc(), Application.id.asc())
        .limit(1)
    ).first()

    if existing is not None:
        print(
            "  YANDEX APPLICATION: уже существует "
            f"id={existing.id} status={existing.status}"
        )
        return existing

    application = Application(
        vacancy_id=vacancy.id,
        status="approved",
        cover_letter=cover_letter,
        selected_resume_key=evaluation.selected_resume_key,
        selected_resume_title=evaluation.selected_resume_title,
        selected_resume_id=evaluation.selected_resume_id,
        selected_resume_score=evaluation.selected_resume_score,
    )
    session.add(application)
    session.flush()

    print(
        "  YANDEX APPLICATION: создана "
        f"id={application.id} status=approved"
    )
    return application


def build_vacancy_text(
    vacancy: Vacancy,
) -> str:
    salary_parts = []

    if vacancy.salary_from is not None:
        salary_parts.append(
            f"от {vacancy.salary_from}"
        )

    if vacancy.salary_to is not None:
        salary_parts.append(
            f"до {vacancy.salary_to}"
        )

    if vacancy.salary_currency:
        salary_parts.append(
            vacancy.salary_currency
        )

    salary_text = (
        " ".join(salary_parts)
        if salary_parts
        else "Не указана"
    )

    return f"""
Название:
{vacancy.title}

Компания:
{vacancy.company or "Не указана"}

Зарплата:
{salary_text}

Описание:
{vacancy.description}
""".strip()


def main() -> None:
    session = SessionLocal()

    evaluator = VacancyEvaluator()
    preferences = load_preferences()

    resume_path = "data/resume.txt"

    with open(
        resume_path,
        "r",
        encoding="utf-8",
    ) as file:
        resume = file.read()

    model_name = os.getenv(
        "LLM_MODEL",
        "gemma3:12b-it-qat",
    )

    vacancies = session.scalars(
        select(Vacancy)
        .where(
            Vacancy.processed.is_(False)
        )
        .order_by(
            Vacancy.found_at.asc()
        )
    ).all()

    total = len(vacancies)

    print(
        f"Вакансий на обработку: {total}"
    )

    processed_by_llm = 0
    rejected_by_filter = 0
    failed = 0

    for index, vacancy in enumerate(
        vacancies,
        start=1,
    ):
        print()
        print(
            f"[{index}/{total}] "
            f"{vacancy.title} | "
            f"{vacancy.company or '-'}"
        )

        try:
            hard_filter = apply_hard_filters(
                title=vacancy.title,
                company=vacancy.company,
                description=vacancy.description,
                salary_from=vacancy.salary_from,
                salary_to=vacancy.salary_to,
                salary_currency=(
                    vacancy.salary_currency
                ),
                preferences=preferences,
            )

            if not hard_filter.passed:
                reason = (
                    hard_filter.reason
                    or "Жёсткий фильтр"
                )

                print(
                    f"  HARD REJECT: {reason}"
                )

                create_hard_reject_evaluation(
                    session=session,
                    vacancy=vacancy,
                    reason=reason,
                    model_name=model_name,
                )

                rejected_by_filter += 1
                continue

            vacancy_text = build_vacancy_text(
                vacancy
            )

            result = evaluator.evaluate(
                resume=resume,
                vacancy=vacancy_text,
                preferences=preferences,
            )

            result = apply_management_policy(
                result,
                resume=resume,
                vacancy=vacancy_text,
            )

            selected_resume_key = None
            selected_resume_title = None
            selected_resume_id = None
            selected_resume_score = None

            if result.decision != "reject":
                try:
                    resume_decision = match_resume(
                        vacancy_title=vacancy.title or "",
                        vacancy_description=(
                            vacancy.description
                            or ""
                        ),
                        vacancy_score=int(
                            result.score
                            or 0
                        ),
                    )

                    selected_resume_key = (
                        resume_decision.selected_resume_key
                        or None
                    )
                    selected_resume_title = (
                        resume_decision.selected_resume_title
                        or None
                    )
                    selected_resume_id = (
                        resume_decision.selected_resume_id
                        or None
                    )
                    selected_resume_score = int(
                        resume_decision.match_score
                        or 0
                    )

                    print(
                        "  RESUME: "
                        f"{selected_resume_title} | "
                        f"match={selected_resume_score}%"
                    )

                except Exception as exc:
                    print(
                        "  [WARN] Resume matcher failed: "
                        f"{type(exc).__name__}: {exc}"
                    )

            evaluation = Evaluation(
                vacancy_id=vacancy.id,

                score=result.score,
                decision=result.decision,

                role_match=result.role_match,
                seniority_match=(
                    result.seniority_match
                ),
                domain_match=result.domain_match,
                responsibility_match=(
                    result.responsibility_match
                ),

                must_have_missing=json.dumps(
                    result.must_have_missing,
                    ensure_ascii=False,
                ),

                nice_to_have_missing=json.dumps(
                    result.nice_to_have_missing,
                    ensure_ascii=False,
                ),

                strengths=json.dumps(
                    result.strengths,
                    ensure_ascii=False,
                ),

                gaps=json.dumps(
                    result.gaps,
                    ensure_ascii=False,
                ),

                red_flags=json.dumps(
                    result.red_flags,
                    ensure_ascii=False,
                ),

                summary=result.summary,

                recommendation=(
                    result.recommendation
                ),

                cover_letter=(
                    result.cover_letter
                ),

                selected_resume_key=(
                    selected_resume_key
                ),
                selected_resume_title=(
                    selected_resume_title
                ),
                selected_resume_id=(
                    selected_resume_id
                ),
                selected_resume_score=(
                    selected_resume_score
                ),

                model=model_name,
            )

            session.add(
                evaluation
            )

            vacancy.processed = True

            # Yandex-очередь формируется сразу из успешной Evaluation.
            # Для HH существующий workflow не меняем.
            ensure_yandex_application(
                session=session,
                vacancy=vacancy,
                evaluation=evaluation,
            )

            session.commit()

            print(
                f"  SCORE: {result.score}"
                f" | {result.decision.upper()}"
            )

            print(
                f"  role={result.role_match}"
                f" seniority={result.seniority_match}"
                f" domain={result.domain_match}"
                f" responsibility="
                f"{result.responsibility_match}"
            )

            print(
                f"  {result.recommendation}"
            )

            processed_by_llm += 1

        except Exception as exc:
            session.rollback()

            print(
                f"  [ERROR] "
                f"{type(exc).__name__}: {exc}"
            )

            failed += 1

    session.close()

    print()
    print("=" * 60)
    print("Готово.")
    print(
        f"Прошли через LLM: "
        f"{processed_by_llm}"
    )
    print(
        f"Отброшено hard filters: "
        f"{rejected_by_filter}"
    )
    print(
        f"Ошибок: {failed}"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()
