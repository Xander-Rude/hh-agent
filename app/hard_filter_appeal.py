from __future__ import annotations

import json
import os
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.llm import LLMProvider


MAX_APPEAL_ATTEMPTS = 2
DEFAULT_APPEAL_CONFIDENCE = 0.75


class HardFilterAppealDecision(BaseModel):
    verdict: Literal[
        "confirm_reject",
        "override_reject",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


def appeal_confidence_threshold() -> float:
    raw = os.getenv(
        "HARD_FILTER_APPEAL_CONFIDENCE",
        str(DEFAULT_APPEAL_CONFIDENCE),
    )
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_APPEAL_CONFIDENCE
    return max(0.0, min(1.0, value))


def should_override_hard_reject(
    decision: HardFilterAppealDecision,
    threshold: float | None = None,
) -> bool:
    minimum = (
        appeal_confidence_threshold()
        if threshold is None
        else max(0.0, min(1.0, float(threshold)))
    )
    return (
        decision.verdict == "override_reject"
        and decision.confidence >= minimum
    )


def _extract_response_text(response: Any) -> str:
    if hasattr(response, "message"):
        message = response.message
        if hasattr(message, "content"):
            return message.content or ""

    if isinstance(response, dict):
        message = response.get("message", {})
        if isinstance(message, dict):
            return message.get("content", "") or ""

    raise RuntimeError(
        "Не удалось извлечь текст LLM-апелляции из ответа Ollama."
    )


def _extract_json(text: str) -> dict:
    text = (text or "").strip()

    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"```$",
            "",
            text,
        ).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(
            "LLM-апелляция не вернула JSON."
        )

    return json.loads(
        text[start : end + 1]
    )


class HardFilterAppealReviewer:
    def __init__(
        self,
        llm: LLMProvider | None = None,
    ) -> None:
        self.llm = (
            llm
            if llm is not None
            else LLMProvider()
        )

    def review(
        self,
        *,
        resume: str,
        vacancy: str,
        preferences: dict | None,
        hard_filter_reason: str,
        hard_filter_code: str | None = None,
    ) -> HardFilterAppealDecision:
        preferences = (
            preferences
            if isinstance(preferences, dict)
            else {}
        )

        prompt = f"""
Ты выполняешь апелляцию решения hard-filter по вакансии.

Hard-filter уже решил отклонить вакансию. Твоя единственная задача —
определить, не является ли это false negative.

Вердикты:
- confirm_reject: hard-filter прав; вакансия действительно не относится
  к целевому карьерному профилю кандидата или причина фильтра фактически верна.
- override_reject: hard-filter сработал слишком грубо; есть разумная и
  подтверждаемая вероятность, что вакансия соответствует целевому профилю
  кандидата и должна пройти в полноценный scoring.

Правила:
- Оценивай смысл роли и реальные обязанности, а не только title.
- Учитывай русские и английские названия, нестандартные названия должностей,
  C-level/Director/Head/руководителей департаментов и направлений.
- Не придумывай кандидату опыт, которого нет в резюме.
- Не отменяй reject только потому, что отдельные слова выглядят знакомо.
- Если название формально нетипичное, но обязанности явно совпадают с
  IT management / PMO / Program / Delivery / Technology leadership профилем,
  это сильный аргумент за override_reject.
- Если причина hard-filter действительно описывает основную суть вакансии,
  подтверждай reject.
- confidence — уверенность именно в выбранном вердикте от 0.0 до 1.0.
- reason — короткое объяснение на русском, 1-3 предложения.

ПРИЧИНА HARD-FILTER
code: {hard_filter_code or "unknown"}
reason: {hard_filter_reason}

РЕЗЮМЕ КАНДИДАТА
{resume[:24000]}

ПРЕДПОЧТЕНИЯ КАНДИДАТА
{json.dumps(preferences, ensure_ascii=False, indent=2)[:8000]}

ВАКАНСИЯ
{vacancy[:16000]}

Верни результат строго по схеме HardFilterAppealDecision.
""".strip()

        schema = HardFilterAppealDecision.model_json_schema()
        last_error: Exception | None = None

        for attempt in range(1, MAX_APPEAL_ATTEMPTS + 1):
            try:
                response = self.llm.chat(
                    messages=[
                        {
                            "role": "user",
                            "content": prompt,
                        }
                    ],
                    format_schema=schema,
                )
                raw = _extract_response_text(response).strip()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = _extract_json(raw)

                return HardFilterAppealDecision.model_validate(data)

            except Exception as exc:
                last_error = exc
                print(
                    "[HARD FILTER APPEAL] "
                    f"attempt {attempt}/{MAX_APPEAL_ATTEMPTS} failed: {exc}"
                )

        raise RuntimeError(
            "HardFilterAppealReviewer не получил корректный structured "
            f"response после {MAX_APPEAL_ATTEMPTS} попыток."
        ) from last_error
