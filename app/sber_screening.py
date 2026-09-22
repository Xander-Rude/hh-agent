from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.application_assets import get_resume_file_path
from app.db import Application, Evaluation, SessionLocal, Vacancy
from app.evaluator import CONFIRMED_COMPETENCY_GUARDS
from app.llm import LLMProvider


@dataclass
class ScreeningContext:
    application_id: int
    vacancy_title: str
    company: str
    vacancy_description: str
    selected_resume_title: str
    evaluation_strengths: list[str]
    verified_facts: list[str]
    resume_text: str = ""


@dataclass
class ScreeningSuggestion:
    answer: str
    confidence: str
    reason: str


def mask_secret(value: str | None, visible: int = 4) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    if len(raw) <= visible * 2:
        return "*" * len(raw)
    return raw[:visible] + "…" + raw[-visible:]


def _json_list(value: str | None) -> list[str]:
    try:
        data = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item).strip()]


def _canonical_verified_facts() -> list[str]:
    facts = ["13+ лет опыта в IT."]
    for guard in CONFIRMED_COMPETENCY_GUARDS:
        value = str(guard.get("strength_ru") or "").strip()
        if value and value not in facts:
            facts.append(value)
    facts.extend(
        [
            "Есть опыт управления кросс-функциональными IT-командами.",
            "Есть опыт полного цикла delivery: от требований и планирования до production и эксплуатации.",
            "Собственный AI-agent проект автоматизирует поиск, оценку и обработку вакансий: https://rudenko.one/hh-agent.html",
        ]
    )
    return facts


def _extract_resume_text(resume_key: str | None, resume_title: str | None) -> str:
    path = get_resume_file_path(resume_key, resume_title)
    if path is None or not path.exists():
        return ""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""

    try:
        reader = PdfReader(str(path))
        pages = []
        for page in reader.pages:
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(text)
        return "\n".join(pages)[:40000]
    except Exception as exc:
        print(
            "[SBER SCREENING] resume PDF extraction failed: "
            f"{type(exc).__name__}",
            flush=True,
        )
        return ""


def load_context(application_id: int) -> ScreeningContext:
    with SessionLocal() as session:
        application = session.get(Application, application_id)
        if application is None:
            raise ValueError(f"Application #{application_id} не найден.")

        vacancy = session.get(Vacancy, application.vacancy_id)
        if vacancy is None:
            raise ValueError("Vacancy для application не найдена.")

        evaluation = session.scalars(
            select(Evaluation)
            .where(Evaluation.vacancy_id == vacancy.id)
            .order_by(Evaluation.id.desc())
        ).first()

        strengths = _json_list(evaluation.strengths if evaluation else None)
        resume_title = (
            application.selected_resume_title
            or (evaluation.selected_resume_title if evaluation else None)
            or application.selected_resume_key
            or "не указано"
        )

        verified = _canonical_verified_facts()
        for item in strengths:
            if item not in verified:
                verified.append(item)

        resume_text = _extract_resume_text(
            application.selected_resume_key,
            resume_title,
        )

        return ScreeningContext(
            application_id=application.id,
            vacancy_title=vacancy.title,
            company=vacancy.company or "",
            vacancy_description=vacancy.description or "",
            selected_resume_title=resume_title,
            evaluation_strengths=strengths,
            verified_facts=verified,
            resume_text=resume_text,
        )


def _history_text(history: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in history[-10:]:
        question = str(item.get("question") or "").strip()
        if question:
            parts.append("Вопрос: " + question)

        if str(item.get("status") or "") != "sent":
            continue

        payload = str(item.get("approved_payload") or "").strip()
        if payload.startswith("text:"):
            answer = payload[5:].strip()
            if answer:
                parts.append("Ответ: " + answer)
        elif payload.startswith("button:"):
            try:
                index = int(payload.split(":", 1)[1])
                options = json.loads(item.get("options_json") or "[]")
                if isinstance(options, list) and 0 <= index < len(options):
                    parts.append("Выбран вариант: " + str(options[index]))
            except (ValueError, json.JSONDecodeError):
                pass
    return "\n".join(parts) or "Истории пока нет."


def build_prompt(
    context: ScreeningContext,
    question: str,
    history: list[dict[str, Any]],
) -> str:
    facts = "\n".join(f"- {item}" for item in context.verified_facts)
    strengths = "\n".join(f"- {item}" for item in context.evaluation_strengths) or "- нет дополнительных"
    return f"""
Ты помогаешь кандидату Александру Руденко пройти первичный скрининг ГигаРекрутера Сбера.

КРИТИЧЕСКИЕ ПРАВИЛА
- Отвечай от первого лица, естественно и коротко, как кандидат в Telegram.
- Используй ТОЛЬКО факты из блока ПОДТВЕРЖДЕННЫЕ ФАКТЫ и контекста конкретного отклика.
- Не придумывай работодателей, сроки, технологии, должности, цифры, достижения или личные обстоятельства.
- Если данных недостаточно для честного ответа, mode должен быть needs_user, answer оставь пустым и кратко объясни, что надо уточнить.
- Не называй автоматический скрининг собеседованием с человеком.
- Не упоминай, что ответ подготовлен LLM или агентом.
- Не пиши длинное сопроводительное письмо. Ответ должен соответствовать конкретному вопросу.

КОНКРЕТНЫЙ ОТКЛИК
Application ID: {context.application_id}
Компания: {context.company}
Вакансия: {context.vacancy_title}
Выбранное резюме: {context.selected_resume_title}

ПОДТВЕРЖДЕННЫЕ ФАКТЫ
{facts}

СИЛЬНЫЕ СТОРОНЫ, УЖЕ ПОДТВЕРЖДЕННЫЕ ПРИ ОЦЕНКЕ ВАКАНСИИ
{strengths}

ТЕКСТ ВЫБРАННОГО РЕЗЮМЕ
{context.resume_text[:32000] or "Текст PDF недоступен, используй только подтвержденные факты выше."}

ОПИСАНИЕ ВАКАНСИИ
{context.vacancy_description[:18000]}

ИСТОРИЯ ЭТОГО СКРИНИНГА
{_history_text(history)}

НОВЫЙ ВОПРОС ГИГАРЕКРУТЕРА
{question}

Верни JSON:
{{
  "mode": "answer" или "needs_user",
  "answer": "готовый ответ либо пустая строка",
  "confidence": "high", "medium" или "needs_user",
  "reason": "кратко, на каких подтвержденных фактах основан ответ или чего не хватает"
}}
""".strip()


def _extract_content(response: Any) -> str:
    message = getattr(response, "message", None)
    content = getattr(message, "content", None)
    if content is not None:
        return str(content)
    if isinstance(response, dict):
        return str((response.get("message") or {}).get("content") or "")
    return str(response)


def generate_suggestion(
    *,
    application_id: int,
    question: str,
    history: list[dict[str, Any]],
    llm: LLMProvider | None = None,
) -> ScreeningSuggestion:
    context = load_context(application_id)
    provider = llm or LLMProvider()
    schema = {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["answer", "needs_user"]},
            "answer": {"type": "string"},
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "needs_user"],
            },
            "reason": {"type": "string"},
        },
        "required": ["mode", "answer", "confidence", "reason"],
    }
    response = provider.chat(
        messages=[{"role": "user", "content": build_prompt(context, question, history)}],
        format_schema=schema,
    )
    raw = _extract_content(response).strip()
    data = json.loads(raw)

    mode = str(data.get("mode") or "needs_user")
    answer = str(data.get("answer") or "").strip()
    confidence = str(data.get("confidence") or "needs_user")
    reason = str(data.get("reason") or "").strip()

    if mode != "answer" or not answer:
        return ScreeningSuggestion(
            answer="",
            confidence="needs_user",
            reason=reason or "Недостаточно подтвержденных данных для автоматического ответа.",
        )

    return ScreeningSuggestion(
        answer=answer,
        confidence=confidence if confidence in {"high", "medium"} else "medium",
        reason=reason,
    )
