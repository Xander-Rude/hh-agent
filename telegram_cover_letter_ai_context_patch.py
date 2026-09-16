from __future__ import annotations

import json

from app.evaluator import (
    AI_PROJECT_URL,
    MAX_LLM_ATTEMPTS,
    _detect_language,
    _extract_response_text,
    _normalize_cover_letter,
    _regenerate_ai_relevant_cover_letter,
    _strip_existing_signature,
)
from app.llm import LLMProvider


AI_RELEVANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "ai_relevant": {
            "type": "boolean",
        }
    },
    "required": ["ai_relevant"],
    "additionalProperties": False,
}


def _is_ai_relevant(llm: LLMProvider, vacancy_text: str) -> bool:
    prompt = f"""
Определи, является ли вакансия содержательно AI-релевантной.

Верни ai_relevant=true ТОЛЬКО если AI/ИИ, ML, LLM, GenAI, AI agents,
RAG, внедрение AI или AI-продукты являются существенной частью задач,
обязательных/значимых требований, продукта или направления вакансии.
Оценивай смысл вакансии, а не наличие отдельного слова или аббревиатуры.
Если AI упомянут вскользь, например в общем описании компании,
в списке технологий вокруг другой роли или как необязательный тренд,
верни false.

ВАКАНСИЯ
{vacancy_text[:20000]}

Верни только JSON по заданной схеме.
""".strip()

    last_error: Exception | None = None
    for attempt in range(1, MAX_LLM_ATTEMPTS + 1):
        try:
            response = llm.chat(
                messages=[{"role": "user", "content": prompt}],
                format_schema=AI_RELEVANCE_SCHEMA,
            )
            raw = _extract_response_text(response).strip()
            payload = json.loads(raw)
            value = payload.get("ai_relevant")
            if isinstance(value, bool):
                return value
            raise ValueError("ai_relevant is not boolean")
        except Exception as exc:
            last_error = exc
            print(
                f"[TELEGRAM COVER] AI relevance attempt "
                f"{attempt}/{MAX_LLM_ATTEMPTS} failed: {exc}",
                flush=True,
            )

    raise RuntimeError(
        "Не удалось определить AI-релевантность вакансии."
    ) from last_error


def _has_required_ai_project_context(text: str) -> bool:
    lower = (text or "").lower()
    return AI_PROJECT_URL in (text or "") and "github" not in lower


def _correct_ai_project_context(
    llm: LLMProvider,
    *,
    current_cover_letter: str,
    resume: str,
    vacancy_text: str,
    language: str,
) -> str:
    language_rule = (
        "Пиши письмо на русском языке."
        if language == "ru"
        else "Write the letter in English."
    )

    draft = _regenerate_ai_relevant_cover_letter(
        llm,
        current_cover_letter=current_cover_letter,
        resume=resume,
        vacancy=vacancy_text,
        language=language,
    )
    normalized = _normalize_cover_letter(draft, language)
    if normalized and _has_required_ai_project_context(normalized):
        return normalized

    last_error: Exception | None = None
    candidate = draft or current_cover_letter

    for attempt in range(1, MAX_LLM_ATTEMPTS + 1):
        prompt = f"""
Исправь сопроводительное письмо для AI-релевантной вакансии.

{language_rule}

Обязательные требования к результату:
- органично упомяни собственный AI-agent проект кандидата, который
  автоматизирует полный workflow работы с вакансиями;
- ОБЯЗАТЕЛЬНО включи в текст ровно эту ссылку обычным текстом:
  {AI_PROJECT_URL}
- НЕ упоминай GitHub, репозиторий, repo или ссылку на GitHub;
- остальные факты бери только из резюме и текущего письма;
- не придумывай технологии, результаты или функциональность проекта;
- сохрани письмо коротким, деловым и от первого лица;
- не добавляй подпись и имя кандидата, Python добавит подпись сам;
- верни только текст письма без markdown и комментариев.

ТЕКУЩЕЕ ПИСЬМО
{_strip_existing_signature(candidate)[:5000]}

РЕЗЮМЕ
{resume[:32000]}

ВАКАНСИЯ
{vacancy_text[:20000]}
""".strip()

        try:
            response = llm.chat(
                messages=[{"role": "user", "content": prompt}],
            )
            candidate = _extract_response_text(response).strip()
            normalized = _normalize_cover_letter(candidate, language)
            if normalized and _has_required_ai_project_context(normalized):
                return normalized
            last_error = RuntimeError(
                "AI cover letter still misses the project URL or mentions GitHub."
            )
        except Exception as exc:
            last_error = exc

        print(
            f"[TELEGRAM COVER] AI project correction attempt "
            f"{attempt}/{MAX_LLM_ATTEMPTS} failed: {last_error}",
            flush=True,
        )

    raise RuntimeError(
        "Не удалось подготовить AI-сопроводительное с корректной ссылкой на проект."
    ) from last_error


def install(cover_module) -> None:
    """Apply the same semantic AI-project policy to Telegram URL requests."""
    if getattr(cover_module, "_ai_context_patch_installed", False):
        return

    def create_cover_letter_for_url(raw_url: str):
        url = cover_module.canonicalize_url(raw_url)
        cached = cover_module._find_cached_snapshot(url)

        snapshot = cached if cached is not None else cover_module._fetch_snapshot(url)
        used_cached = bool(cached is not None and cached.cached_cover_letter)

        if used_cached:
            base_cover_letter = cached.cached_cover_letter
        else:
            base_cover_letter = cover_module._generate_cover_letter(snapshot)

        resume = cover_module.RESUME_PATH.read_text(encoding="utf-8")
        vacancy_text = cover_module._build_vacancy_text(snapshot)
        language = _detect_language(vacancy_text)
        llm = LLMProvider()

        if _is_ai_relevant(llm, vacancy_text):
            cover_letter = _correct_ai_project_context(
                llm,
                current_cover_letter=base_cover_letter,
                resume=resume,
                vacancy_text=vacancy_text,
                language=language,
            )
        else:
            cover_letter = base_cover_letter

        return cover_module.CoverLetterResult(
            title=snapshot.title,
            company=snapshot.company,
            url=snapshot.url,
            cover_letter=cover_letter,
            used_cached_evaluation=used_cached,
        )

    cover_module.create_cover_letter_for_url = create_cover_letter_for_url
    cover_module._ai_context_patch_installed = True
