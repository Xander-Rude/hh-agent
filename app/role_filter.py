from dataclasses import dataclass


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
    "head of product",
    "product owner",
    "delivery manager",
    "delivery lead",
    "project manager",
    "program manager",
    "руководитель проекта",
    "руководитель проектов",
    "менеджер проектов",
    "руководитель проектного офиса",
    "head of pmo",
    "pmo",
    "бизнес-партнер",
    "бизнес партнер",
]

DEFAULT_BLOCKED_MARKERS = [
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

    blocked = preferences.get(
        "blocked_role_markers",
        DEFAULT_BLOCKED_MARKERS,
    )

    for marker in blocked:
        if normalize(str(marker)) in normalized_title:
            return RoleFilterResult(
                passed=False,
                reason=f"Неподходящая роль: {marker}",
            )

    allowed = preferences.get(
        "allowed_role_markers",
        DEFAULT_ALLOWED_MARKERS,
    )

    if any(
        normalize(str(marker)) in normalized_title
        for marker in allowed
    ):
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