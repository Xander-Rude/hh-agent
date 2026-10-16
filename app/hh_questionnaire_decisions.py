"""Isolated decision policy for HH employer questionnaires only.

This module does not change LLMProvider defaults or any scoring/cover-letter prompt.
All proposed choices must still be approved by the account owner.
"""
from __future__ import annotations

import json
import os
import re

from dotenv import load_dotenv


_SITUATION_ACTION = re.compile(
    r"что (?:вы )?(?:сделаете|предложите|предпримете|измените|выберете)|"
    r"как (?:вы )?(?:поступите|проверите|решите|определите|приоритизи(?:руете|ровать))|"
    r"каким будет (?:ваш|первый) шаг|"
    r"с чего начн[её]те|"
    r"что делать (?:в|если)|"
    r"как бы вы (?:действовали|поступили|решили)",
    re.I,
)
_SITUATION_CONTEXT = re.compile(
    r"если|представьте|ситуаци|кейс|после запуска|менеджер|"
    r"заказчик|клиент|команд[аы]|руководитель|бизнес|процесс|"
    r"интеграц|внедрен|crm|erp|ошибк|жалоб|потребовал|просит|перенос",
    re.I,
)
_PERSONAL_CHOICE = re.compile(
    r"готов(?:ы|а|\(а\))? ли|согласн|подходит ли вам|"
    r"офис|гибрид|удал[её]н|релокац|переезд|"
    r"гражданств|виз[аы]|разрешение на работу|"
    r"официальн(?:ое|ый) трудоустройств|"
    r"ваш(?:и|его)? опыт|ваше образование|"
    r"работали ли вы|есть ли у вас|умеете ли вы|"
    r"зарплат|оклад|доход|ожидан|"
    r"телеграм|telegram|t\.me|контакт",
    re.I,
)
_TELEGRAM_CONTACT = re.compile(r"телеграм|telegram|t\.me|tg для (?:связи|контакта)", re.I)


def is_telegram_question(question: str) -> bool:
    return bool(_TELEGRAM_CONTACT.search(question or ""))


def telegram_contact_answer(question: str) -> str | None:
    """None = not a contact question; empty = private contact not configured."""
    if not is_telegram_question(question):
        return None
    load_dotenv(override=False)
    value = os.getenv("HH_APPLICANT_TELEGRAM", "").strip()
    match = re.fullmatch(
        r"(?:https?://t\.me/|@)?([A-Za-z0-9_]{5,32})/?", value, re.I
    )
    return "@" + match.group(1) if match else ""


def is_situational(question: str) -> bool:
    text = question or ""
    if _PERSONAL_CHOICE.search(text):
        return False
    return bool(_SITUATION_ACTION.search(text) and _SITUATION_CONTEXT.search(text))


def requires_personal_confirmation(question: str) -> bool:
    return bool(_PERSONAL_CHOICE.search(question or ""))


def missing_answer_label(question: dict) -> str:
    text = question.get("question") or ""
    if is_telegram_question(text):
        return "⚠️ Не указан Telegram для обратной связи."
    if requires_personal_confirmation(text):
        return "⚠️ Нужно твоё подтверждение личного опыта или условий."
    if question.get("kind") == "choice" and is_situational(text):
        return "⚠️ ИИ не выбрал вариант — нужна ручная проверка."
    return "⚠️ Недостаточно надёжных данных для ответа — требуется уточнение."


SENIOR_PM_DECISION_POLICY = """Ты опытный руководитель IT-проектов и программ (Senior PM/Program Manager),
а не исполнитель, который сразу берёт предложенное решение в разработку.
В кейсах: выясняй реальную бизнес-проблему и root cause, измеряй потери и эффект,
учитывай ROI/TCO, стоимость изменений и поддержки, сроки, приоритеты, риски,
зависимости, безопасность, заинтересованные стороны и критерии успеха.
Сопоставляй альтернативы: процесс/обучение, готовая настройка, интеграция,
RPA и ИИ-агенты, включая работу через UI старой системы, когда это уместно.
Не предлагай AI как самоцель: оцени точность, надёжность, права доступа,
требования к данным, контроль человеком и эксплуатационную стоимость.
Выбирай стратегию с наибольшей бизнес-ценностью, а не самую технологичную.
Не придумывай личные достижения и опыт кандидата; речь о гипотетическом кейсе.
Отвечай конкретно, без канцелярита и рассуждений уровня junior."""


def choose_situational_option(question: dict, provider) -> tuple[str, str]:
    """Return exact existing HH option text, plus a short decision rationale.

    The model is allowed to recommend, not invent an option or submit it.
    """
    if question.get("kind") != "choice" or not is_situational(question.get("question", "")):
        return "", ""
    options = question.get("options") or []
    if not 2 <= len(options) <= 8 or not all(o.get("text") for o in options):
        return "", ""
    numbered = "\n".join(f"{i}. {o['text']}" for i, o in enumerate(options, 1))
    prompt = (
        SENIOR_PM_DECISION_POLICY
        + "\n\nСитуационный вопрос работодателя: "
        + question["question"]
        + "\nВарианты (порядок фиксирован):\n"
        + numbered
        + "\nВыбери ОДИН лучший из предложенных вариантов. "
          "Если самый сильный подход шире вариантов (например, AI/RPA), "
          "выбери ближайший вариант, который позволяет сначала проверить "
          "ценность и альтернативы. НЕ выдумывай пятый вариант. "
          "Если вопрос фактически про личные условия кандидата, не угадывай: mode=needs_user. "
          "Коротко объясни управленческую логику без биографических утверждений. "
          "Верни только JSON: "
          '{"mode":"select" или "needs_user","option_index":1,"reason":"почему этот вариант"}'
    )
    schema = {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["select", "needs_user"]},
            "option_index": {"type": "integer", "minimum": 0, "maximum": len(options)},
            "reason": {"type": "string"},
        },
        "required": ["mode", "option_index", "reason"],
    }
    response = provider.chat(messages=[{"role": "user", "content": prompt}], format_schema=schema)
    raw = getattr(getattr(response, "message", None), "content", None)
    if raw is None and isinstance(response, dict):
        raw = (response.get("message") or {}).get("content")
    result = json.loads(str(raw or "{}"))
    index = result.get("option_index")
    if result.get("mode") != "select" or type(index) is not int or not 1 <= index <= len(options):
        return "", ""
    reason = re.sub(r"\s+", " ", str(result.get("reason") or "")).strip()
    # Explanation is informational only; it never enters the HH answer payload.
    if len(reason) > 220:
        reason = reason[:217].rstrip() + "..."
    return options[index - 1]["text"], reason


def situation_text_prompt(question: str) -> str:
    return (
        SENIOR_PM_DECISION_POLICY
        + "\n\nДай практический ответ от первого лица на ситуационный вопрос HH "
          "в 2–3 коротких предложениях. Объясни первый шаг, сравнение реальных "
          "альтернатив (включая AI/RPA, если применимо) и критерий результата. "
          "Не приписывай кандидату неподтверждённый прошлый опыт. "
          "Не добавляй выдуманных числовых результатов. "
        + "\nВОПРОС: " + question
        + '\nВерни JSON: {"mode":"answer" или "needs_user","answer":"готовый ответ"}'
    )
