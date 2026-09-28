from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import CoverLetterArtifact, CleanShadowAssessment, Vacancy
from app.llm import LLMProvider
from app.preferences import load_preferences


ROOT = Path(__file__).resolve().parent.parent
RESUME_PATH = Path(
    os.getenv("COVER_LETTER_RESUME_PATH", str(ROOT / "data" / "resume.txt"))
)
PROMPT_VERSION = "canonical-cover-v1"
AI_PROJECT_URL = "https://rudenko.one/hh-agent.html"

_SIGNATURES = (
    "С уважением,\nАлександр Руденко",
    "Best regards,\nAleksandr Rudenko",
)
_PLACEHOLDERS = (
    "[ваше имя]",
    "[имя]",
    "<ваше имя>",
    "<имя>",
    "{ваше имя}",
    "{имя}",
    "{name}",
    "[name]",
    "<name>",
    "your name",
)
_BAD_PHRASES = (
    "уважаемый hr",
    "уважаемый рекрутер",
    "уверен, что",
    "уверен, мой",
    "готов обсудить",
    "многолетний опыт",
    "на уровне senior/lead",
    "идеально подходит",
    "полностью соответствует",
    "dear hr",
    "dear recruiter",
    "i am confident that",
    "i'm confident that",
    "happy to discuss",
    "many years of experience",
    "senior/lead level",
    "perfect fit",
    "fully matches",
)
_AI_RE = re.compile(
    r"(?:\bAI\b|\bLLM\b|\bML\b|искусственн\w* интеллект\w*|"
    r"нейросет\w*|машинн\w* обучен\w*|AI[- ]?агент\w*)",
    re.I,
)
_LEGAL_COMPANY_WORDS = {
    "ооо", "пао", "ао", "зао", "оао", "ип",
    "llc", "inc", "ltd", "company", "компания",
    "гк", "группа", "управляющая",
}


class CoverLetterValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeneratedCoverLetter:
    draft_text: str
    final_text: str
    generation_attempts: int


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _normalize(value: str | None) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", " ", (value or "").lower()).strip()


def _strip_signature(value: str) -> str:
    text = value.strip()
    for signature in _SIGNATURES:
        if text.endswith(signature):
            text = text[: -len(signature)].rstrip()
    return text


def _detect_language(title: str, description: str) -> str:
    sample = f"{title}\n{description}"
    cyr = len(re.findall(r"[А-Яа-яЁё]", sample))
    lat = len(re.findall(r"[A-Za-z]", sample))
    return "en" if lat > cyr else "ru"


def _normalize_generated(value: str, language: str) -> str:
    text = (value or "").strip()
    fence = chr(96) * 3
    for prefix in (fence + "text", fence + "markdown", fence):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].lstrip()
            break
    if text.endswith(fence):
        text = text[:-len(fence)].rstrip()
    text = _strip_signature(text)
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if language == "ru" and not text.lower().startswith("здравствуйте"):
        text = "Здравствуйте!\n\n" + text
    elif language == "en" and not text.lower().startswith(("hello", "hi")):
        text = "Hello!\n\n" + text
    signature = _SIGNATURES[1] if language == "en" else _SIGNATURES[0]
    return f"{text}\n\n{signature}".strip()


def vacancy_content_hash(vacancy: Vacancy) -> str:
    payload = "\n".join(
        [
            str(vacancy.source or ""),
            str(vacancy.external_id or vacancy.hh_id or ""),
            str(vacancy.title or ""),
            str(vacancy.company or ""),
            str(vacancy.description or ""),
            str(vacancy.source_payload_hash or ""),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def _resume_version(resume: str) -> str:
    digest = hashlib.sha256(resume.encode("utf-8", errors="replace")).hexdigest()
    return f"resume-sha256:{digest[:16]}"


def _extraction_payload(extraction_json: object) -> dict[str, Any]:
    if isinstance(extraction_json, dict):
        return extraction_json
    if not extraction_json:
        return {}
    try:
        value = json.loads(str(extraction_json))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _evidence_packet(extraction_json: object) -> dict[str, Any]:
    extraction = _extraction_payload(extraction_json)
    requirements = [
        item for item in (extraction.get("requirements") or [])
        if isinstance(item, dict)
    ]
    rank = {"non_negotiable": 0, "core": 1, "preferred": 2, "context": 3}
    requirements.sort(
        key=lambda item: (
            rank.get(str(item.get("criticality") or ""), 4),
            0 if item.get("match_quality") == "full" else 1,
        )
    )
    matched: list[dict[str, str]] = []
    gaps: list[str] = []
    for item in requirements:
        source = str(item.get("source_text") or "").strip()
        evidence = str(item.get("candidate_evidence") or "").strip()
        quality = str(item.get("match_quality") or "").strip()
        if not source:
            continue
        if quality == "full" and evidence:
            matched.append(
                {
                    "requirement": source[:500],
                    "evidence": evidence[:500],
                    "criticality": str(item.get("criticality") or ""),
                }
            )
        elif quality in {"partial", "none"}:
            gaps.append(source[:500])
    return {
        "role_family": extraction.get("role_family_primary"),
        "matched": matched[:7],
        "gaps": gaps[:5],
        "invite_reasons": list(extraction.get("top_invite_reasons") or [])[:5],
        "invite_risks": list(extraction.get("invite_risks") or [])[:5],
    }


def _company_aliases(company: str | None) -> set[str]:
    raw = str(company or "").strip()
    if not raw:
        return set()
    tokens = re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", raw)
    useful = [t for t in tokens if t.lower() not in _LEGAL_COMPANY_WORDS]
    aliases = {_normalize(raw)}
    cleaned = _normalize(" ".join(useful))
    if cleaned:
        aliases.add(cleaned)
    if useful:
        first = _normalize(useful[0])
        if len(first) >= 3 or any(ch.isdigit() for ch in first):
            aliases.add(first)
    return {item for item in aliases if item}


def _title_aliases(title: str | None) -> set[str]:
    raw = str(title or "").strip()
    if not raw:
        return set()
    aliases = {_normalize(raw)}
    for piece in re.split(r"[(/|]", raw):
        normalized = _normalize(piece)
        if len(normalized) >= 12 and len(normalized.split()) >= 2:
            aliases.add(normalized)

    # Catch common Russian case forms of role nouns without treating verbs
    # such as "СЂСѓРєРѕРІРѕРґРёР» РїСЂРѕРµРєС‚Р°РјРё" as the official vacancy title.
    variants = {
        "\u0440\u0443\u043a\u043e\u0432\u043e\u0434\u0438\u0442\u0435\u043b\u044c": (
            "\u0440\u0443\u043a\u043e\u0432\u043e\u0434\u0438\u0442\u0435\u043b\u044f",
            "\u0440\u0443\u043a\u043e\u0432\u043e\u0434\u0438\u0442\u0435\u043b\u0435\u043c",
        ),
        "\u043c\u0435\u043d\u0435\u0434\u0436\u0435\u0440": (
            "\u043c\u0435\u043d\u0435\u0434\u0436\u0435\u0440\u0430",
            "\u043c\u0435\u043d\u0435\u0434\u0436\u0435\u0440\u043e\u043c",
        ),
        "\u0434\u0438\u0440\u0435\u043a\u0442\u043e\u0440": (
            "\u0434\u0438\u0440\u0435\u043a\u0442\u043e\u0440\u0430",
            "\u0434\u0438\u0440\u0435\u043a\u0442\u043e\u0440\u043e\u043c",
        ),
        "\u0432\u043b\u0430\u0434\u0435\u043b\u0435\u0446": (
            "\u0432\u043b\u0430\u0434\u0435\u043b\u044c\u0446\u0430",
            "\u0432\u043b\u0430\u0434\u0435\u043b\u044c\u0446\u0435\u043c",
        ),
    }
    for alias in list(aliases):
        parts = alias.split()
        if not parts:
            continue
        for replacement in variants.get(parts[0], ()):
            aliases.add(" ".join([replacement, *parts[1:]]))
    return {item for item in aliases if item}


def _contains_alias(normalized_text: str, alias: str) -> bool:
    if not alias:
        return False
    return f" {alias} " in f" {normalized_text} "


def cover_letter_guard_issues(
    text: str,
    *,
    vacancy_title: str = "",
    vacancy_company: str | None = None,
    resume_text: str = "",
    extraction_json: object = None,
) -> list[str]:
    body = _strip_signature(text)
    normalized = _normalize(body)
    issues: list[str] = []
    if len(body) < 350:
        issues.append("too_short")
    if len(body) > 1400:
        issues.append("too_long")
    if any(marker in body.lower() for marker in _PLACEHOLDERS):
        issues.append("placeholder")
    if any(phrase in body.lower() for phrase in _BAD_PHRASES):
        issues.append("bad_phrase")
    if re.search(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", body):
        issues.append("list_format")
    if any(
        _contains_alias(normalized, alias)
        for alias in _company_aliases(vacancy_company)
    ):
        issues.append("company_name_present")
    if any(
        _contains_alias(normalized, alias)
        for alias in _title_aliases(vacancy_title)
    ):
        issues.append("vacancy_title_present")

    language = _detect_language(vacancy_title, body)
    if language == "ru":
        if not body.lower().startswith("здравствуйте"):
            issues.append("missing_greeting")
        if not re.search(
            r"\b(?:я|мой|моя|моём|моем|мне|вёл|вел|руководил|отвечал)\b",
            body,
            re.I,
        ):
            issues.append("not_first_person")
    else:
        if not body.lower().startswith(("hello", "hi")):
            issues.append("missing_greeting")
        if not re.search(r"\b(?:i|my|me|i've|i'm)\b", body, re.I):
            issues.append("not_first_person")

    packet = _evidence_packet(extraction_json)
    for item in packet["matched"]:
        source = _normalize(item["requirement"])
        if len(source) >= 45 and source in normalized:
            issues.append("copied_requirement")
            break

    if resume_text:
        allowed_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", resume_text))
        output_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", body))
        if output_numbers - allowed_numbers:
            issues.append("ungrounded_number")
    return sorted(set(issues))


def _ai_relevant(
    vacancy_title: str,
    vacancy_description: str,
    extraction_json: object,
) -> bool:
    packet = _evidence_packet(extraction_json)
    sample = " ".join(
        [
            vacancy_title,
            vacancy_description,
            json.dumps(packet, ensure_ascii=False),
        ]
    )
    return bool(_AI_RE.search(sample))


def _build_prompt(
    *,
    vacancy_title: str,
    vacancy_description: str,
    resume_text: str,
    preferences: dict[str, Any],
    extraction_json: object,
) -> str:
    language = _detect_language(vacancy_title, vacancy_description)
    language_rule = "Пиши на русском языке." if language == "ru" else "Write in English."
    packet = _evidence_packet(extraction_json)
    ai_rule = (
        "Если это естественно усиливает письмо, можно кратко упомянуть собственный "
        f"AI-agent проект и только эту ссылку: {AI_PROJECT_URL}."
        if _ai_relevant(vacancy_title, vacancy_description, extraction_json)
        else "Не упоминай AI-agent проект кандидата: для этой вакансии он не нужен."
    )
    return f"""
Ты пишешь финальное сопроводительное письмо от первого лица.
{language_rule}

Цель: показать 2-3 конкретные точки пересечения реального опыта кандидата
с задачами работодателя. Не пересказывай вакансию.

ЖЁСТКИЕ ПРАВИЛА:
- используй только подтверждённые факты из резюме и CONFIRMED EVIDENCE;
- requirement работодателя сам по себе НЕ является фактом о кандидате;
- не копируй формулировки требований вакансии предложениями;
- 500-1100 знаков без подписи, 2-4 коротких абзаца, без списков;
- не пиши "Уверен", "Готов обсудить", "идеально подходит",
  "многолетний опыт", "на уровне senior/lead";
- максимум один scale-факт: бюджет, размер команды или портфель;
- НЕ ПИШИ НАЗВАНИЕ КОМПАНИИ;
- НЕ ПИШИ ОФИЦИАЛЬНОЕ НАЗВАНИЕ ПОЗИЦИИ;
- не начинай с "Рассматриваю позицию..." или "I am interested in the ... role";
- название позиции ниже дано только как внутренний контекст таргетинга;
- не добавляй подпись или имя кандидата: Python добавит их сам;
- если есть gap, не маскируй его выдуманным опытом;
- {ai_rule}

ВНУТРЕННИЙ КОНТЕКСТ РОЛИ, НЕ КОПИРОВАТЬ:
{vacancy_title[:500]}

РЕЗЮМЕ:
{resume_text[:32000]}

CONFIRMED EVIDENCE ИЗ CLEAN:
{json.dumps(packet, ensure_ascii=False, indent=2)[:12000]}

ПРЕДПОЧТЕНИЯ:
{json.dumps(preferences, ensure_ascii=False, indent=2)[:5000]}

ОПИСАНИЕ ВАКАНСИИ:
{vacancy_description[:22000]}

Верни только текст письма без markdown и комментариев.
""".strip()


def generate_cover_letter_text(
    *,
    vacancy_title: str,
    vacancy_company: str | None,
    vacancy_description: str,
    extraction_json: object = None,
    resume_text: str | None = None,
    preferences: dict[str, Any] | None = None,
    llm: LLMProvider | None = None,
) -> GeneratedCoverLetter:
    resume = resume_text
    if resume is None:
        if not RESUME_PATH.exists():
            raise RuntimeError(f"Не найдено резюме: {RESUME_PATH}")
        resume = RESUME_PATH.read_text(encoding="utf-8", errors="replace")
    prefs = preferences if preferences is not None else load_preferences()
    provider = llm or LLMProvider()
    language = _detect_language(vacancy_title, vacancy_description)
    prompt = _build_prompt(
        vacancy_title=vacancy_title,
        vacancy_description=vacancy_description,
        resume_text=resume,
        preferences=prefs,
        extraction_json=extraction_json,
    )

    response = provider.chat(messages=[{"role": "user", "content": prompt}])
    raw = str(getattr(getattr(response, "message", None), "content", "") or "")
    first = _normalize_generated(raw, language)
    issues = cover_letter_guard_issues(
        first,
        vacancy_title=vacancy_title,
        vacancy_company=vacancy_company,
        resume_text=resume,
        extraction_json=extraction_json,
    )
    if not issues:
        return GeneratedCoverLetter(first, first, 1)

    repair_prompt = f"""
Перепиши черновик, исправив ВСЕ нарушения:
{json.dumps(issues, ensure_ascii=False)}

Не пиши название компании или официальное название позиции.
Не копируй requirements. Не добавляй новых фактов или цифр.
Никаких списков и подписи.

ЧЕРНОВИК:
{_strip_signature(first)}

ИСХОДНЫЕ ПРАВИЛА И КОНТЕКСТ:
{prompt}

Верни только исправленный текст.
""".strip()
    repaired_response = provider.chat(
        messages=[{"role": "user", "content": repair_prompt}]
    )
    repaired_raw = str(
        getattr(getattr(repaired_response, "message", None), "content", "") or ""
    )
    repaired = _normalize_generated(repaired_raw, language)
    repaired_issues = cover_letter_guard_issues(
        repaired,
        vacancy_title=vacancy_title,
        vacancy_company=vacancy_company,
        resume_text=resume,
        extraction_json=extraction_json,
    )
    if repaired_issues:
        raise CoverLetterValidationError(
            "canonical cover letter failed deterministic guard after repair: "
            + ",".join(repaired_issues)
        )
    return GeneratedCoverLetter(first, repaired, 2)


def _artifact_versions(
    assessment: CleanShadowAssessment | None,
    resume_text: str,
) -> tuple[str, str]:
    if assessment is not None:
        return (
            assessment.candidate_profile_version,
            assessment.recruiter_resume_version,
        )
    version = _resume_version(resume_text)
    return version, version


def get_or_enqueue_artifact(
    session: Session,
    *,
    vacancy: Vacancy,
    account_key: str,
    assessment: CleanShadowAssessment | None = None,
) -> CoverLetterArtifact:
    resume = (
        RESUME_PATH.read_text(encoding="utf-8", errors="replace")
        if RESUME_PATH.exists()
        else ""
    )
    candidate_version, recruiter_version = _artifact_versions(assessment, resume)
    content_hash = vacancy_content_hash(vacancy)
    artifact = session.scalar(
        select(CoverLetterArtifact)
        .where(
            CoverLetterArtifact.vacancy_id == vacancy.id,
            CoverLetterArtifact.account_key == account_key,
            CoverLetterArtifact.candidate_profile_version == candidate_version,
            CoverLetterArtifact.recruiter_resume_version == recruiter_version,
            CoverLetterArtifact.vacancy_content_hash == content_hash,
            CoverLetterArtifact.prompt_version == PROMPT_VERSION,
        )
        .order_by(CoverLetterArtifact.id.desc())
        .limit(1)
    )
    if artifact is not None:
        return artifact

    artifact = CoverLetterArtifact(
        vacancy_id=vacancy.id,
        account_key=account_key,
        clean_assessment_id=(assessment.id if assessment is not None else None),
        candidate_profile_version=candidate_version,
        recruiter_resume_version=recruiter_version,
        vacancy_content_hash=content_hash,
        prompt_version=PROMPT_VERSION,
        status="pending",
        validation_json="[]",
    )
    session.add(artifact)
    session.flush()
    return artifact


def final_cover_letter(
    session: Session,
    *,
    vacancy: Vacancy,
    account_key: str,
    assessment: CleanShadowAssessment | None = None,
) -> str | None:
    artifact = get_or_enqueue_artifact(
        session,
        vacancy=vacancy,
        account_key=account_key,
        assessment=assessment,
    )
    if artifact.status != "final":
        return None
    return (artifact.final_text or "").strip() or None


def generate_artifact(
    session: Session,
    artifact: CoverLetterArtifact,
    *,
    llm: LLMProvider | None = None,
) -> CoverLetterArtifact:
    vacancy = session.get(Vacancy, artifact.vacancy_id)
    if vacancy is None:
        raise RuntimeError(f"missing vacancy_id={artifact.vacancy_id}")
    assessment = (
        session.get(CleanShadowAssessment, artifact.clean_assessment_id)
        if artifact.clean_assessment_id is not None
        else None
    )
    resume = RESUME_PATH.read_text(encoding="utf-8", errors="replace")
    claim = session.execute(
        update(CoverLetterArtifact)
        .where(
            CoverLetterArtifact.id == artifact.id,
            CoverLetterArtifact.status.in_(("pending", "error")),
        )
        .values(
            status="generating",
            generation_attempts=CoverLetterArtifact.generation_attempts + 1,
            updated_at=_now(),
        )
    )
    session.commit()
    session.refresh(artifact)
    if claim.rowcount != 1:
        if artifact.status == "final" and (artifact.final_text or "").strip():
            return artifact
        raise RuntimeError(
            f"canonical cover letter is already claimed: artifact={artifact.id} "
            f"status={artifact.status}"
        )
    try:
        generated = generate_cover_letter_text(
            vacancy_title=vacancy.title or "",
            vacancy_company=vacancy.company,
            vacancy_description=vacancy.description or "",
            extraction_json=assessment.extraction_json if assessment else None,
            resume_text=resume,
            llm=llm,
        )
        artifact.draft_text = generated.draft_text
        artifact.final_text = generated.final_text
        artifact.validation_json = "[]"
        artifact.status = "final"
        artifact.last_error = None
        artifact.finalized_at = _now()
        artifact.updated_at = _now()
        session.commit()
        return artifact
    except Exception as exc:
        artifact.status = "error"
        artifact.last_error = f"{type(exc).__name__}: {exc}"[:4000]
        artifact.updated_at = _now()
        session.commit()
        raise


def get_or_generate_cover_letter(
    session: Session,
    *,
    vacancy: Vacancy,
    account_key: str,
    assessment: CleanShadowAssessment | None = None,
    llm: LLMProvider | None = None,
) -> tuple[str, bool]:
    artifact = get_or_enqueue_artifact(
        session,
        vacancy=vacancy,
        account_key=account_key,
        assessment=assessment,
    )
    if artifact.status == "final" and (artifact.final_text or "").strip():
        return artifact.final_text.strip(), True
    if artifact.status == "generating":
        raise RuntimeError(
            f"canonical cover letter is already generating: artifact={artifact.id}"
        )
    artifact = generate_artifact(session, artifact, llm=llm)
    return (artifact.final_text or "").strip(), False
