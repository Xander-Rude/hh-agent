"""Regression checks for fair shadow prioritization and first OLD discoveries."""
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import clean_shadow
from app.db import Base, CleanLiveQueue, Evaluation, HhVacancyDiscovery, Vacancy
from app.shadow_queue_priority import prioritize_shadow_rows


class ShadowQueuePriorityTests(unittest.TestCase):
    @staticmethod
    def row(i, score=85, *, days=0):
        now = datetime.now(UTC).replace(tzinfo=None)
        return (
            SimpleNamespace(id=i, attempts=0),
            SimpleNamespace(id=i, found_at=now - timedelta(days=days)),
            SimpleNamespace(score=score),
        )

    def test_fresh_old_and_clean_have_fair_shares_and_backlog_progresses(self):
        now = datetime.now(UTC).replace(tzinfo=None)
        old = [self.row(i) for i in range(1, 12)]
        clean = [self.row(i, score=40) for i in range(12, 23)]
        backlog = [self.row(i, days=3) for i in range(23, 34)]
        discoveries = [
            *((i, "old", now) for i in range(1, 12)),
            *((i, "clean", now) for i in range(12, 23)),
        ]
        ranked = prioritize_shadow_rows(
            old + clean + backlog, discoveries, now=now,
        )
        first_twenty = [v.id for _, v, _ in ranked[:20]]
        self.assertEqual(len(first_twenty), len(set(first_twenty)))
        self.assertEqual(sum(i <= 11 for i in first_twenty), 8)
        self.assertEqual(sum(12 <= i <= 22 for i in first_twenty), 8)
        self.assertEqual(sum(i >= 23 for i in first_twenty), 4)
        self.assertEqual(len(ranked), 33)

    def test_shared_vacancy_is_scored_once(self):
        now = datetime.now(UTC).replace(tzinfo=None)
        rows = [self.row(i) for i in range(1, 6)]
        discoveries = [(1, "old", now), (1, "clean", now)]
        ranked = prioritize_shadow_rows(rows, discoveries, now=now)
        self.assertEqual(sorted(v.id for _, v, _ in ranked), [1, 2, 3, 4, 5])

    def test_failed_attempts_are_deferred_inside_lane(self):
        now = datetime.now(UTC).replace(tzinfo=None)
        fresh = self.row(1)
        retry = self.row(2)
        retry[0].attempts = 2
        ranked = prioritize_shadow_rows(
            [retry, fresh], [(1, "old", now), (2, "old", now)], now=now,
        )
        self.assertEqual([v.id for _, v, _ in ranked], [1, 2])

    def test_first_old_discovery_of_existing_shared_vacancy_is_eligible(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        with Session() as db:
            now = datetime.now(UTC).replace(tzinfo=None)
            vacancy = Vacancy(
                hh_id="shadow-old-shared", source="hh",
                external_id="shadow-old-shared", title="Senior IT PM",
                company="Example", description="Manage IT delivery",
                url="https://hh.ru/vacancy/shadow-old-shared",
                found_at=now - timedelta(days=12),
            )
            db.add(vacancy)
            db.flush()
            db.add(Evaluation(
                vacancy_id=vacancy.id, score=95, decision="apply",
                role_match=90, seniority_match=90, domain_match=90,
                responsibility_match=90, must_have_missing="[]",
                nice_to_have_missing="[]", strengths="[]", gaps="[]",
                red_flags="[]", summary="Fits", recommendation="Apply",
                cover_letter="Hello", model="test",
            ))
            db.add(HhVacancyDiscovery(
                vacancy_id=vacancy.id, account_key="old",
                discovery_source="search", first_seen_at=now,
                last_seen_at=now,
            ))
            db.commit()
            with patch.object(
                clean_shadow, "clean_queue_cutoff",
                return_value=now - timedelta(days=9),
            ):
                stats = clean_shadow._enqueue_old_auto_shadow_candidates(
                    db, "test-current-version",
                )
                self.assertEqual(stats["queued"], 1)
                queue = clean_shadow._live_queue_evaluations(db)
                self.assertEqual(len(queue), 1)
                self.assertEqual(queue[0][1].id, vacancy.id)
        engine.dispose()


    def test_pruning_removes_expired_rows_but_preserves_new_old_discovery(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        now = datetime.now(UTC).replace(tzinfo=None)
        with Session() as db:
            outdated = Vacancy(
                hh_id="expired-old", source="hh", external_id="expired-old",
                title="PM", company="Example", description="Example",
                url="https://hh.ru/vacancy/expired-old",
                found_at=now - timedelta(days=14),
            )
            resurfaced = Vacancy(
                hh_id="fresh-old", source="hh", external_id="fresh-old",
                title="PM", company="Example", description="Example",
                url="https://hh.ru/vacancy/fresh-old",
                found_at=now - timedelta(days=14),
            )
            db.add_all([outdated, resurfaced])
            db.flush()
            db.add_all([
                CleanLiveQueue(vacancy_id=outdated.id),
                CleanLiveQueue(vacancy_id=resurfaced.id),
                HhVacancyDiscovery(
                    vacancy_id=resurfaced.id,
                    account_key="old", discovery_source="search",
                    first_seen_at=now, last_seen_at=now,
                ),
            ])
            db.commit()
            with patch.object(
                clean_shadow, "clean_queue_cutoff",
                return_value=now - timedelta(days=9),
            ):
                self.assertEqual(clean_shadow._prune_expired_live_queue(db), 1)
            self.assertEqual(
                db.scalars(select(CleanLiveQueue.vacancy_id)).all(),
                [resurfaced.id],
            )
        engine.dispose()


    def test_later_discovery_source_does_not_refresh_old_account_age(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        now = datetime.now(UTC).replace(tzinfo=None)
        with Session() as db:
            vacancy = Vacancy(
                hh_id="old-vacancy-second-source",
                source="hh", external_id="old-vacancy-second-source",
                title="PM", company="Example", description="Example",
                url="https://hh.ru/vacancy/old-vacancy-second-source",
                found_at=now - timedelta(days=20),
            )
            db.add(vacancy)
            db.flush()
            db.add(Evaluation(
                vacancy_id=vacancy.id, score=91, decision="apply",
                role_match=90, seniority_match=90, domain_match=90,
                responsibility_match=90, must_have_missing="[]",
                nice_to_have_missing="[]", strengths="[]",
                gaps="[]", red_flags="[]", summary="Fits",
                recommendation="Apply", cover_letter="Test", model="test",
            ))
            db.add_all([
                HhVacancyDiscovery(
                    vacancy_id=vacancy.id,
                    account_key="old", discovery_source="recommendation",
                    first_seen_at=now - timedelta(days=20),
                    last_seen_at=now,
                ),
                HhVacancyDiscovery(
                    vacancy_id=vacancy.id,
                    account_key="old", discovery_source="search",
                    first_seen_at=now, last_seen_at=now,
                ),
            ])
            db.add(CleanLiveQueue(vacancy_id=vacancy.id))
            db.commit()
            with patch.object(
                clean_shadow, "clean_queue_cutoff",
                return_value=now - timedelta(days=9),
            ):
                self.assertEqual(clean_shadow._live_queue_evaluations(db), [])
                stats = clean_shadow._enqueue_old_auto_shadow_candidates(
                    db, "test",
                )
                self.assertEqual(stats["candidates"], 0)
                self.assertEqual(clean_shadow._prune_expired_live_queue(db), 1)
        engine.dispose()

    def test_priority_uses_earliest_seen_for_each_account(self):
        now = datetime.now(UTC).replace(tzinfo=None)
        was_known = self.row(1, days=20)
        truly_fresh = self.row(2)
        ranked = prioritize_shadow_rows(
            [was_known, truly_fresh],
            [
                (1, "old", now - timedelta(days=20)),
                (1, "old", now),
                (2, "clean", now),
            ],
            now=now,
        )
        self.assertEqual([v.id for _, v, _ in ranked], [2, 1])


if __name__ == "__main__":
    unittest.main()
