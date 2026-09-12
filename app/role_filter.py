from dataclasses import dataclass
import re


@dataclass
class RoleFilterResult:
    passed: bool
    reason: str | None = None


def normalize(text: str) -> str:
    normalized = (
        text.lower()
        .replace("ё", "е")
        .replace("‑", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("&", " and ")
    )
    normalized = re.sub(r"[,;:/()]+", " ", normalized)
    return " ".join(normalized.split())


def marker_matches(title: str, marker: str) -> bool:
    """Match role markers as complete terms, not arbitrary substrings."""
    normalized_title = normalize(title)
    normalized_marker = normalize(marker)

    if not normalized_marker:
        return False

    pattern = re.compile(
        rf"(?<!\w){re.escape(normalized_marker)}(?!\w)",
        re.IGNORECASE,
    )
    return bool(pattern.search(normalized_title))


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

    # C-level technology / transformation roles.
    "cto",
    "chief technology officer",
    "chief technical officer",
    "cio",
    "chief information officer",
    "chief information technology officer",
    "cdto",
    "chief digital officer",
    "chief digital transformation officer",
    "chief digital and technology officer",
    "chief transformation officer",

    # Common English senior-leadership variants.
    "vp technology",
    "vp of technology",
    "vp engineering",
    "vp of engineering",
    "vp it",
    "vp of it",
    "svp technology",
    "svp engineering",
    "svp it",
    "vice president of technology",
    "vice president of engineering",
    "vice president of it",
    "senior vice president of technology",
    "senior vice president of engineering",
    "senior vice president of it",
    "it director",
    "director of it",
    "director of information technology",
    "technology director",
    "director of technology",
    "technical director",
    "engineering director",
    "director of engineering",
    "head of it",
    "head of information technology",
    "head of technology",
    "head of engineering",
    "head of digital",
    "head of digital transformation",
    "head of transformation",
    "director of transformation",
    "transformation director",
    "head of infrastructure",
    "director of infrastructure",
    "head of platform engineering",
    "director of platform engineering",
    "head of information systems",
    "director of information systems",

    # Common Russian executive / technology variants.
    "it-директор",
    "it директор",
    "ит-директор",
    "ит директор",
    "директор it",
    "директор ит",
    "директор по it",
    "директор по ит",
    "директор по информационным технологиям",
    "директор по информационным системам",
    "директор по технологиям",
    "технический директор",
    "директор по цифровой трансформации",
    "директор по цифровизации",
    "директор по цифровому развитию",
    "директор по трансформации",
    "директор по автоматизации",
    "директор по разработке",
    "руководитель it",
    "руководитель ит",
    "руководитель информационных технологий",
    "руководитель информационных систем",
    "руководитель цифровой трансформации",
    "руководитель трансформации",
    "руководитель автоматизации",
    "руководитель разработки",
    "руководитель it-службы",
    "руководитель ит-службы",
    "руководитель службы it",
    "руководитель службы ит",
    "руководитель службы информационных технологий",
]

# Executive technology titles get an explicit pass before IC-level blocked
# markers. This prevents titles such as "Director of Developer Platform" from
# being rejected merely because they contain the word "developer".
EXECUTIVE_ALLOWED_PATTERNS = [
    re.compile(
        r"\b(?:head|director|managing director|executive director|"
        r"vice president|senior vice president|vp|svp)\s+(?:of\s+)?"
        r"(?:it|information technology|technology|engineering|software engineering|"
        r"digital(?: transformation)?|transformation|information systems|"
        r"it infrastructure|technology infrastructure|infrastructure|"
        r"platform(?: engineering)?|developer platform|enterprise applications?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:заместитель\s+)?(?:руководитель|директор|начальник|глава)\s+"
        r"(?:(?:департамента|управления|службы|отдела|направления|центра)\s+)?"
        r"(?:по\s+)?(?:развитию\s+)?"
        r"(?:it|ит|информационн\w*\s+технолог\w*|информационн\w*\s+систем\w*|"
        r"технолог\w*|цифров\w+(?:\s+(?:трансформац\w*|развити\w*|технолог\w*))?|"
        r"трансформац\w*|автоматизац\w*|разработк\w*|"
        r"(?:it|ит)[- ]инфраструктур\w*|платформ\w*)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:заместитель\s+)?(?:руководитель|директор|начальник|глава)\s+"
        r"(?:it|ит)[- ]?(?:департамента|управления|службы|отдела|направления|центра)\b",
        re.IGNORECASE,
    ),
]

# Нужны для названий, где между "менеджер" и "продукт" стоит сегмент
# или специализация, например "Менеджер B2B-продуктов".
DEFAULT_ALLOWED_PATTERNS = [
    re.compile(
        r"\bменеджер\s+(?:[a-zа-я0-9]+[-])?продукт(?:а|ов)\b",
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
    "стажер",
    "intern",
    "internship",
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

    # High-level technology leadership is intentionally evaluated before
    # IC-level exclusions: seniority itself must never be a hard reject.
    for pattern in EXECUTIVE_ALLOWED_PATTERNS:
        if pattern.search(normalized_title):
            return RoleFilterResult(
                passed=True,
            )

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
        if marker_matches(normalized_title, normalized_marker):
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
        if marker_matches(normalized_title, normalized_marker):
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
