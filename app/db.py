from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    event,
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

    # Legacy storage key. Для HH это настоящий hh_id, для карьерных сайтов
    # пока сохраняем source:external_id ради обратной совместимости.
    hh_id: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        index=True,
    )

    source: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        index=True,
    )

    external_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
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
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
    )

    hh_response_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
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


@event.listens_for(Vacancy, "before_insert")
def _populate_vacancy_source(mapper, connection, target: Vacancy) -> None:
    """Не даёт старым collectors создавать вакансии без source/external_id."""
    if target.source and target.external_id:
        return

    legacy_id = str(target.hh_id or "")

    if legacy_id.startswith("yandex:"):
        target.source = target.source or "yandex"
        target.external_id = target.external_id or legacy_id.split(":", 1)[1]
        return

    if legacy_id:
        target.source = target.source or "hh"
        target.external_id = target.external_id or legacy_id


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

    score: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(32))
    role_match: Mapped[int] = mapped_column(Integer)
    seniority_match: Mapped[int] = mapped_column(Integer)
    domain_match: Mapped[int] = mapped_column(Integer)
    responsibility_match: Mapped[int] = mapped_column(Integer)

    must_have_missing: Mapped[str] = mapped_column(Text)
    nice_to_have_missing: Mapped[str] = mapped_column(Text)
    strengths: Mapped[str] = mapped_column(Text)
    gaps: Mapped[str] = mapped_column(Text)
    red_flags: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[str] = mapped_column(Text)
    cover_letter: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(128))

    selected_resume_key: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    selected_resume_title: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    selected_resume_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    selected_resume_score: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
    )

    vacancy: Mapped["Vacancy"] = relationship(
        back_populates="evaluations",
    )


class CleanShadowAssessment(Base):
    __tablename__ = "clean_shadow_assessments"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    vacancy_id: Mapped[int] = mapped_column(
        ForeignKey("vacancies.id"),
        index=True,
    )
    legacy_evaluation_id: Mapped[int] = mapped_column(
        ForeignKey("evaluations.id"),
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        default="ok",
        index=True,
    )
    fit_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )
    invite_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )
    role_family: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    role_confidence_pct: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    hard_stops: Mapped[str] = mapped_column(
        Text,
        default="[]",
    )
    base_routing_class: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    routing_class: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    route_reason_codes: Mapped[str] = mapped_column(
        Text,
        default="[]",
    )

    company_entity_key: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        index=True,
    )
    company_rank: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    company_state: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )

    extraction_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
    )
    candidate_profile_version: Mapped[str] = mapped_column(
        String(128),
    )
    recruiter_resume_version: Mapped[str] = mapped_column(
        String(128),
    )
    learned_patterns_version: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    prompt_version: Mapped[str] = mapped_column(
        String(128),
    )
    scoring_version: Mapped[str] = mapped_column(
        String(128),
        index=True,
    )
    gate_version: Mapped[str] = mapped_column(
        String(128),
    )
    routing_version: Mapped[str] = mapped_column(
        String(128),
    )
    company_policy_version: Mapped[str] = mapped_column(
        String(128),
    )
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        index=True,
    )


class CleanRescoreRun(Base):
    __tablename__ = "clean_rescore_runs"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="running",
        index=True,
    )
    source: Mapped[str] = mapped_column(
        String(32),
        default="hh",
        index=True,
    )
    window_from: Mapped[datetime] = mapped_column(DateTime, index=True)
    window_to: Mapped[datetime] = mapped_column(DateTime, index=True)
    window_days: Mapped[int] = mapped_column(Integer, default=7)

    selected_count: Mapped[int] = mapped_column(Integer, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    ok_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    clean_candidate_count: Mapped[int] = mapped_column(Integer, default=0)

    rescore_version: Mapped[str] = mapped_column(String(128), index=True)
    candidate_profile_version: Mapped[str] = mapped_column(String(128))
    recruiter_resume_version: Mapped[str] = mapped_column(String(128))
    learned_patterns_version: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    prompt_version: Mapped[str] = mapped_column(String(128))
    scoring_version: Mapped[str] = mapped_column(String(128), index=True)
    gate_version: Mapped[str] = mapped_column(String(128))
    routing_version: Mapped[str] = mapped_column(String(128))
    company_policy_version: Mapped[str] = mapped_column(String(128))

    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        index=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )


class CleanRescoreItem(Base):
    __tablename__ = "clean_rescore_items"
    __table_args__ = (
        Index(
            "uq_clean_rescore_items_run_vacancy",
            "run_id",
            "vacancy_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    run_id: Mapped[int] = mapped_column(
        ForeignKey("clean_rescore_runs.id"),
        index=True,
    )
    vacancy_id: Mapped[int] = mapped_column(
        ForeignKey("vacancies.id"),
        index=True,
    )
    legacy_evaluation_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluations.id"),
        nullable=True,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        index=True,
    )
    availability_status: Mapped[str] = mapped_column(
        String(32),
        default="not_checked",
        index=True,
    )
    availability_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    fit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    invite_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    role_family: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    role_confidence_pct: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    hard_stops: Mapped[str] = mapped_column(Text, default="[]")
    base_routing_class: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    routing_class: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    route_reason_codes: Mapped[str] = mapped_column(Text, default="[]")

    company_entity_key: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        index=True,
    )
    company_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    company_state: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )

    vacancy_snapshot: Mapped[str] = mapped_column(Text, default="{}")
    extraction_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        index=True,
    )


class ApplicationDecisionSnapshot(Base):
    __tablename__ = "application_decision_snapshots"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id"),
        index=True,
    )
    vacancy_id: Mapped[int] = mapped_column(
        ForeignKey("vacancies.id"),
        index=True,
    )
    legacy_evaluation_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluations.id"),
        nullable=True,
        index=True,
    )
    shadow_assessment_id: Mapped[int | None] = mapped_column(
        ForeignKey("clean_shadow_assessments.id"),
        nullable=True,
        index=True,
    )

    account_key: Mapped[str] = mapped_column(
        String(32),
        index=True,
    )
    application_type: Mapped[str] = mapped_column(
        String(64),
        default="legacy",
        index=True,
    )
    routing_class: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    route_reason_codes: Mapped[str] = mapped_column(
        Text,
        default="[]",
    )
    fit_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    invite_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    hard_stops: Mapped[str] = mapped_column(
        Text,
        default="[]",
    )
    role_family: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    role_confidence_pct: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    company_entity_key: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    company_rank: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    company_state: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    candidate_profile_version: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    recruiter_resume_version: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    prompt_version: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    scoring_version: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    gate_version: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    routing_version: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    company_policy_version: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    learned_patterns_version: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )

    cover_letter_final: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    surfaced_evidence: Mapped[str] = mapped_column(
        Text,
        default="[]",
    )
    vacancy_snapshot: Mapped[str] = mapped_column(
        Text,
        default="{}",
    )
    approved_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        index=True,
    )


class CalibrationRun(Base):
    __tablename__ = "calibration_runs"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        index=True,
    )
    scope: Mapped[str] = mapped_column(
        String(64),
        default="hh_clean",
        index=True,
    )
    event_high_watermark: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )
    snapshot_high_watermark: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )
    sample_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )
    mature_sample_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )
    new_mature_sample_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )
    dataset_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    metrics_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
    )
    case_summaries_json: Mapped[str] = mapped_column(
        Text,
        default="[]",
    )
    llm_report_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    prompt_version: Mapped[str] = mapped_column(
        String(128),
    )
    llm_model: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        index=True,
    )


class StrategyMemoryVersion(Base):
    __tablename__ = "strategy_memory_versions"
    __table_args__ = (
        Index(
            "uq_strategy_memory_versions_calibration_run_id",
            "calibration_run_id",
            unique=True,
            sqlite_where=text("calibration_run_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    version_number: Mapped[int] = mapped_column(
        Integer,
        unique=True,
        index=True,
    )
    parent_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_memory_versions.id"),
        nullable=True,
        index=True,
    )
    source: Mapped[str] = mapped_column(
        String(64),
        default="manual",
        index=True,
    )
    calibration_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("calibration_runs.id"),
        nullable=True,
        index=True,
    )
    content_hash: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        index=True,
    )
    note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        index=True,
    )


class StrategyMemoryState(Base):
    __tablename__ = "strategy_memory_state"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        default=1,
    )
    active_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_memory_versions.id"),
        nullable=True,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
    )


class StrategyMemoryActivation(Base):
    __tablename__ = "strategy_memory_activations"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    version_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_memory_versions.id"),
        index=True,
    )
    previous_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_memory_versions.id"),
        nullable=True,
        index=True,
    )
    reason: Mapped[str] = mapped_column(
        String(64),
        default="activate",
    )
    note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        index=True,
    )


class StrategyCandidateProfile(Base):
    __tablename__ = "strategy_candidate_profiles"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    memory_version_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_memory_versions.id"),
        unique=True,
        index=True,
    )
    payload_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
    )
    source_ref: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    source_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )


class StrategyTargetStrategy(Base):
    __tablename__ = "strategy_target_strategies"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    memory_version_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_memory_versions.id"),
        unique=True,
        index=True,
    )
    payload_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
    )


class StrategyLearnedPattern(Base):
    __tablename__ = "strategy_learned_patterns"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    memory_version_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_memory_versions.id"),
        index=True,
    )
    pattern_key: Mapped[str] = mapped_column(
        String(160),
        index=True,
    )
    pattern_type: Mapped[str] = mapped_column(
        String(64),
        index=True,
    )
    statement: Mapped[str] = mapped_column(
        Text,
    )
    support_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )
    confidence_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    source_calibration_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("calibration_runs.id"),
        nullable=True,
        index=True,
    )
    evidence_application_ids_json: Mapped[str] = mapped_column(
        Text,
        default="[]",
    )


class StrategyGoodExample(Base):
    __tablename__ = "strategy_good_examples"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    memory_version_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_memory_versions.id"),
        index=True,
    )
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id"),
        nullable=True,
        index=True,
    )
    decision_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("application_decision_snapshots.id"),
        nullable=True,
        index=True,
    )
    outcome_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("application_events.id"),
        nullable=True,
        index=True,
    )
    label: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    rationale: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )


class StrategyBadExample(Base):
    __tablename__ = "strategy_bad_examples"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    memory_version_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_memory_versions.id"),
        index=True,
    )
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id"),
        nullable=True,
        index=True,
    )
    decision_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("application_decision_snapshots.id"),
        nullable=True,
        index=True,
    )
    outcome_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("application_events.id"),
        nullable=True,
        index=True,
    )
    label: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    rationale: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
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

    account_key: Mapped[str] = mapped_column(
        String(32),
        default="old",
        index=True,
    )

    cover_letter: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_resume_key: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    selected_resume_title: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    selected_resume_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    selected_resume_score: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Technical apply state lives in status. Career state is intentionally
    # separate: an HH workflow "invitation" is not a human response or an
    # interview and must never overwrite the transport/apply result.
    career_status: Mapped[str] = mapped_column(
        String(64),
        default="unknown",
        index=True,
    )
    response_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    manual_recovery_attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )
    manual_recovery_last_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
    )

    vacancy: Mapped["Vacancy"] = relationship(
        back_populates="applications",
    )
    events: Mapped[list["ApplicationEvent"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
    )


class ApplicationEvent(Base):
    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id"),
        index=True,
    )
    decision_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("application_decision_snapshots.id"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(64),
        index=True,
    )
    event_class: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    source: Mapped[str] = mapped_column(
        String(64),
        default="hh-agent",
    )
    attribution: Mapped[str] = mapped_column(
        String(64),
        default="unknown",
        index=True,
    )
    confidence: Mapped[str] = mapped_column(
        String(64),
        default="unknown",
    )
    raw_ref: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    details: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        index=True,
    )

    application: Mapped["Application"] = relationship(
        back_populates="events",
    )


def _add_missing_column(
    table_name: str,
    column_name: str,
    ddl_type: str,
) -> bool:
    inspector = inspect(engine)
    existing = {
        item["name"]
        for item in inspector.get_columns(table_name)
    }

    if column_name in existing:
        return False

    with engine.begin() as connection:
        connection.execute(
            text(
                f"ALTER TABLE {table_name} "
                f"ADD COLUMN {column_name} {ddl_type}"
            )
        )

    print(f"[DB MIGRATION] {table_name}.{column_name} added")
    return True


def _backfill_initial_hh_response_cache() -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE vacancies "
                "SET hh_response_checked_at=CURRENT_TIMESTAMP "
                "WHERE source='hh' OR (source IS NULL AND hh_id NOT LIKE '%:%')"
            )
        )

    print(
        "[DB MIGRATION] Existing HH vacancies marked as recently checked "
        "to avoid a one-time recheck storm"
    )


def _backfill_vacancy_sources() -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE vacancies "
                "SET source='yandex', external_id=substr(hh_id, 8) "
                "WHERE hh_id LIKE 'yandex:%' "
                "AND (source IS NULL OR external_id IS NULL)"
            )
        )
        connection.execute(
            text(
                "UPDATE vacancies "
                "SET source='hh', external_id=hh_id "
                "WHERE hh_id NOT LIKE '%:%' "
                "AND (source IS NULL OR external_id IS NULL)"
            )
        )

        # SQLite поддерживает partial unique index; старые NULL не мешают.
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_vacancies_source_external_id "
                "ON vacancies(source, external_id) "
                "WHERE source IS NOT NULL AND external_id IS NOT NULL"
            )
        )


def _backfill_application_account_keys() -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE applications "
                "SET account_key='old' "
                "WHERE account_key IS NULL OR trim(account_key)=''"
            )
        )


def _backfill_application_career_statuses() -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE applications "
                "SET career_status='submitted' "
                "WHERE (career_status IS NULL OR career_status='unknown') "
                "AND (status='applied' OR applied_at IS NOT NULL)"
            )
        )


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)

    migration_columns = {
        "vacancies": {
            "source": "VARCHAR(32)",
            "external_id": "VARCHAR(128)",
            "hh_response_checked_at": "DATETIME",
        },
        "evaluations": {
            "selected_resume_key": "VARCHAR(64)",
            "selected_resume_title": "VARCHAR(500)",
            "selected_resume_id": "VARCHAR(128)",
            "selected_resume_score": "INTEGER",
        },
        "applications": {
            "account_key": "VARCHAR(32) DEFAULT 'old'",
            "selected_resume_key": "VARCHAR(64)",
            "selected_resume_title": "VARCHAR(500)",
            "selected_resume_id": "VARCHAR(128)",
            "selected_resume_score": "INTEGER",
            "career_status": "VARCHAR(64) DEFAULT 'unknown'",
            "response_checked_at": "DATETIME",
            "manual_recovery_attempts": "INTEGER DEFAULT 0",
            "manual_recovery_last_at": "DATETIME",
        },
        "application_events": {
            "decision_snapshot_id": "INTEGER",
            "event_class": "VARCHAR(64)",
            "attribution": "VARCHAR(64) DEFAULT 'unknown'",
            "confidence": "VARCHAR(64) DEFAULT 'unknown'",
            "raw_ref": "TEXT",
        },
        "clean_shadow_assessments": {
            "learned_patterns_version": "VARCHAR(128)",
        },
    }

    hh_response_cache_added = False

    for table_name, columns in migration_columns.items():
        for column_name, ddl_type in columns.items():
            added = _add_missing_column(table_name, column_name, ddl_type)
            if (
                table_name == "vacancies"
                and column_name == "hh_response_checked_at"
                and added
            ):
                hh_response_cache_added = True

    _backfill_vacancy_sources()
    _backfill_application_account_keys()
    _backfill_application_career_statuses()

    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_strategy_memory_versions_calibration_run_id "
                "ON strategy_memory_versions(calibration_run_id) "
                "WHERE calibration_run_id IS NOT NULL"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_applications_career_status "
                "ON applications(career_status)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_applications_account_key "
                "ON applications(account_key)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_application_events_decision_snapshot_id "
                "ON application_events(decision_snapshot_id)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_application_events_attribution "
                "ON application_events(attribution)"
            )
        )

    if hh_response_cache_added:
        _backfill_initial_hh_response_cache()


init_db()
