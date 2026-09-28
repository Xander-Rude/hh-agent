from __future__ import annotations

import difflib
import json
import os
import re
from typing import Any

from app.llm import LLMProvider


COVER_WRITER_VERSION = "human-cover-v3"

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
    "имею опыт",
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


def _similarity_body(text: str | None) -> str:
    body = _strip_signature(text or "")
    body = re.sub(
        r"^\s*(?:Здравствуйте|Hello)\s*[,.:;!?-]*\s*",
        "",
        body,
        flags=re.IGNORECASE,
    )
    return _normalize(body)


def _similarity_ratio(left: str | None, right: str | None) -> float:
    first = _similarity_body(left)
    second = _similarity_body(right)
    if not first or not second:
        return 0.0
    return difflib.SequenceMatcher(None, first, second).ratio()


def _style_variant(*values: object, count: int) -> int:
    if count <= 1:
        return 0
    seed = "|".join(_normalize(str(value or "")) for value in values)
    return sum(ord(char) for char in seed) % count


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
        "что помогает",
        "что помогло",
        "что позволяет",
        "помог",
        "позвол",
        "так что",
        "так, что",
        "в результате чего",
        "which enabled",
        "which allowed",
        "which helps",
        "this helps",
        "this allows",
        "helps me",
        "allows me",
        "enables me",
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
    recent_letters: list[str] | None = None,
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
    style_rules = (
        "Начни с конкретной рабочей задачи вакансии, затем докажи совпадение фактами.",
        "Начни сразу с самого сильного факта кандидата, а затем свяжи его с сутью роли без причинных выдумок.",
        "Начни с технического или доменного контекста роли, затем коротко покажи свой практический опыт.",
        "Сделай первое предложение очень коротким и предметным. Не используй конструкции «мне близка» и «мне нравится формат».",
        "Сначала обозначь тип ответственности в этой роли, затем дай два разных доказательства из опыта.",
        "Построй письмо как компактную заметку: конкретный hook, один сильный кейс, второй факт и короткое завершение.",
    )
    style_rule = style_rules[
        _style_variant(
            vacancy_title,
            vacancy_company,
            vacancy_description[:1000],
            count=len(style_rules),
        )
    ]
    recent = [
        re.sub(r"\s+", " ", str(item or "")).strip()
        for item in (recent_letters or [])
        if str(item or "").strip()
    ][:5]
    recent_block = "\n".join(
        f"R{index}: {item[:700]}"
        for index, item in enumerate(recent, start=1)
    ) or "Нет."

    return f"""
Ты пишешь короткое сопроводительное письмо от первого лица за Александра Руденко.

Цель: письмо должно звучать как живое человеческое сообщение сильного IT Project/Delivery Manager,
а не как шаблон, ATS-текст или набор ключевых слов.

{language_rule}
{channel_rule}

СТИЛЬ ЭТОГО ПИСЬМА
{style_rule}
Не копируй синтаксис, первую фразу, порядок аргументов и концовку из RECENT LETTERS.
Если факт тот же, переформулируй его естественно и поставь в другую структуру предложения.

КЛЮЧЕВОЙ ПРИНЦИП
Сначала пойми, что в самой работе реально важно и интересно. Одной естественной фразой покажи,
что автор увидел суть задачи. Затем используй 2-3 наиболее сильных подтверждённых факта кандидата.
Сначала выбирай самые специфичные совпадения именно с этой ролью: редкий домен, hardware/software,
подрядчики и закупки, портфель параллельных проектов, архитектурный/инфраструктурный контекст,
AI/data или другой явно выраженный scope. Общие PM-факты, универсальный delivery и привычная
метрика Time-to-Market не должны вытеснять более прямое совпадение.
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
- Метрику используй только если она входит в два самых сильных доказательства для этой вакансии.
  Не добавляй Time-to-Market просто потому, что он есть среди разрешённых фактов.
- Пиши спокойно, уверенно и по-человечески. Не используй дежурный старт вроде
  "Мне близка задача", "Мне нравится формат", "Меня привлекает" или "Для меня здесь важно",
  если такую же конструкцию можно заменить конкретным содержанием вакансии.
- Последнее предложение должно быть коротким человеческим завершением без нового факта о кандидате:
  например, что именно хотелось бы предметно обсудить. Не используй одну и ту же дежурную формулу.
- Не используй длинное тире "—" или "–".
- 4-6 коротких предложений после приветствия. Без markdown и списков.
- Не добавляй подпись и имя: Python добавит их сам.
- Верни JSON по схеме: letter_body и used_fact_ids.
- used_fact_ids должны содержать только реально использованные F1..Fn.

ALLOWED CANDIDATE FACTS
{facts}

RECENT LETTERS
Это недавние письма этого же кандидата. Их факты могут повторяться, формулировки и структура - нет:
{recent_block}

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
    if "—" in candidate or "–" in candidate:
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


def validate_human_cover_letter(
    text: str | None,
    *,
    vacancy_title: str,
    vacancy_company: str | None,
    allowed_facts: list[str],
) -> str | None:
    """Validate a persisted human-writer letter without calling the LLM.

    Pending Telegram cards store the exact text shown to the user. Approval
    must preserve that text when it is still safe against the same grounded
    fact bank, so the reviewed preview cannot silently change at send time.
    """
    candidate = (text or "").strip()
    facts = _dedupe_facts(allowed_facts)[:12]
    if not candidate or not facts:
        return None

    language = _detect_language(
        " ".join([vacancy_title or "", candidate])
    )
    required_urls = _urls(" ".join(facts))
    if required_urls and not required_urls.issubset(_urls(candidate)):
        return None

    return _validate_body(
        candidate,
        language=language,
        vacancy_title=vacancy_title,
        vacancy_company=vacancy_company,
        selected_facts=facts,
    )


def write_human_cover_letter(
    *,
    account_key: str,
    vacancy_title: str,
    vacancy_company: str | None,
    vacancy_description: str,
    safe_draft: str,
    allowed_facts: list[str],
    recent_letters: list[str] | None = None,
    llm: LLMProvider | None = None,
) -> str:
    fallback = (safe_draft or "").strip()
    facts = _dedupe_facts(allowed_facts)[:12]
    recent = [
        str(item or "").strip()
        for item in (recent_letters or [])
        if str(item or "").strip()
    ][:5]
    max_recent_similarity = float(
        os.getenv("HH_COVER_MAX_RECENT_SIMILARITY", "0.82")
    )

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
        recent_letters=recent,
    )

    for attempt in range(2):
        attempt_prompt = prompt
        if attempt:
            attempt_prompt += """

SAFETY REWRITE
Предыдущий вариант не прошёл автоматическую проверку. Перепиши письмо с нуля.
Не добавляй причинно-следственных связей между отдельными фактами, не усиливай
результаты словами вроде "значительно" или "большой опыт", не повторяй company/title.
Не используй "помогает", "позволяет", "благодаря", если такая причинная связь не дана
прямо внутри одного ALLOWED CANDIDATE FACT. Сначала выбери самые специфичные совпадения
с вакансией; универсальный Time-to-Market не добавляй по привычке.
Используй только 2-3 ALLOWED CANDIDATE FACTS, один естественный hook по сути работы и
короткое человеческое завершение без нового факта. Если проблема была в сходстве с RECENT LETTERS,
обязательно поменяй не только слова, но и порядок предложений, тип первой фразы и концовку.
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
            reject_reason = "validation"
            if result and recent:
                highest_similarity = max(
                    _similarity_ratio(result, previous)
                    for previous in recent
                )
                if highest_similarity > max_recent_similarity:
                    reject_reason = (
                        f"similarity={highest_similarity:.3f}"
                    )
                    result = None
            if result:
                return result

            print(
                f"[COVER_WRITER] {COVER_WRITER_VERSION} rejected model output "
                f"attempt={attempt + 1} reason={reject_reason}"
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
