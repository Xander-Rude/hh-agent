from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import hh_collect
from app.db import (
    Base,
    CleanLiveQueue,
    HhVacancyDiscovery,
    Vacancy,
)


ROOT = Path(__file__).resolve().parents[1]


def test_default_collector_uses_old_profile() -> None:
    assert hh_collect.COLLECT_ACCOUNT_KEY == "old"
    assert Path(hh_collect.PROFILE_DIR).name == "browser-profile"


def test_application_history_clause_is_account_scoped() -> None:
    clean_clause = str(hh_collect._application_account_clause("clean"))
    old_clause = str(hh_collect._application_account_clause("old"))

    assert "applications.account_key" in clean_clause
    assert "applications.account_key" in old_clause
    assert "IS NULL" in old_clause


def test_clean_profile_and_search_are_configurable_per_process() -> None:
    source = (ROOT / "hh_collect.py").read_text(encoding="utf-8")

    assert 'os.getenv("HH_COLLECT_ACCOUNT")' in source
    assert "get_account(COLLECT_ACCOUNT_KEY)" in source
    assert "COLLECT_ACCOUNT.profile_dir" in source
    assert "HH_ALWAYS_RUN_TARGET_SEARCH" in source
    assert "ALWAYS_RUN_TARGET_SEARCH" in source


def test_existing_clean_vacancy_does_not_reuse_old_response_cache() -> None:
    source = (ROOT / "hh_collect.py").read_text(encoding="utf-8")

    assert 'COLLECT_ACCOUNT_KEY == "old"' in source
    assert "account_key=COLLECT_ACCOUNT_KEY" in source


def test_discovery_is_scoped_by_account_and_source() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        vacancy = Vacancy(
            hh_id="hh-discovery-test",
            source="hh",
            external_id="hh-discovery-test",
            title="Project Manager",
            company="Example",
            url="https://hh.ru/vacancy/1",
            description="IT project delivery",
        )
        session.add(vacancy)
        session.flush()

        hh_collect.record_hh_discovery(
            session,
            vacancy,
            source_label="HH_RECOMMENDATION",
            account_key="clean",
        )
        hh_collect.record_hh_discovery(
            session,
            vacancy,
            source_label="HH_SEARCH",
            account_key="old",
        )
        session.commit()

        discoveries = session.scalars(
            select(HhVacancyDiscovery)
            .order_by(
                HhVacancyDiscovery.account_key,
                HhVacancyDiscovery.discovery_source,
            )
        ).all()
        assert [
            (row.account_key, row.discovery_source)
            for row in discoveries
        ] == [
            ("clean", "recommendation"),
            ("old", "search"),
        ]

        queue_rows = session.scalars(select(CleanLiveQueue)).all()
        assert len(queue_rows) == 1
        assert queue_rows[0].vacancy_id == vacancy.id
    finally:
        session.close()
        engine.dispose()


def test_old_discovery_does_not_create_clean_queue() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        vacancy = Vacancy(
            hh_id="hh-old-only",
            source="hh",
            external_id="hh-old-only",
            title="Project Manager",
            company="Example",
            url="https://hh.ru/vacancy/2",
            description="IT project delivery",
        )
        session.add(vacancy)
        session.flush()

        hh_collect.record_hh_discovery(
            session,
            vacancy,
            source_label="HH_SEARCH",
            account_key="old",
        )
        session.commit()

        assert session.scalar(select(CleanLiveQueue.id)) is None
    finally:
        session.close()
        engine.dispose()
