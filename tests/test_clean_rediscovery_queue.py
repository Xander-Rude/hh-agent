from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import hh_collect
from app.db import Base, CleanLiveQueue, Evaluation, HhVacancyDiscovery, Vacancy


@pytest.mark.parametrize(
    ("assessment_state", "expected_queued"),
    [
        ("current", False),
        ("outdated_legacy", True),
        ("missing", True),
        ("no_legacy", True),
    ],
)
def test_clean_rediscovery_queues_only_when_assessment_is_not_current(
    monkeypatch, assessment_state, expected_queued
):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        vacancy = Vacancy(
            hh_id=f"rediscovery-{assessment_state}",
            source="hh",
            external_id=f"rediscovery-{assessment_state}",
            title="Senior Project Manager",
            company="Example",
            url="https://hh.ru/vacancy/123",
            description="Own end-to-end IT delivery",
        )
        session.add(vacancy)
        session.flush()

        older = latest = None
        if assessment_state != "no_legacy":
            older = Evaluation(vacancy_id=vacancy.id, score=88, decision="apply")
            latest = Evaluation(vacancy_id=vacancy.id, score=90, decision="apply")
            session.add_all([older, latest])
            session.flush()

        if assessment_state == "current":
            assessment = SimpleNamespace(legacy_evaluation_id=latest.id)
        elif assessment_state == "outdated_legacy":
            assessment = SimpleNamespace(legacy_evaluation_id=older.id)
        else:
            assessment = None

        monkeypatch.setattr(
            hh_collect,
            "current_clean_assessment",
            lambda _session, _vacancy_id: assessment,
        )

        hh_collect.record_hh_discovery(
            session, vacancy, source_label="HH_RECOMMENDATION", account_key="clean"
        )
        hh_collect.record_hh_discovery(
            session, vacancy, source_label="HH_SEARCH", account_key="clean"
        )
        session.commit()

        discovered = session.scalars(
            select(HhVacancyDiscovery).where(
                HhVacancyDiscovery.vacancy_id == vacancy.id,
                HhVacancyDiscovery.account_key == "clean",
            )
        ).all()
        assert {row.discovery_source for row in discovered} == {"search", "recommendation"}

        queued = session.scalars(
            select(CleanLiveQueue).where(CleanLiveQueue.vacancy_id == vacancy.id)
        ).all()
        assert bool(queued) is expected_queued
        assert len(queued) <= 1
    finally:
        session.close()
        engine.dispose()
