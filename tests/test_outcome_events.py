import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.application_events as events
from app.db import (
    Application,
    ApplicationDecisionSnapshot,
    ApplicationEvent,
    Base,
    Vacancy,
)


class OutcomeEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session_patcher = patch.object(
            events,
            "SessionLocal",
            self.Session,
        )
        self.session_patcher.start()

    def tearDown(self) -> None:
        self.session_patcher.stop()
        self.engine.dispose()

    def _application(
        self,
        *,
        account_key: str = "clean",
        source: str = "hh",
        with_snapshot: bool = True,
    ) -> tuple[int, int | None]:
        session = self.Session()
        try:
            vacancy = Vacancy(
                hh_id="123456" if source == "hh" else f"{source}:123456",
                source=source,
                external_id="123456",
                title="Senior IT Project Manager",
                company="Example",
                url="https://example.test/vacancy/123456",
                description="Full-cycle IT delivery " * 20,
            )
            session.add(vacancy)
            session.flush()

            application = Application(
                vacancy_id=vacancy.id,
                status="applied",
                account_key=account_key,
                career_status="submitted",
            )
            session.add(application)
            session.flush()

            snapshot_id = None
            if with_snapshot:
                snapshot = ApplicationDecisionSnapshot(
                    application_id=application.id,
                    vacancy_id=vacancy.id,
                    account_key=account_key,
                    application_type=(
                        "fresh_clean"
                        if account_key == "clean"
                        else "old"
                    ),
                )
                session.add(snapshot)
                session.flush()
                snapshot_id = snapshot.id

            application_id = application.id
            session.commit()
            return application_id, snapshot_id
        finally:
            session.close()

    def _events(self, application_id: int) -> list[ApplicationEvent]:
        session = self.Session()
        try:
            return list(
                session.scalars(
                    select(ApplicationEvent)
                    .where(
                        ApplicationEvent.application_id
                        == application_id
                    )
                    .order_by(ApplicationEvent.id)
                )
            )
        finally:
            session.close()

    def test_outcome_binds_snapshot_and_derives_clean_attribution(self):
        application_id, snapshot_id = self._application()

        created = events.record_outcome_event(
            application_id,
            "viewed",
            source="hh_negotiations",
            confidence="platform_observed",
            raw_ref="hh-negotiations:clean:123456",
        )

        self.assertTrue(created)
        row = self._events(application_id)[0]
        self.assertEqual(row.event_type, "viewed")
        self.assertEqual(row.event_class, "platform_outcome")
        self.assertEqual(row.attribution, "hh_clean")
        self.assertEqual(row.confidence, "platform_observed")
        self.assertEqual(row.decision_snapshot_id, snapshot_id)

    def test_non_hh_platform_outcome_derives_career_site_attribution(self):
        application_id, _ = self._application(
            account_key="old",
            source="yandex",
        )

        events.record_outcome_event(
            application_id,
            "viewed",
            source="career_collector",
            confidence="platform_observed",
        )

        row = self._events(application_id)[0]
        self.assertEqual(row.attribution, "career_site")

    def test_unattributed_human_response_does_not_claim_clean_credit(self):
        application_id, _ = self._application()

        events.record_outcome_event(
            application_id,
            "recruiter_message",
            source="manual",
            confidence="user_confirmed",
        )

        row = self._events(application_id)[0]
        self.assertEqual(row.attribution, "unknown")

    def test_explicit_assisted_attribution_overrides_hh_clean(self):
        application_id, _ = self._application()

        events.record_outcome_event(
            application_id,
            "recruiter_message",
            source="manual",
            attribution="assisted_multi_touch",
            confidence="user_confirmed",
        )

        row = self._events(application_id)[0]
        self.assertEqual(row.attribution, "assisted_multi_touch")

    def test_identical_consecutive_outcome_is_deduped(self):
        application_id, _ = self._application()
        kwargs = {
            "source": "hh_negotiations",
            "confidence": "platform_observed",
            "raw_ref": "hh-negotiations:clean:123456",
        }

        self.assertTrue(
            events.record_outcome_event(
                application_id,
                "viewed",
                **kwargs,
            )
        )
        self.assertFalse(
            events.record_outcome_event(
                application_id,
                "viewed",
                **kwargs,
            )
        )
        self.assertEqual(len(self._events(application_id)), 1)

    def test_workflow_invitation_cannot_overwrite_human_stage(self):
        self.assertFalse(
            events.career_transition_allowed(
                "interview_completed",
                "workflow_invited",
            )
        )

    def test_legacy_interview_alias_records_canonical_event(self):
        application_id, _ = self._application()

        changed = events.update_career_status(
            application_id,
            "interview_done",
            source="manual",
            confidence="user_confirmed",
        )

        self.assertTrue(changed)
        row = self._events(application_id)[0]
        self.assertEqual(row.event_type, "interview_completed")
        self.assertEqual(row.event_class, "human_stage")

    def test_materialized_update_can_suppress_event(self):
        application_id, _ = self._application()

        changed = events.update_career_status(
            application_id,
            "viewed",
            source="repair",
            emit_event=False,
        )

        self.assertTrue(changed)
        self.assertEqual(self._events(application_id), [])

    def test_no_response_7d_is_recorded_at_exact_milestone(self):
        now = datetime(2026, 9, 23, 12, 0, 0)
        application_id, _ = self._application()

        session = self.Session()
        try:
            application = session.get(Application, application_id)
            application.applied_at = now - timedelta(days=8)
            session.commit()
        finally:
            session.close()

        counts = events.record_due_no_response_events(
            account_keys={"clean"},
            now=now,
        )

        self.assertEqual(counts["no_response_7d"], 1)
        self.assertEqual(counts["no_response_30d"], 0)
        row = self._events(application_id)[0]
        self.assertEqual(row.event_type, "no_response_7d")
        self.assertEqual(
            row.observed_at,
            now - timedelta(days=1),
        )
        self.assertEqual(row.attribution, "hh_clean")
        self.assertEqual(row.confidence, "derived")

    def test_response_before_milestone_suppresses_no_response(self):
        now = datetime(2026, 9, 23, 12, 0, 0)
        application_id, _ = self._application()

        session = self.Session()
        try:
            application = session.get(Application, application_id)
            application.applied_at = now - timedelta(days=10)
            session.commit()
        finally:
            session.close()

        events.record_outcome_event(
            application_id,
            "viewed",
            source="hh_negotiations",
            confidence="platform_observed",
            observed_at=now - timedelta(days=6),
        )

        counts = events.record_due_no_response_events(
            account_keys={"clean"},
            now=now,
        )

        self.assertEqual(counts["no_response_7d"], 0)
        self.assertFalse(
            any(
                row.event_type == "no_response_7d"
                for row in self._events(application_id)
            )
        )

    def test_late_response_preserves_true_7d_no_response_history(self):
        now = datetime(2026, 9, 23, 12, 0, 0)
        application_id, _ = self._application()

        session = self.Session()
        try:
            application = session.get(Application, application_id)
            application.applied_at = now - timedelta(days=20)
            session.commit()
        finally:
            session.close()

        events.record_outcome_event(
            application_id,
            "recruiter_message",
            source="manual",
            attribution="hh_clean",
            confidence="user_confirmed",
            observed_at=now - timedelta(days=10),
        )

        counts = events.record_due_no_response_events(
            account_keys={"clean"},
            now=now,
        )

        self.assertEqual(counts["no_response_7d"], 1)
        rows = self._events(application_id)
        milestone = next(
            row for row in rows if row.event_type == "no_response_7d"
        )
        self.assertEqual(
            milestone.observed_at,
            now - timedelta(days=13),
        )

        repeated = events.record_due_no_response_events(
            account_keys={"clean"},
            now=now,
        )
        self.assertEqual(repeated["no_response_7d"], 0)

    def test_unknown_outcome_event_is_rejected(self):
        application_id, _ = self._application()

        with self.assertRaisesRegex(ValueError, "Unsupported outcome event"):
            events.record_outcome_event(
                application_id,
                "made_up_event",
                source="test",
            )


if __name__ == "__main__":
    unittest.main()
