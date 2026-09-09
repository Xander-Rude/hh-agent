import re

from sqlalchemy import select

from app.db import SessionLocal
from .models import Company, Contact, EntryPoint, Note, Person, TargetedHuntCase


def normalize_company_name(name: str) -> str:
    value = re.sub(r"[^\wа-яё]+", " ", name.lower(), flags=re.IGNORECASE)
    return " ".join(value.split())


def get_or_create_company(session, name: str, website: str | None = None) -> Company:
    normalized = normalize_company_name(name)
    company = session.scalars(select(Company).where(Company.normalized_name == normalized)).first()
    if company is None:
        company = Company(name=name.strip(), normalized_name=normalized, website=website)
        session.add(company)
        session.flush()
    elif website and not company.website:
        company.website = website
    return company


def add_person(
    company_name: str,
    name: str,
    title: str | None = None,
    *,
    source_type: str = "user_supplied",
    confidence: float = 1.0,
    user_locked: bool = True,
) -> Person:
    with SessionLocal() as session:
        company = get_or_create_company(session, company_name)
        person = Person(
            company_id=company.id,
            name=name.strip(),
            title=(title or "").strip() or None,
            source_type=source_type,
            confidence=confidence,
            user_locked=user_locked,
        )
        session.add(person)
        session.commit()
        session.refresh(person)
        return person


def add_contact(
    person_id: int,
    channel: str,
    value: str,
    *,
    source_type: str = "user_supplied",
    source_url: str | None = None,
    confidence: float = 1.0,
    verified: bool = True,
) -> Contact:
    with SessionLocal() as session:
        existing = session.scalars(
            select(Contact).where(
                Contact.person_id == person_id,
                Contact.channel == channel.strip().lower(),
                Contact.value == value.strip(),
            )
        ).first()
        if existing is not None:
            return existing
        contact = Contact(
            person_id=person_id,
            channel=channel.strip().lower(),
            value=value.strip(),
            source_type=source_type,
            source_url=source_url,
            confidence=confidence,
            verified=verified,
        )
        session.add(contact)
        session.commit()
        session.refresh(contact)
        return contact


def add_note(
    body: str,
    *,
    company_id: int | None = None,
    person_id: int | None = None,
    vacancy_id: int | None = None,
    source_type: str = "user_supplied",
) -> Note:
    if not any((company_id, person_id, vacancy_id)):
        raise ValueError("note must be attached to company, person, or vacancy")
    with SessionLocal() as session:
        note = Note(
            company_id=company_id,
            person_id=person_id,
            vacancy_id=vacancy_id,
            body=body.strip(),
            source_type=source_type,
        )
        session.add(note)
        session.commit()
        session.refresh(note)
        return note


def lock_entry_point(case_id: int, person_id: int, rationale: str | None = None) -> EntryPoint:
    """User choice wins over automated ranking while alternatives remain visible."""
    with SessionLocal() as session:
        for item in session.scalars(select(EntryPoint).where(EntryPoint.case_id == case_id)).all():
            item.user_locked = False
        entry = session.scalars(
            select(EntryPoint).where(EntryPoint.case_id == case_id, EntryPoint.person_id == person_id)
        ).first()
        if entry is None:
            entry = EntryPoint(
                case_id=case_id,
                person_id=person_id,
                score=100,
                confidence=1.0,
                rationale=rationale or "selected by user",
                source_type="user_supplied",
                user_locked=True,
            )
            session.add(entry)
        else:
            entry.score = 100
            entry.confidence = 1.0
            entry.user_locked = True
            entry.source_type = "user_supplied"
            if rationale:
                entry.rationale = rationale
        session.commit()
        session.refresh(entry)
        return entry
