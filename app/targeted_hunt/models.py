from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, engine


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Company(Base):
    __tablename__ = "hunt_companies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(500), index=True)
    normalized_name: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Person(Base):
    __tablename__ = "hunt_people"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("hunt_companies.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(500), index=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), default="agent_found", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    user_locked: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    company: Mapped[Company | None] = relationship()


class Contact(Base):
    __tablename__ = "hunt_contacts"
    __table_args__ = (UniqueConstraint("person_id", "channel", "value", name="uq_hunt_contact"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("hunt_people.id"), index=True)
    channel: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(32), default="agent_found")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    verified: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    person: Mapped[Person] = relationship()


class Note(Base):
    __tablename__ = "hunt_notes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("hunt_companies.id"), nullable=True, index=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("hunt_people.id"), nullable=True, index=True)
    vacancy_id: Mapped[int | None] = mapped_column(ForeignKey("vacancies.id"), nullable=True, index=True)
    body: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(32), default="user_supplied")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Relationship(Base):
    __tablename__ = "hunt_relationships"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_person_id: Mapped[int] = mapped_column(ForeignKey("hunt_people.id"), index=True)
    to_person_id: Mapped[int | None] = mapped_column(ForeignKey("hunt_people.id"), nullable=True, index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("hunt_companies.id"), nullable=True, index=True)
    relation_type: Mapped[str] = mapped_column(String(64), index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="user_supplied")
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class IntelligenceSource(Base):
    __tablename__ = "hunt_sources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(64), index=True)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TargetedHuntCase(Base):
    __tablename__ = "targeted_hunt_cases"
    __table_args__ = (UniqueConstraint("vacancy_id", name="uq_targeted_hunt_vacancy"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="new", index=True)
    eligibility_score: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class EntryPoint(Base):
    __tablename__ = "hunt_entry_points"
    __table_args__ = (UniqueConstraint("case_id", "person_id", name="uq_hunt_entry_point"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("targeted_hunt_cases.id"), index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("hunt_people.id"), index=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), default="agent_found")
    user_locked: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class OutreachAttempt(Base):
    __tablename__ = "hunt_outreach_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("targeted_hunt_cases.id"), index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("hunt_people.id"), index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("hunt_contacts.id"), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(64), index=True)
    message_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    outcome_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


Base.metadata.create_all(bind=engine)
