import re

from sqlalchemy import select

from app.db import SessionLocal, Vacancy
from .models import Company, Contact, EntryPoint, Note, Person, TargetedHuntCase


def normalize_company_name(name: str) -> str:
    value = re.sub(r"[^\wа-яё]+", " ", name.lower(), flags=re.IGNORECASE)
    return " ".join(value.split())


def normalize_person_name(name: str) -> str:
    return " ".join((name or "").casefold().split())


def get_or_create_company(session, name: str, website: str | None = None) -> Company:
    normalized = normalize_company_name(name)
    if not normalized:
        raise ValueError("company name is empty")
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
    clean_name = " ".join((name or "").split())
    if not clean_name:
        raise ValueError("person name is empty")
    clean_title = " ".join((title or "").split()) or None
    with SessionLocal() as session:
        company = get_or_create_company(session, company_name)
        people = session.scalars(select(Person).where(Person.company_id == company.id)).all()
        person = next((p for p in people if normalize_person_name(p.name) == normalize_person_name(clean_name)), None)
        if person is None:
            person = Person(
                company_id=company.id,
                name=clean_name,
                title=clean_title,
                source_type=source_type,
                confidence=confidence,
                user_locked=user_locked,
            )
            session.add(person)
        else:
            if clean_title:
                person.title = clean_title
            if source_type == "user_supplied":
                person.source_type = source_type
                person.confidence = max(person.confidence, confidence)
                person.user_locked = person.user_locked or user_locked
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
    clean_channel = (channel or "").strip().lower()
    clean_value = (value or "").strip()
    if not clean_channel or not clean_value:
        raise ValueError("contact channel and value are required")
    with SessionLocal() as session:
        if session.get(Person, person_id) is None:
            raise ValueError("person not found")
        existing = session.scalars(
            select(Contact).where(
                Contact.person_id == person_id,
                Contact.channel == clean_channel,
                Contact.value == clean_value,
            )
        ).first()
        if existing is not None:
            if source_type == "user_supplied":
                existing.source_type = source_type
                existing.confidence = max(existing.confidence, confidence)
                existing.verified = existing.verified or verified
                if source_url and not existing.source_url:
                    existing.source_url = source_url
                session.commit()
                session.refresh(existing)
            return existing
        contact = Contact(
            person_id=person_id,
            channel=clean_channel,
            value=clean_value,
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
    targets = [company_id is not None, person_id is not None, vacancy_id is not None]
    if sum(targets) != 1:
        raise ValueError("note must be attached to exactly one company, person, or vacancy")
    clean_body = (body or "").strip()
    if not clean_body:
        raise ValueError("note body is empty")
    with SessionLocal() as session:
        if company_id is not None and session.get(Company, company_id) is None:
            raise ValueError("company not found")
        if person_id is not None and session.get(Person, person_id) is None:
            raise ValueError("person not found")
        if vacancy_id is not None and session.get(Vacancy, vacancy_id) is None:
            raise ValueError("vacancy not found")
        note = Note(
            company_id=company_id,
            person_id=person_id,
            vacancy_id=vacancy_id,
            body=clean_body,
            source_type=source_type,
        )
        session.add(note)
        session.commit()
        session.refresh(note)
        return note


def lock_entry_point(case_id: int, person_id: int, rationale: str | None = None) -> EntryPoint:
    """User choice wins over automated ranking while alternatives remain visible."""
    with SessionLocal() as session:
        case = session.get(TargetedHuntCase, case_id)
        person = session.get(Person, person_id)
        if case is None or person is None:
            raise ValueError("case or person not found")
        vacancy = session.get(Vacancy, case.vacancy_id)
        if vacancy is None:
            raise ValueError("vacancy not found")
        if person.company_id is not None:
            company = session.get(Company, person.company_id)
            if company is not None and normalize_company_name(vacancy.company or "") != company.normalized_name:
                raise ValueError("person belongs to a different company")
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
