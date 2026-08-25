import json

from sqlalchemy import select

from app.db import (
    Evaluation,
    SessionLocal,
    Vacancy,
)


def parse_list(
    value: str,
) -> list[str]:

    try:
        result = json.loads(
            value
        )

        if isinstance(
            result,
            list,
        ):
            return result

    except Exception:
        pass

    return []


def print_list(
    value: str,
    limit: int = 4,
) -> None:

    items = parse_list(
        value
    )

    if not items:
        print("    -")
        return

    for item in items[:limit]:
        print(
            f"    • {item}"
        )


def salary_text(
    vacancy: Vacancy,
) -> str | None:

    if (
        vacancy.salary_from is None
        and vacancy.salary_to is None
    ):
        return None

    parts = []

    if vacancy.salary_from is not None:
        parts.append(
            f"от {vacancy.salary_from:,}"
            .replace(
                ",",
                " ",
            )
        )

    if vacancy.salary_to is not None:
        parts.append(
            f"до {vacancy.salary_to:,}"
            .replace(
                ",",
                " ",
            )
        )

    if vacancy.salary_currency:
        parts.append(
            vacancy.salary_currency
        )

    return " ".join(
        parts
    )


def print_vacancy(
    number: int,
    vacancy: Vacancy,
    evaluation: Evaluation,
) -> None:

    print()
    print(
        f"#{number} | "
        f"{evaluation.score}/100 | "
        f"{evaluation.decision.upper()}"
    )

    print(
        vacancy.title
    )

    print(
        vacancy.company
        or "-"
    )

    salary = salary_text(
        vacancy
    )

    if salary:
        print(
            f"Зарплата: {salary}"
        )

    print(
        "MATCH:",
        f"role={evaluation.role_match}",
        f"seniority={evaluation.seniority_match}",
        f"domain={evaluation.domain_match}",
        (
            "responsibility="
            f"{evaluation.responsibility_match}"
        ),
    )

    must_have = parse_list(
        evaluation.must_have_missing
    )

    red_flags = parse_list(
        evaluation.red_flags
    )

    if must_have:
        print()
        print(
            f"MUST-HAVE MISSING: "
            f"{len(must_have)}"
        )

        for item in must_have[:4]:
            print(
                f"    ⚠ {item}"
            )

    if red_flags:
        print()
        print(
            f"RED FLAGS: "
            f"{len(red_flags)}"
        )

        for item in red_flags[:4]:
            print(
                f"    ⚠ {item}"
            )

    print()
    print(
        "Почему подходит:"
    )

    print_list(
        evaluation.strengths
    )

    print()
    print(
        "Пробелы:"
    )

    print_list(
        evaluation.gaps
    )

    print()
    print(
        "Рекомендация:",
        evaluation.recommendation,
    )

    if evaluation.cover_letter:
        print()
        print(
            "Сопроводительное:"
        )

        print(
            evaluation.cover_letter
        )

    print()
    print(
        "URL:",
        vacancy.url,
    )

    print(
        "-" * 88
    )


def main() -> None:
    session = SessionLocal()

    rows = session.execute(
        select(
            Vacancy,
            Evaluation,
        )
        .join(
            Evaluation,
            Evaluation.vacancy_id
            == Vacancy.id,
        )
        .order_by(
            Evaluation.score.desc(),
            Evaluation.responsibility_match.desc(),
            Evaluation.role_match.desc(),
        )
    ).all()

    apply_rows = []
    review_rows = []
    reject_rows = []
    hard_reject_rows = []

    for vacancy, evaluation in rows:

        if evaluation.model.startswith(
            "hard-filter/"
        ):
            hard_reject_rows.append(
                (
                    vacancy,
                    evaluation,
                )
            )

        elif (
            evaluation.decision
            == "apply"
        ):
            apply_rows.append(
                (
                    vacancy,
                    evaluation,
                )
            )

        elif (
            evaluation.decision
            == "review"
        ):
            review_rows.append(
                (
                    vacancy,
                    evaluation,
                )
            )

        else:
            reject_rows.append(
                (
                    vacancy,
                    evaluation,
                )
            )

    print()
    print(
        "=" * 88
    )

    print(
        "HH AGENT — РЕЗУЛЬТАТЫ"
    )

    print(
        "=" * 88
    )

    print()
    print(
        f"Всего вакансий: "
        f"{len(rows)}"
    )

    print(
        f"APPLY: "
        f"{len(apply_rows)}"
    )

    print(
        f"REVIEW: "
        f"{len(review_rows)}"
    )

    print(
        f"REJECT после LLM: "
        f"{len(reject_rows)}"
    )

    print(
        f"HARD REJECT: "
        f"{len(hard_reject_rows)}"
    )

    if apply_rows:
        print()
        print()
        print(
            "=" * 88
        )

        print(
            "🔥 APPLY — ПРИОРИТЕТНЫЕ ВАКАНСИИ"
        )

        print(
            "=" * 88
        )

        for number, (
            vacancy,
            evaluation,
        ) in enumerate(
            apply_rows,
            start=1,
        ):
            print_vacancy(
                number=number,
                vacancy=vacancy,
                evaluation=evaluation,
            )

    if review_rows:
        print()
        print()
        print(
            "=" * 88
        )

        print(
            "👀 REVIEW — ПОСМОТРЕТЬ ВРУЧНУЮ"
        )

        print(
            "=" * 88
        )

        for number, (
            vacancy,
            evaluation,
        ) in enumerate(
            review_rows,
            start=1,
        ):
            print_vacancy(
                number=number,
                vacancy=vacancy,
                evaluation=evaluation,
            )

    if reject_rows:
        print()
        print()
        print(
            "=" * 88
        )

        print(
            "❌ REJECT ПОСЛЕ LLM"
        )

        print(
            "=" * 88
        )

        for number, (
            vacancy,
            evaluation,
        ) in enumerate(
            reject_rows,
            start=1,
        ):
            print()
            print(
                f"#{number} | "
                f"{evaluation.score}/100 | "
                f"{vacancy.title} | "
                f"{vacancy.company or '-'}"
            )

            print(
                evaluation.recommendation
            )

            print(
                vacancy.url
            )

    if hard_reject_rows:
        print()
        print()
        print(
            "=" * 88
        )

        print(
            "🧱 HARD REJECT — ОТСЕЯНЫ ДО LLM"
        )

        print(
            "=" * 88
        )

        for number, (
            vacancy,
            evaluation,
        ) in enumerate(
            hard_reject_rows,
            start=1,
        ):

            flags = parse_list(
                evaluation.red_flags
            )

            reason = (
                flags[0]
                if flags
                else evaluation.recommendation
            )

            print(
                f"#{number} | "
                f"{vacancy.title} | "
                f"{vacancy.company or '-'}"
            )

            print(
                f"    Причина: {reason}"
            )

    session.close()


if __name__ == "__main__":
    main()