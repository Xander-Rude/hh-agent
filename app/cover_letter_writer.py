from __future__ import annotations

import json
import os
import re
from typing import Any

from app.llm import LLMProvider


COVER_WRITER_VERSION = "human-cover-v1"

_BANNED_PHRASES_RU = (
    "релевантный опыт",
    "такого типа it-проект",
    "с этим контуром",
    "по описанию задач",
    "для этой позиции наиболее релевант",
    "мой основной профиль",
    "буду рад рассказать подробнее о похожих проектах",
    "буду рад обсудить задачи подробнее",
    "если мой опыт подходит",
    "большой опыт",
)

_BANNED_PHRASES_EN = (
    "relevant experience for this type",
    "my core profile",
    "the responsibilities are close to",
    "a lot of the responsibilities are familiar",
    "my background is close to",
    "happy to share more detail on similar projects",
    "happy to discuss the role",
)

_THIRD_PERSON_PATTERNS = (
    r"\bкандидат\w*\b",
    r"\bего опыт\b",
    r"\bего навы",
    r"\bон имеет\b",
    r"\bон умеет\b",
    r"\bthe candidate\b",
    r"\bhis experience\b",
    r"\bhe has\b",
    r"\bhe can\b",
)

_WRITER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "letter_body": {"type": "string"},
        "used_fact_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["letter_body", "used_fact_ids"],
}


def _env_enabled() -> bool:
    return os.getenv(
        "HH_ENABLE_LLM_COVER_WRITER",
        "true",
    ).strip().lower() in {"1", "true", "yes", "on"}


def _response_text(response: Any) -> str:
    if hasattr(response, "message"):
        message = response.message
        if hasattr(message, "content"):
            return message.content or ""
    if isinstance(response, dict):
        message = response.get("message", {})
        if isinstance(message, dict):
            return message.get("content", "") or ""
    raise RuntimeError("Не удалось извлечь текст cover-letter writer.")


def _normalize(text: str | None) -> str:
    return re.sub(
        r"[^0-9a-zа-я]+",
        " ",
        (text or "").lower().replace("ё", "е"),
        flags=re.IGNORECASE,
    ).strip()


def _detect_language(text: str) -> str:
    cyr = len(re.findall(r"[А-Яа-яЁё]", text or ""))
    lat = len(re.findall(r"[A-Za-z]", text or ""))
    return "en" if lat > cyr else "ru"


def _strip_signature(text: str) -> str:
    result = (text or "").strip()
    result = re.sub(
        r"(?is)\n*\s*(?:с уважением\s*,?\s*)?александр\s+руденко\s*$",
        "",
        result,
    ).strip()
    result = re.sub(
        r"(?is)\n*\s*(?:best regards\s*,?\s*)?aleksandr\s+rudenko\s*$",
        "",
        result,
    ).strip()
    return result


def _dedupe_facts(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = re.sub(r"\s+", " ", str(item or "")).strip(" .;:-")
        key = _normalize(value)
        if not value or not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _numeric_tokens(text: str) -> set[str]:
    result: set[str] = set()
    for token in re.findall(r"(?<![\w])\d+(?:[.,]\d+)?\s*(?:\+|%)?", text or ""):
        result.add(
            token.replace(" ", "").replace(",", ".").strip()
        )
    return result


def _urls(text: str) -> set[str]:
    return {
        value.rstrip(").,;")
        for value in re.findall(r"https?://\S+", text or "")
    }


def _contains_banned_phrase(text: str, language: str) -> bool:
    normalized = (text or "").lower().replace("ё", "е")
    phrases = _BANNED_PHRASES_EN if language == "en" else _BANNED_PHRASES_RU
    return any(phrase in normalized for phrase in phrases)


def _contains_third_person(text: str) -> bool:
    return any(
        re.search(pattern, text or "", flags=re.IGNORECASE)
        for pattern in _THIRD_PERSON_PATTERNS
    )


def _contains_unsupported_causality(
    text: str,
    selected_facts: list[str],
) -> bool:
    markers = (
        "за счет",
        "за счёт",
        "благодаря",
        "что позволило",
        "что помогло",
        "так что",
        "так, что",
        "в результате чего",
        "which enabled",
        "which allowed",
        "thanks to",
    )
    body = (text or "").lower().replace("ё", "е")
    facts = " ".join(selected_facts).lower().replace("ё", "е")
    return any(marker in body and marker not in facts for marker in markers)


def _mentions_identity(
    text: str,
    *,
    vacancy_title: str,
    vacancy_company: str | None,
) -> bool:
    body = _normalize(text)
    company = _normalize(vacancy_company)
    title = _normalize(vacancy_title)
    if company and len(company) >= 4 and company in body:
        return True
    if title and len(title) >= 6 and title in body:
        return True
    return False


def _build_prompt(
    *,
    account_key: str,
    vacancy_title: str,
    vacancy_company: str | None,
    vacancy_description: str,
    safe_draft: str,
    allowed_facts: list[str],
    language: str,
) -> str:
    facts = "\n".join(
        f"F{index}: {fact}"
        for index, fact in enumerate(allowed_facts, start=1)
    )
    language_rule = (
        "Write the cover letter in English."
        if language == "en"
        else "Пиши сопроводительное на русском языке."
    )
    channel_rule = (
        "CLEAN: персонализация должна быть особенно точной: выбери самые сильные "
        "пересечения именно с этой вакансией."
        if account_key == "clean"
        else "OLD: письмо может быть чуть проще, но всё равно должно ощущаться "
        "написанным живым человеком именно после чтения этой вакансии."
    )

    return f"""
Ты пишешь короткое сопроводительное письмо от первого лица за Александра Руденко.

Цель: письмо должно звучать как живое человеческое сообщение сильного IT Project/Delivery Manager,
а не как шаблон, ATS-текст или набор ключевых слов.

{language_rule}
{channel_rule}

КЛЮЧЕВОЙ ПРИНЦИП
Сначала пойми, что в самой работе реально важно и интересно. Одной естественной фразой покажи,
что автор увидел суть задачи. Затем используй 2-3 наиболее сильных подтверждённых факта кандидата.
Тепло создавай через нормальную человеческую интонацию и интерес к содержанию работы, а не через
лесть компании или фальшивый энтузиазм.

ЖЁСТКИЕ ПРАВИЛА
- Любое утверждение о кандидате можно делать ТОЛЬКО из ALLOWED CANDIDATE FACTS ниже.
- Не придумывай опыт, технологии, достижения, масштабы, цифры, домены или обязанности.
- Не связывай два разрешённых факта новой причинно-следственной связью. Если в фактах отдельно есть
  результат и отдельно есть практика/навык, нельзя писать, что результат получен "за счёт", "благодаря"
  или "что позволило" этой практике, если такая связь явно не дана в одном факте.
- Если среди ALLOWED CANDIDATE FACTS есть URL собственного AI-agent, обязательно используй этот факт
  и сохрани URL дословно: для AI-вакансии ссылка должна остаться в письме.
- Не переноси требования вакансии в опыт кандидата.
- Не повторяй название компании и название позиции: это выглядит искусственно.
- Не пиши "релевантный опыт", "с этим контуром", "по описанию задач",
  "для этой позиции наиболее релевантны", "мой основной профиль" и похожие канцелярские конструкции.
- Не начинай с "Меня заинтересовала ваша вакансия" и не пиши "давно слежу за компанией",
  "мечтал работать", "вдохновляет миссия", если такого факта нет.
- Не перечисляй навыки через двоеточие как резюме.
- Не пытайся закрыть все требования. Лучше 2 сильных совпадения, чем 7 слабых.
- Масштабный факт (30+ проектов, крупный бюджет, большие команды и т.п.) используй максимум один
  и только если он прямо помогает именно этой вакансии.
- Пиши спокойно, уверенно и по-человечески. Допустима фраза вроде
  "Мне здесь особенно близка задача..." или "Мне нравится формат, где...",
  если она опирается на реальную задачу вакансии.
- 4-6 коротких предложений после приветствия. Без markdown и списков.
- Не добавляй подпись и имя: Python добавит их сам.
- Верни JSON по схеме: letter_body и used_fact_ids.
- used_fact_ids должны содержать только реально использованные F1..Fn.

ALLOWED CANDIDATE FACTS
{facts}

SAFE DRAFT
Это только безопасный fallback по фактам. НЕ копируй его канцелярский стиль:
{safe_draft[:5000]}

VACANCY CONTEXT
Title: {vacancy_title}
Company: {vacancy_company or ""}
Description:
{(vacancy_description or "")[:16000]}
""".strip()


def _validate_body(
    body: str,
    *,
    language: str,
    vacancy_title: str,
    vacancy_company: str | None,
    selected_facts: list[str],
) -> str | None:
    candidate = _strip_signature(body)
    if not candidate:
        return None

    if _contains_banned_phrase(candidate, language):
        return None
    if _contains_third_person(candidate):
        return None
    if _contains_unsupported_causality(candidate, selected_facts):
        return None
    if _mentions_identity(
        candidate,
        vacancy_title=vacancy_title,
        vacancy_company=vacancy_company,
    ):
        return None

    if len(candidate) < 140 or len(candidate) > 1200:
        return None

    allowed_numbers = _numeric_tokens(" ".join(selected_facts))
    if not _numeric_tokens(candidate).issubset(allowed_numbers):
        return None

    allowed_urls = _urls(" ".join(selected_facts))
    if not _urls(candidate).issubset(allowed_urls):
        return None

    if language == "ru":
        candidate = re.sub(
            r"^\s*Здравствуйте\s*[,.:;!?-]*\s*",
            "",
            candidate,
            flags=re.IGNORECASE,
        ).strip()
        if not candidate:
            return None
        return "Здравствуйте!\n\n" + candidate + "\n\nАлександр Руденко"

    candidate = re.sub(
        r"^\s*Hello\s*[,.:;!?-]*\s*",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).strip()
    if not candidate:
        return None
    return "Hello!\n\n" + candidate + "\n\nAleksandr Rudenko"


def write_human_cover_letter(
    *,
    account_key: str,
    vacancy_title: str,
    vacancy_company: str | None,
    vacancy_description: str,
    safe_draft: str,
    allowed_facts: list[str],
    llm: LLMProvider | None = None,
) -> str:
    fallback = (safe_draft or "").strip()
    facts = _dedupe_facts(allowed_facts)[:12]

    if not fallback or not facts or not _env_enabled():
        return fallback

    # Normal unit/integration tests must stay offline. Dedicated writer tests
    # pass a fake llm explicitly and still exercise the full path.
    if llm is None and os.getenv("PYTEST_CURRENT_TEST"):
        return fallback

    provider = llm or LLMProvider()
    language = _detect_language(
        " ".join([vacancy_title or "", vacancy_description or "", fallback])
    )
    prompt = _build_prompt(
        account_key=account_key,
        vacancy_title=vacancy_title,
        vacancy_company=vacancy_company,
        vacancy_description=vacancy_description,
        safe_draft=fallback,
        allowed_facts=facts,
        language=language,
    )

    for attempt in range(2):
        attempt_prompt = prompt
        if attempt:
            attempt_prompt += """

SAFETY REWRITE
Предыдущий вариант не прошёл автоматическую проверку. Перепиши письмо с нуля.
Не добавляй причинно-следственных связей между отдельными фактами, не усиливай
результаты словами вроде "значительно" или "большой опыт", не повторяй company/title.
Используй только 2-3 ALLOWED CANDIDATE FACTS и один естественный hook по сути работы.
"""
        try:
            response = provider.chat(
                messages=[{"role": "user", "content": attempt_prompt}],
                format_schema=_WRITER_SCHEMA,
            )
            raw = _response_text(response).strip()
            payload = json.loads(raw)

            body = str(payload.get("letter_body") or "").strip()
            ids = payload.get("used_fact_ids") or []
            if not isinstance(ids, list):
                continue

            selected: list[str] = []
            seen_ids: set[str] = set()
            invalid_id = False
            for fact_id in ids:
                token = str(fact_id or "").strip().upper()
                match = re.fullmatch(r"F(\d+)", token)
                if not match or token in seen_ids:
                    continue
                index = int(match.group(1)) - 1
                if index < 0 or index >= len(facts):
                    invalid_id = True
                    break
                seen_ids.add(token)
                selected.append(facts[index])

            if invalid_id or not selected:
                continue

            required_urls = _urls(" ".join(facts))
            if required_urls and not required_urls.issubset(_urls(body)):
                result = None
            else:
                result = _validate_body(
                    body,
                    language=language,
                    vacancy_title=vacancy_title,
                    vacancy_company=vacancy_company,
                    selected_facts=selected,
                )
            if result:
                return result

            print(
                f"[COVER_WRITER] {COVER_WRITER_VERSION} rejected model output "
                f"attempt={attempt + 1}"
            )
        except Exception as exc:
            print(
                f"[COVER_WRITER] {COVER_WRITER_VERSION} attempt={attempt + 1} "
                f"failed: {type(exc).__name__}: {exc}"
            )

    print(
        f"[COVER_WRITER] {COVER_WRITER_VERSION} using deterministic fallback"
    )
    return fallback
