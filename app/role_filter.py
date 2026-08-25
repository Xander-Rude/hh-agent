from dataclasses import dataclass
import re


@dataclass
class RoleFilterResult:
    passed: bool
    reason: str | None = None


def normalize(text: str) -> str:
    return " ".join(text.lower().split())


DEFAULT_ALLOWED_MARKERS = [
    "product manager",
    "senior product manager",
    "lead product manager",
    "product lead",
    "head of product",
    "product owner",
    "delivery manager",
    "delivery lead",
    "project manager",
    "technical project manager",
    "program manager",
    "руководитель проекта",
    "руководитель проектов",
    "менеджер проектов",
    "менеджер it-проектов",
    "менеджер it проектов",
    "технический менеджер проектов",
    "технический менеджер",
    "руководитель проектного офиса",
    "менеджер продукта",
    "продуктовый менеджер",
    "руководитель продукта",
    "head of pmo",
    "pmo",
    "бизнес-партнер",
    "бизнес партнер",
]

# Нужны для названий, где между "менеджер" и "продукт" стоит сегмент
# или специализация, например "Менеджер B2B-продуктов".
DEFAULT_ALLOWED_PATTERNS = [
    re.compile(
        r"\bменеджер\s+(?:[a-zа-я0-9]+[-‑–—])?продукт(?:а|ов)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:старший\s+)?продуктов(?:ый|ого)\s+менеджер\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bруководител[ья]\s+продукт(?:а|ов)\b",
        re.IGNORECASE,
    ),
]

DEFAULT_BLOCKED_MARKERS = [
    "стажёр",
    "стажер",
    "intern",
    "internship",
    "cto",
    "chief technology officer",
    "tech lead",
    "technical lead",
    "team lead developer",
    "developer",
    "разработчик",
    "программист",
    "software engineer",
    "data scientist",
    "data analyst",
    "аналитик данных",
    "системный аналитик",
    "бизнес-аналитик",
    "business analyst",
    "solution architect",
    "software architect",
    "архитектор",
    "qa engineer",
    "тестировщик",
]


def check_role_title(
    title: str,
    preferences: dict,
) -> RoleFilterResult:
    normalized_title = normalize(title)

    # Пользовательские списки дополняют безопасные базовые маркеры, а не
    # полностью заменяют их. Иначе локальный preferences.yaml может случайно
    # отключить поддержку новых корректных названий ролей.
    custom_blocked = preferences.get("blocked_role_markers", []) or []
    blocked = [*DEFAULT_BLOCKED_MARKERS, *custom_blocked]

    seen_blocked: set[str] = set()
    for marker in blocked:
        normalized_marker = normalize(str(marker))
        if not normalized_marker or normalized_marker in seen_blocked:
            continue
        seen_blocked.add(normalized_marker)
        if normalized_marker in normalized_title:
            return RoleFilterResult(
                passed=False,
                reason=f"Неподходящая роль: {marker}",
            )

    for pattern in DEFAULT_ALLOWED_PATTERNS:
        if pattern.search(normalized_title):
            return RoleFilterResult(
                passed=True,
            )

    custom_allowed = preferences.get("allowed_role_markers", []) or []
    allowed = [*DEFAULT_ALLOWED_MARKERS, *custom_allowed]

    seen_allowed: set[str] = set()
    for marker in allowed:
        normalized_marker = normalize(str(marker))
        if not normalized_marker or normalized_marker in seen_allowed:
            continue
        seen_allowed.add(normalized_marker)
        if normalized_marker in normalized_title:
            return RoleFilterResult(
                passed=True,
            )

    return RoleFilterResult(
        passed=False,
        reason=(
            f"Название роли не соответствует целевому профилю: "
            f"{title}"
        ),
    )
