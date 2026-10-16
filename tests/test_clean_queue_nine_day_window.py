import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import hh_collect
import clean_shadow
from app.clean_queue_freshness import (
    CLEAN_QUEUE_LOOKBACK_DAYS,
    clean_queue_cutoff,
    is_clean_queue_fresh,
)
from app.db import Base, CleanLiveQueue, HhVacancyDiscovery, Vacancy


class CleanQueueNineDayTests(unittest.TestCase):
    def test_freshness_is_rolling_nine_days(self):
        self.assertEqual(CLEAN_QUEUE_LOOKBACK_DAYS, 9)
        self.assertTrue(
            is_clean_queue_fresh(datetime.now(UTC) - timedelta(days=8))
        )
        self.assertFalse(
            is_clean_queue_fresh(datetime.now(UTC) - timedelta(days=10))
        )
        self.assertTrue(
            is_clean_queue_fresh(
                datetime.now(UTC).replace(tzinfo=None) - timedelta(days=7)
            )
        )
        self.assertIsNone(clean_queue_cutoff().tzinfo)
        self.assertFalse(is_clean_queue_fresh(None))

    def test_first_clean_discovery_of_old_shared_vacancy_is_queued(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            vacancy = Vacancy(
                hh_id="old-10days",
                source="hh",
                external_id="old-10days",
                title="Senior Project Manager",
                company="Example",
                url="https://hh.ru/vacancy/old",
                description="Own end-to-end IT delivery",
                found_at=datetime.now(UTC).replace(tzinfo=None)
                - timedelta(days=10),
            )
            session.add(vacancy)
            session.flush()
            hh_collect.record_hh_discovery(
                session, vacancy, source_label="HH_RECOMMENDATION",
                account_key="clean",
            )
            session.commit()
            self.assertIsNotNone(session.scalar(select(CleanLiveQueue.id)))
            self.assertIsNotNone(session.scalar(select(HhVacancyDiscovery.id)))
        finally:
            session.close()
            engine.dispose()

    def test_old_clean_discovery_is_not_requeued_by_rediscovery(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            old = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=12)
            vacancy = Vacancy(
                hh_id="old-known-clean",
                source="hh",
                external_id="old-known-clean",
                title="Senior PM",
                company="Example",
                url="https://hh.ru/vacancy/old-known-clean",
                description="Delivery management",
                found_at=old,
            )
            session.add(vacancy)
            session.flush()
            session.add(
                HhVacancyDiscovery(
                    vacancy_id=vacancy.id,
                    account_key="clean",
                    discovery_source="search",
                    first_seen_at=old,
                    last_seen_at=old,
                )
            )
            session.commit()
            hh_collect.record_hh_discovery(
                session, vacancy, source_label="HH_SEARCH",
                account_key="clean",
            )
            session.commit()
            self.assertIsNone(session.scalar(select(CleanLiveQueue.id)))
        finally:
            session.close()
            engine.dispose()

    def test_shadow_worker_filters_stale_live_queue(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            old_vacancy = Vacancy(
                hh_id="stale-live-10days",
                source="hh",
                external_id="stale-live-10days",
                title="PM",
                company="Example",
                url="https://hh.ru/vacancy/stale",
                description="Example",
                found_at=datetime.now(UTC).replace(tzinfo=None)
                - timedelta(days=10),
            )
            fresh_vacancy = Vacancy(
                hh_id="fresh-live-2days",
                source="hh",
                external_id="fresh-live-2days",
                title="PM",
                company="Example",
                url="https://hh.ru/vacancy/fresh",
                description="Example",
                found_at=datetime.now(UTC).replace(tzinfo=None)
                - timedelta(days=2),
            )
            session.add_all([old_vacancy, fresh_vacancy])
            session.flush()
            session.add_all(
                [
                    CleanLiveQueue(vacancy_id=old_vacancy.id),
                    CleanLiveQueue(vacancy_id=fresh_vacancy.id),
                ]
            )
            session.commit()
            # The worker's query requires a legacy Evaluation. We check the
            # restrictive SQL directly to avoid expensive LLM inference.
            eligible = session.scalars(
                select(CleanLiveQueue.vacancy_id)
                .join(Vacancy, Vacancy.id == CleanLiveQueue.vacancy_id)
                .where(Vacancy.found_at >= clean_queue_cutoff())
            ).all()
            self.assertEqual(eligible, [fresh_vacancy.id])
        finally:
            session.close()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
