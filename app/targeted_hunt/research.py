from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select

from app.db import Evaluation, SessionLocal, Vacancy
from app.llm import LLMProvider
from .eligibility import evaluate_eligibility
from .models import EntryPoint, IntelligenceSource, Person, TargetedHuntCase
from .service import get_or_create_company
from .web_search import PublicWebSearch, SearchResult


@dataclass(frozen=True)
class ResearchOutcome:
    case_id: int
    candidates: int
    status: str


SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "title": {"type": "string"},
                    "score": {"type": "integer"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                    "evidence_indexes": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["name", "title", "score", "confidence", "reason", "evidence_indexes"],
            },
        }
    },
    "required": ["candidates"],
}


def _evidence_text(results: list[SearchResult]) -> str:
    return "\n\n".join(
        f"[{i}] TITLE: {r.title}\nURL: {r.url}\nSNIPPET: {r.snippet}"
        for i, r in enumerate(results)
    )


def _queries(company: str, title: str) -> list[str]:
    return [
        f'"{company}" {title} руководитель CTO CIO директор',
        f'"{company}" IT директор CTO CIO delivery PMO конференция',
        f'"{company}" {title} команда руководитель',
    ]


def research_case(vacancy_id: int) -> ResearchOutcome:
    search = PublicWebSearch()
    llm = LLMProvider()
    with SessionLocal() as session:
        vacancy = session.get(Vacancy, vacancy_id)
        if vacancy is None:
            raise ValueError(f"vacancy {vacancy_id} not found")
        evaluation = session.scalars(
            select(Evaluation).where(Evaluation.vacancy_id == vacancy_id).order_by(Evaluation.id.desc())
        ).first()
        if evaluation is None:
            raise ValueError("vacancy has no evaluation")
        eligibility = evaluate_eligibility(vacancy, evaluation)
        if not eligibility.eligible:
            raise ValueError(f"not eligible: {eligibility.reason}")
        company_name = vacancy.company or ""
        vacancy_title = vacancy.title or ""

        case = session.scalars(select(TargetedHuntCase).where(TargetedHuntCase.vacancy_id == vacancy_id)).first()
        if case is None:
            case = TargetedHuntCase(
                vacancy_id=vacancy_id,
                status="researching",
                eligibility_score=eligibility.score,
                reason=eligibility.reason,
            )
            session.add(case)
            session.flush()
        else:
            case.status = "researching"

        company = get_or_create_company(session, company_name)
        case_id = case.id
        company_id = company.id
        session.commit()

    evidence: list[SearchResult] = []
    seen_urls: set[str] = set()
    for query in _queries(company_name, vacancy_title):
        for result in search.search(query):
            if result.url and result.url not in seen_urls:
                seen_urls.add(result.url)
                evidence.append(result)

    if not evidence:
        with SessionLocal() as session:
            case = session.get(TargetedHuntCase, case_id)
            case.status = "retry"
            case.reason = "public search returned no evidence"
            session.commit()
        return ResearchOutcome(case_id, 0, "retry")

    prompt = f"""You rank possible human entry points for a job candidate.
Company: {company_name}
Vacancy: {vacancy_title}

Rules:
- Use ONLY the numbered evidence below.
- Return a person only when the evidence explicitly contains their full name and current/relevant role.
- Never invent names, titles, contacts or reporting lines.
- Prefer the likely direct manager/function leader over a famous but distant executive.
- score is 0..100; confidence is 0..1.
- evidence_indexes must point to evidence that actually supports that person/title.
- If evidence is insufficient, return an empty candidates array.

EVIDENCE:\n{_evidence_text(evidence)}
"""
    response = llm.chat([{"role": "user", "content": prompt}], format_schema=SCHEMA)
    payload = json.loads(response.message.content)
    candidates = payload.get("candidates", [])[:8]

    saved = 0
    with SessionLocal() as session:
        case = session.get(TargetedHuntCase, case_id)
        for candidate in candidates:
            indexes = [i for i in candidate.get("evidence_indexes", []) if isinstance(i, int) and 0 <= i < len(evidence)]
            if not indexes:
                continue
            name = str(candidate.get("name", "")).strip()
            if not name or not any(name.lower() in (evidence[i].title + " " + evidence[i].snippet).lower() for i in indexes):
                continue
            person = Person(
                company_id=company_id,
                name=name,
                title=str(candidate.get("title", "")).strip() or None,
                source_type="agent_found",
                confidence=max(0.0, min(1.0, float(candidate.get("confidence", 0.5)))),
                user_locked=False,
            )
            session.add(person)
            session.flush()
            session.add(
                EntryPoint(
                    case_id=case_id,
                    person_id=person.id,
                    score=max(0, min(100, int(candidate.get("score", 0)))),
                    confidence=person.confidence,
                    rationale=str(candidate.get("reason", ""))[:2000],
                    source_type="agent_found",
                )
            )
            for index in indexes:
                item = evidence[index]
                session.add(
                    IntelligenceSource(
                        entity_type="person",
                        entity_id=person.id,
                        url=item.url,
                        source_type="public_web",
                        excerpt=(item.title + " — " + item.snippet)[:3000],
                    )
                )
            saved += 1
        case.status = "ready" if saved else "needs_manual_research"
        case.reason = f"{saved} grounded entry-point candidates"
        session.commit()
    return ResearchOutcome(case_id, saved, "ready" if saved else "needs_manual_research")
