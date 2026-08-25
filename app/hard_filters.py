from dataclasses import dataclass
from typing import Any

from app.role_filter import check_role_title


@dataclass
class HardFilterResult:
    passed: bool
    reason: str | None = None


def normalize_text(value: str | None) -> str:
    if not value:
        return ""

    return " ".join(
        value.lower().split()
    )


def check_salary(
    salary_from: int | None,
    salary_to: int | None,
    salary_currency: str | None,
    preferences: dict[str, Any],
) -> HardFilterResult:
    salary_preferences = preferences.get(
        "salary",
    )

    if isinstance(
        salary_preferences,
        dict,
    ):
        minimum = salary_preferences.get(
            "minimum",
        )

        required_currency = salary_preferences.get(
            "currency",
            preferences.get(
                "currency",
                "RUB",
            ),
        )
    else:
        minimum = salary_preferences

        required_currency = preferences.get(
            "currency",
            "RUB",
        )

    if not minimum:
        return HardFilterResult(
            passed=True,
        )

    # Если зарплата не указана — не режем вакансию.
    if salary_from is None and salary_to is None:
        return HardFilterResult(
            passed=True,
        )

    # Пока не конвертируем валюты автоматически.
    if (
        salary_currency
        and required_currency
        and salary_currency.upper()
        != required_currency.upper()
    ):
        return HardFilterResult(
            passed=True,
        )

    # Верхняя граница ниже нашего минимума.
    if (
        salary_to is not None
        and salary_to < minimum
    ):
        return HardFilterResult(
            passed=False,
            reason=(
                f"Зарплата до {salary_to} "
                f"{salary_currency or ''} ниже минимума "
                f"{minimum} {required_currency}"
            ),
        )

    # Если указана только нижняя граница
    # и она ниже нашего минимума.
    if (
        salary_from is not None
        and salary_to is None
        and salary_from < minimum
    ):
        return HardFilterResult(
            passed=False,
            reason=(
                f"Зарплата от {salary_from} "
                f"{salary_currency or ''} ниже минимума "
                f"{minimum} {required_currency}"
            ),
        )

    return HardFilterResult(
        passed=True,
    )


def check_blacklist_words(
    title: str,
    description: str,
    preferences: dict[str, Any],
) -> HardFilterResult:
    blacklist_words = preferences.get(
        "blacklist_words",
        [],
    )

    # Hard filter должен быть высокоточным. Стоп-слово в длинном описании
    # (например, «продажи» в контексте соседней команды или продукта) не означает,
    # что сама вакансия относится к продажам. Поэтому здесь проверяем только title;
    # содержание вакансии дальше оценивает LLM.
    haystack = normalize_text(title)

    for word in blacklist_words:
        normalized_word = normalize_text(
            str(word)
        )

        if (
            normalized_word
            and normalized_word in haystack
        ):
            return HardFilterResult(
                passed=False,
                reason=(
                    f"Найдено стоп-слово в названии: {word}"
                ),
            )

    return HardFilterResult(
        passed=True,
    )


def check_blacklist_company(
    company: str | None,
    preferences: dict[str, Any],
) -> HardFilterResult:
    blacklist_companies = preferences.get(
        "blacklist_companies",
        [],
    )

    company_normalized = normalize_text(
        company
    )

    if not company_normalized:
        return HardFilterResult(
            passed=True,
        )

    for blocked_company in blacklist_companies:
        blocked_normalized = normalize_text(
            str(blocked_company)
        )

        if (
            blocked_normalized
            and blocked_normalized
            in company_normalized
        ):
            return HardFilterResult(
                passed=False,
                reason=(
                    f"Компания в blacklist: "
                    f"{blocked_company}"
                ),
            )

    return HardFilterResult(
        passed=True,
    )


def check_unwanted_domains(
    title: str,
    description: str,
    preferences: dict[str, Any],
) -> HardFilterResult:
    unwanted_domains = preferences.get(
        "unwanted_domains",
        [],
    )

    haystack = normalize_text(
        f"{title} {description}"
    )

    domain_markers = {
        "gambling": [
            "gambling",
            "betting",
            "букмекер",
            "ставки на спорт",
            "казино",
        ],
        "crypto": [
            "crypto",
            "cryptocurrency",
            "криптовалют",
            "крипто",
            "web3",
        ],
        "adult": [
            "adult",
            "18+",
        ],
    }

    for domain in unwanted_domains:
        normalized_domain = normalize_text(
            str(domain)
        )

        markers = domain_markers.get(
            normalized_domain,
            [normalized_domain],
        )

        for marker in markers:
            if marker and marker in haystack:
                return HardFilterResult(
                    passed=False,
                    reason=(
                        f"Нежелательный домен: "
                        f"{domain}"
                    ),
                )

    return HardFilterResult(
        passed=True,
    )


def apply_hard_filters(
    title: str,
    company: str | None,
    description: str,
    salary_from: int | None,
    salary_to: int | None,
    salary_currency: str | None,
    preferences: dict[str, Any],
) -> HardFilterResult:
    checks = [
        # Сначала проверяем саму профессию.
        check_role_title(
            title=title,
            preferences=preferences,
        ),

        check_salary(
            salary_from=salary_from,
            salary_to=salary_to,
            salary_currency=salary_currency,
            preferences=preferences,
        ),

        check_blacklist_company(
            company=company,
            preferences=preferences,
        ),

        check_blacklist_words(
            title=title,
            description=description,
            preferences=preferences,
        ),

        check_unwanted_domains(
            title=title,
            description=description,
            preferences=preferences,
        ),
    ]

    for result in checks:
        if not result.passed:
            return result

    return HardFilterResult(
        passed=True,
    )
