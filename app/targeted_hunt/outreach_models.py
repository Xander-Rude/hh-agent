from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, engine


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class OutreachAttempt(Base):
    __tablename__ = "hunt_outreach_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("targeted_hunt_cases.id"), index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("hunt_people.id"), index=True)
    channel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    draft: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


Base.metadata.create_all(bind=engine)
