from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select

from app.db import SessionLocal, Vacancy


@dataclass(slots=True)
class RawVacancy:
    source: str
    external_id: str
    title: str
    company: str | None
    url: str
    description: str
    salary_from: int | None = None
    salary_to: int | None = None
    salary_currency: str | None = None
    published_at: datetime | None = None


@dataclass(slots=True)
class SourceResult:
    vacancies: list[RawVacancy] = field(default_factory=list)
    skipped: int = 0
    errors: int = 0


class VacancySource(ABC):
    name: str

    @abstractmethod
    def collect(self) -> SourceResult:
        raise NotImplementedError


def storage_id(source: str, external_id: str) -> str:
    if source == "hh":
        return external_id
    return f"{source}:{external_id}"


def vacancy_exists(source: str, external_id: str) -> bool:
    session = SessionLocal()
    try:
        existing = session.scalar(
            select(Vacancy.id).where(
                Vacancy.source == source,
                Vacancy.external_id == external_id,
            )
        )
        if existing is not None:
            return True

        # Совместимость с базой до миграции source/external_id.
        legacy_id = storage_id(source, external_id)
        existing = session.scalar(
            select(Vacancy.id).where(Vacancy.hh_id == legacy_id)
        )
        return existing is not None
    finally:
        session.close()


def save_vacancy(raw: RawVacancy) -> bool:
    session = SessionLocal()
    try:
        existing = session.scalar(
            select(Vacancy.id).where(
                Vacancy.source == raw.source,
                Vacancy.external_id == raw.external_id,
            )
        )
        if existing is not None:
            return False

        legacy_id = storage_id(raw.source, raw.external_id)
        existing = session.scalar(
            select(Vacancy.id).where(Vacancy.hh_id == legacy_id)
        )
        if existing is not None:
            # Запись могла быть создана до появления source/external_id.
            vacancy = session.get(Vacancy, existing)
            if vacancy is not None:
                vacancy.source = raw.source
                vacancy.external_id = raw.external_id
                session.commit()
            return False

        session.add(
            Vacancy(
                hh_id=legacy_id,
                source=raw.source,
                external_id=raw.external_id,
                title=raw.title,
                company=raw.company,
                url=raw.url,
                salary_from=raw.salary_from,
                salary_to=raw.salary_to,
                salary_currency=raw.salary_currency,
                description=raw.description,
                published_at=raw.published_at,
                processed=False,
            )
        )
        session.commit()
        return True
    finally:
        session.close()
