from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)


ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "hh_agent.db"

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    echo=False,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    pass


class Vacancy(Base):
    __tablename__ = "vacancies"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    hh_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(500),
    )

    company: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    url: Mapped[str] = mapped_column(
        Text,
    )

    salary_from: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    salary_to: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    salary_currency: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )

    description: Mapped[str] = mapped_column(
        Text,
    )

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    found_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )

    processed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    evaluations: Mapped[list["Evaluation"]] = relationship(
        back_populates="vacancy",
        cascade="all, delete-orphan",
    )

    applications: Mapped[list["Application"]] = relationship(
        back_populates="vacancy",
        cascade="all, delete-orphan",
    )


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    vacancy_id: Mapped[int] = mapped_column(
        ForeignKey("vacancies.id"),
        index=True,
    )

    score: Mapped[int] = mapped_column(
        Integer,
    )

    decision: Mapped[str] = mapped_column(
        String(32),
    )

    role_match: Mapped[int] = mapped_column(
        Integer,
    )

    seniority_match: Mapped[int] = mapped_column(
        Integer,
    )

    domain_match: Mapped[int] = mapped_column(
        Integer,
    )

    responsibility_match: Mapped[int] = mapped_column(
        Integer,
    )

    must_have_missing: Mapped[str] = mapped_column(
        Text,
    )

    nice_to_have_missing: Mapped[str] = mapped_column(
        Text,
    )

    strengths: Mapped[str] = mapped_column(
        Text,
    )

    gaps: Mapped[str] = mapped_column(
        Text,
    )

    red_flags: Mapped[str] = mapped_column(
        Text,
    )

    summary: Mapped[str] = mapped_column(
        Text,
    )

    recommendation: Mapped[str] = mapped_column(
        Text,
    )

    cover_letter: Mapped[str] = mapped_column(
        Text,
    )

    model: Mapped[str] = mapped_column(
        String(128),
    )

    selected_resume_key: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    selected_resume_title: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    selected_resume_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    selected_resume_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )

    vacancy: Mapped["Vacancy"] = relationship(
        back_populates="evaluations",
    )


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    vacancy_id: Mapped[int] = mapped_column(
        ForeignKey("vacancies.id"),
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(64),
        default="pending",
    )

    cover_letter: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    selected_resume_key: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    selected_resume_title: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    selected_resume_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    selected_resume_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )

    vacancy: Mapped["Vacancy"] = relationship(
        back_populates="applications",
    )


def _add_missing_column(
    table_name: str,
    column_name: str,
    ddl_type: str,
) -> None:
    inspector = inspect(engine)

    existing = {
        item["name"]
        for item in inspector.get_columns(table_name)
    }

    if column_name in existing:
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                f"ALTER TABLE {table_name} "
                f"ADD COLUMN {column_name} {ddl_type}"
            )
        )

    print(
        f"[DB MIGRATION] {table_name}.{column_name} added"
    )


def init_db() -> None:
    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    Base.metadata.create_all(
        bind=engine,
    )

    migration_columns = {
        "evaluations": {
            "selected_resume_key": "VARCHAR(64)",
            "selected_resume_title": "VARCHAR(500)",
            "selected_resume_id": "VARCHAR(128)",
            "selected_resume_score": "INTEGER",
        },
        "applications": {
            "selected_resume_key": "VARCHAR(64)",
            "selected_resume_title": "VARCHAR(500)",
            "selected_resume_id": "VARCHAR(128)",
            "selected_resume_score": "INTEGER",
        },
    }

    for table_name, columns in migration_columns.items():
        for column_name, ddl_type in columns.items():
            _add_missing_column(
                table_name,
                column_name,
                ddl_type,
            )


init_db()
