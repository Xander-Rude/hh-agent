from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import SessionLocal, Vacancy
from .models import Contact, EntryPoint, OutreachAttempt, Person, TargetedHuntCase


OUTREACH_STATUSES = {"draft", "sent", "replied", "call", "interview", "final", "offer", "closed"}


@dataclass(frozen=True)
class OutreachPackage:
    case_id: int
    vacancy_id: int
    vacancy_title: str
    company: str
    person_id: int
    person_name: str
    person_title: str
    score: int
    confidence: float
    rationale: str
    contacts: tuple[tuple[int, str, str, bool], ...]


def get_outreach_package(vacancy_id: int) -> OutreachPackage:
    """Return the best current entry point; a user-locked choice always wins."""
    with SessionLocal() as session:
        vacancy = session.get(Vacancy, vacancy_id)
        if vacancy is None:
            raise ValueError(f"vacancy {vacancy_id} not found")
        case = session.scalars(
            select(TargetedHuntCase).where(TargetedHuntCase.vacancy_id == vacancy_id)
        ).first()
        if case is None:
            raise ValueError("targeted hunt case not found")
        entries = session.scalars(
            select(EntryPoint).where(EntryPoint.case_id == case.id).order_by(
                EntryPoint.user_locked.desc(), EntryPoint.score.desc(), EntryPoint.confidence.desc()
            )
        ).all()
        if not entries:
            raise ValueError("entry point not found")
        entry = entries[0]
        person = session.get(Person, entry.person_id)
        if person is None:
            raise ValueError("entry point person not found")
        contacts = session.scalars(
            select(Contact).where(Contact.person_id == person.id).order_by(
                Contact.verified.desc(), Contact.confidence.desc(), Contact.id.asc()
            )
        ).all()
        return OutreachPackage(
            case_id=case.id,
            vacancy_id=vacancy.id,
            vacancy_title=vacancy.title or "—",
            company=vacancy.company or "—",
            person_id=person.id,
            person_name=person.name,
            person_title=person.title or "—",
            score=entry.score,
            confidence=entry.confidence,
            rationale=entry.rationale or "—",
            contacts=tuple((c.id, c.channel, c.value, c.verified) for c in contacts),
        )


def render_package(package: OutreachPackage) -> str:
    lines = [
        f"🎯 TARGETED HUNT — {package.score}/100",
        f"{package.vacancy_title} — {package.company}",
        "",
        f"Entry point: #{package.person_id} {package.person_name}",
        f"{package.person_title} | confidence {package.confidence:.0%}",
        f"Почему: {package.rationale}",
    ]
    if package.contacts:
        lines.append("\nПубличные/добавленные контакты:")
        for contact_id, channel, value, verified in package.contacts:
            lines.append(f"• #{contact_id} {channel}: {value}{' ✓' if verified else ''}")
    else:
        lines.append("\nКонтактов пока нет. Добавить: /contact PERSON_ID | channel | value")
    lines.append(f"\nЗафиксировать отправку: /outreach {package.vacancy_id} | CONTACT_ID | sent")
    return "\n".join(lines)[:4000]


def record_outreach(vacancy_id: int, contact_id: int, status: str, note: str | None = None) -> OutreachAttempt:
    status = status.strip().lower()
    if status not in OUTREACH_STATUSES:
        raise ValueError(f"unknown outreach status: {status}")
    now = datetime.now(UTC).replace(tzinfo=None)
    with SessionLocal() as session:
        case = session.scalars(
            select(TargetedHuntCase).where(TargetedHuntCase.vacancy_id == vacancy_id)
        ).first()
        contact = session.get(Contact, contact_id)
        if case is None or contact is None:
            raise ValueError("case or contact not found")
        entry = session.scalars(
            select(EntryPoint).where(
                EntryPoint.case_id == case.id,
                EntryPoint.person_id == contact.person_id,
            )
        ).first()
        if entry is None:
            raise ValueError("contact person is not an entry point for this case")
        attempt = session.scalars(
            select(OutreachAttempt).where(
                OutreachAttempt.case_id == case.id,
                OutreachAttempt.person_id == contact.person_id,
                OutreachAttempt.contact_id == contact.id,
            ).order_by(OutreachAttempt.id.desc())
        ).first()
        if attempt is None:
            attempt = OutreachAttempt(
                case_id=case.id,
                person_id=contact.person_id,
                contact_id=contact.id,
                channel=contact.channel,
            )
            session.add(attempt)
        attempt.status = status
        if note:
            attempt.outcome_note = note.strip()
        if status in {"sent", "replied", "call", "interview", "final", "offer"} and attempt.sent_at is None:
            attempt.sent_at = now
        if status in {"replied", "call", "interview", "final", "offer"} and attempt.replied_at is None:
            attempt.replied_at = now
        session.commit()
        session.refresh(attempt)
        return attempt
