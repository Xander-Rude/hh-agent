import json
import os

from sqlalchemy import select

from app.db import (
    SessionLocal,
    Vacancy,
    Evaluation,
)
from app.evaluation_grounding import ground_and_decide
from app.evaluation_policy import apply_management_policy
from app.evaluator import VacancyEvaluator
from app.gpu_guard import should_defer_ollama
from app.hard_filters import apply_hard_filters
from app.llm_resilience import (
    configure_processor_llm_environment,
    is_transient_ollama_failure,
    max_consecutive_llm_defers,
)
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
        must_have_missing=json.dumps([], ensure_ascii=False),
        nice_to_have_missing=json.dumps([], ensure_ascii=False),
        strengths=json.dumps([], ensure_ascii=False),
        gaps=json.dumps([], ensure_ascii=False),
        red_flags=json.dumps([reason], ensure_ascii=False),
        summary="Вакансия отклонена жёстким фильтром до анализа LLM.",
        recommendation=f"Пропустить. Причина: {reason}",
        cover_letter="",
        model=f"hard-filter/{model_name}",
    )
    session.add(evaluation)
    vacancy.processed = True
    session.commit()


def build_vacancy_text(vacancy: Vacancy) -> str:
    salary_parts = []
    if vacancy.salary_from is not None:
        salary_parts.append(f"от {vacancy.salary_from}")
    if vacancy.salary_to is not None:
        salary_parts.append(f"до {vacancy.salary_to}")
    if vacancy.salary_currency:
        salary_parts.append(vacancy.salary_currency)
    salary_text = " ".join(salary_parts) if salary_parts else "Не указана"

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


def _print_gpu_status(status) -> None:
    print(
        "  [GPU] "
        f"index={status.index} "
        f"utilization={status.utilization_percent}% "
        f"vram={status.memory_used_mb}/{status.memory_total_mb} MB "
        f"free={status.memory_free_mb} MB"
    )


def main() -> None:
    session = SessionLocal()

    processor_llm_timeout, processor_llm_retries = (
        configure_processor_llm_environment()
    )
    evaluator = VacancyEvaluator()
    preferences = load_preferences()

    print(
        "[LLM RESILIENCE] "
        f"timeout={processor_llm_timeout:g}s "
        f"transport_retries={processor_llm_retries} "
        f"max_consecutive_defers={max_consecutive_llm_defers()}"
    )

    with open("data/resume.txt", "r", encoding="utf-8") as file:
        resume = file.read()

    model_name = os.getenv("LLM_MODEL", "gemma4:12b")

    vacancies = session.scalars(
        select(Vacancy)
        .where(Vacancy.processed.is_(False))
        .order_by(Vacancy.found_at.asc())
    ).all()

    total = len(vacancies)
    print(f"Вакансий на обработку: {total}")

    processed_by_llm = 0
    rejected_by_filter = 0
    failed = 0
    deferred_pending = 0
    deferred_by_llm = 0
    consecutive_llm_defers = 0
    llm_circuit_open = False
    llm_circuit_reason = None
    gpu_warning_printed = False

    for index, vacancy in enumerate(vacancies, start=1):
        print()
        print(f"[{index}/{total}] {vacancy.title} | {vacancy.company or '-'}")

        try:
            hard_filter = apply_hard_filters(
                title=vacancy.title,
                company=vacancy.company,
                description=vacancy.description,
                salary_from=vacancy.salary_from,
                salary_to=vacancy.salary_to,
                salary_currency=vacancy.salary_currency,
                preferences=preferences,
            )

            if not hard_filter.passed:
                reason = hard_filter.reason or "Жёсткий фильтр"
                print(f"  HARD REJECT: {reason}")
                create_hard_reject_evaluation(
                    session=session,
                    vacancy=vacancy,
                    reason=reason,
                    model_name=model_name,
                )
                rejected_by_filter += 1
                continue

            if llm_circuit_open:
                deferred_by_llm += 1
                print(
                    "  [LLM DEFER] Ollama временно признана недоступной "
                    "в этом проходе. Вакансия остаётся pending."
                )
                continue

            gpu_decision = should_defer_ollama()

            if gpu_decision.warning and not gpu_warning_printed:
                print(f"  [GPU WARN] {gpu_decision.warning}")
                gpu_warning_printed = True

            if gpu_decision.defer:
                if gpu_decision.status is not None:
                    _print_gpu_status(gpu_decision.status)

                deferred_pending = total - index + 1
                reason = gpu_decision.reason or "GPU занят"
                print(f"  [DEFER] {reason}")
                print(
                    "  [DEFER] Ollama scoring отложен. "
                    f"Текущая и оставшиеся вакансии ({deferred_pending}) "
                    "останутся pending до следующего запуска."
                )
                break

            vacancy_text = build_vacancy_text(vacancy)
            result = evaluator.evaluate(
                resume=resume,
                vacancy=vacancy_text,
                preferences=preferences,
            )

            result = ground_and_decide(result, vacancy=vacancy_text)
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
                        vacancy_description=vacancy.description or "",
                        vacancy_score=int(result.score or 0),
                    )
                    selected_resume_key = resume_decision.selected_resume_key or None
                    selected_resume_title = resume_decision.selected_resume_title or None
                    selected_resume_id = resume_decision.selected_resume_id or None
                    selected_resume_score = int(resume_decision.match_score or 0)
                    print(
                        "  RESUME: "
                        f"{selected_resume_title} | match={selected_resume_score}%"
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
                seniority_match=result.seniority_match,
                domain_match=result.domain_match,
                responsibility_match=result.responsibility_match,
                must_have_missing=json.dumps(result.must_have_missing, ensure_ascii=False),
                nice_to_have_missing=json.dumps(result.nice_to_have_missing, ensure_ascii=False),
                strengths=json.dumps(result.strengths, ensure_ascii=False),
                gaps=json.dumps(result.gaps, ensure_ascii=False),
                red_flags=json.dumps(result.red_flags, ensure_ascii=False),
                summary=result.summary,
                recommendation=result.recommendation,
                cover_letter=result.cover_letter,
                selected_resume_key=selected_resume_key,
                selected_resume_title=selected_resume_title,
                selected_resume_id=selected_resume_id,
                selected_resume_score=selected_resume_score,
                model=model_name,
            )

            session.add(evaluation)
            vacancy.processed = True

            # Application здесь намеренно НЕ создаётся ни для HH, ни для Yandex.
            # Её создаёт Telegram при показе вакансии со status=notified.
            # Только нажатие «Откликнуться» переводит её в approved.
            session.commit()

            print(f"  SCORE: {result.score} | {result.decision.upper()}")
            print(
                f"  role={result.role_match}"
                f" seniority={result.seniority_match}"
                f" domain={result.domain_match}"
                f" responsibility={result.responsibility_match}"
            )
            print(f"  {result.recommendation}")
            processed_by_llm += 1
            consecutive_llm_defers = 0

        except Exception as exc:
            session.rollback()

            if is_transient_ollama_failure(exc):
                deferred_by_llm += 1
                consecutive_llm_defers += 1
                print(
                    "  [LLM DEFER] Ollama не ответила в отведённое время. "
                    "Вакансия остаётся pending; processor идёт дальше."
                )
                print(
                    "  [LLM DEFER] "
                    f"consecutive={consecutive_llm_defers}/"
                    f"{max_consecutive_llm_defers()}"
                )

                if consecutive_llm_defers >= max_consecutive_llm_defers():
                    llm_circuit_open = True
                    llm_circuit_reason = (
                        "слишком много подряд transport/timeout ошибок Ollama"
                    )
                    print(
                        "  [LLM CIRCUIT] Ollama отключена до конца текущего "
                        "прохода. Hard filters продолжат работу, а вакансии, "
                        "которым нужна LLM, останутся pending."
                    )

                continue

            print(f"  [ERROR] {type(exc).__name__}: {exc}")
            failed += 1

    session.close()

    print()
    print("=" * 60)
    print("Готово.")
    print(f"Прошли через LLM: {processed_by_llm}")
    print(f"Отброшено hard filters: {rejected_by_filter}")
    print(f"Отложено из-за ошибок Ollama: {deferred_by_llm}")
    print(f"Ошибок: {failed}")
    if llm_circuit_open:
        print(f"LLM circuit: OPEN ({llm_circuit_reason})")
    if deferred_pending:
        print(f"Осталось pending из-за занятого GPU: {deferred_pending}")
    print("=" * 60)


if __name__ == "__main__":
    main()
