import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.strategy_memory as strategy_memory
from app.db import (
    Application,
    ApplicationDecisionSnapshot,
    ApplicationEvent,
    Base,
    StrategyBadExample,
    StrategyCandidateProfile,
    StrategyGoodExample,
    StrategyLearnedPattern,
    StrategyMemoryActivation,
    StrategyMemoryState,
    StrategyMemoryVersion,
    StrategyTargetStrategy,
    Vacancy,
)


class StrategyMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.patch = patch.object(
            strategy_memory,
            "SessionLocal",
            self.Session,
        )
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.engine.dispose()

    def _candidate(self, name: str = "baseline") -> dict:
        return {
            "schema_version": "candidate-profile-v1",
            "name": name,
            "evidence_visibility": "recruiter_visible",
        }

    def _strategy(self, objective: str = "interviews_per_clean_apply") -> dict:
        return {
            "schema_version": "target-strategy-v1",
            "objective": objective,
            "account": "clean",
        }

    def _application_refs(self) -> tuple[int, int, int]:
        session = self.Session()
        try:
            vacancy = Vacancy(
                hh_id="123456",
                source="hh",
                external_id="123456",
                title="Senior IT Project Manager",
                company="Example",
                url="https://hh.test/vacancy/123456",
                description="Full-cycle IT delivery " * 20,
            )
            session.add(vacancy)
            session.flush()

            application = Application(
                vacancy_id=vacancy.id,
                status="applied",
                account_key="clean",
                career_status="interview_completed",
            )
            session.add(application)
            session.flush()

            snapshot = ApplicationDecisionSnapshot(
                application_id=application.id,
                vacancy_id=vacancy.id,
                account_key="clean",
                application_type="fresh_clean",
            )
            session.add(snapshot)
            session.flush()

            event = ApplicationEvent(
                application_id=application.id,
                decision_snapshot_id=snapshot.id,
                event_type="interview_completed",
                event_class="human_stage",
                source="manual",
                attribution="hh_clean",
                confidence="user_confirmed",
            )
            session.add(event)
            session.flush()

            result = (
                application.id,
                snapshot.id,
                event.id,
            )
            session.commit()
            return result
        finally:
            session.close()

    def test_create_activate_and_read_separate_components(self) -> None:
        application_id, snapshot_id, event_id = self._application_refs()

        version = strategy_memory.create_memory_version(
            candidate_profile=self._candidate(),
            target_strategy=self._strategy(),
            learned_patterns=[
                {
                    "pattern_key": "project-core-positive",
                    "pattern_type": "positive",
                    "statement": "PROJECT_CORE can convert when evidence is direct.",
                    "support_count": 3,
                    "confidence_score": 80,
                    "evidence_application_ids": [application_id],
                }
            ],
            good_examples=[
                {
                    "application_id": application_id,
                    "decision_snapshot_id": snapshot_id,
                    "outcome_event_id": event_id,
                    "label": "Interview completed",
                }
            ],
            bad_examples=[],
            candidate_profile_source_ref="data/clean_resume_visible.txt",
            candidate_profile_source_hash="abc123",
            source="baseline",
            activate=True,
        )

        self.assertEqual(version.version_number, 1)
        self.assertIsNone(version.parent_version_id)

        active = strategy_memory.get_active_memory()
        self.assertIsNotNone(active)
        self.assertEqual(active["version"]["id"], version.id)
        self.assertEqual(
            active["candidate_profile"]["name"],
            "baseline",
        )
        self.assertEqual(
            active["target_strategy"]["objective"],
            "interviews_per_clean_apply",
        )
        self.assertEqual(len(active["learned_patterns"]), 1)
        self.assertEqual(len(active["good_examples"]), 1)
        self.assertEqual(active["bad_examples"], [])

        session = self.Session()
        try:
            self.assertEqual(
                session.query(StrategyCandidateProfile).count(),
                1,
            )
            self.assertEqual(
                session.query(StrategyTargetStrategy).count(),
                1,
            )
            self.assertEqual(
                session.query(StrategyLearnedPattern).count(),
                1,
            )
            self.assertEqual(
                session.query(StrategyGoodExample).count(),
                1,
            )
            self.assertEqual(
                session.query(StrategyBadExample).count(),
                0,
            )
            state = session.get(StrategyMemoryState, 1)
            self.assertEqual(state.active_version_id, version.id)
        finally:
            session.close()

    def test_new_version_inherits_active_parent_and_rollback_only_moves_pointer(self) -> None:
        first = strategy_memory.create_memory_version(
            candidate_profile=self._candidate("v1"),
            target_strategy=self._strategy("objective-v1"),
            activate=True,
        )
        second = strategy_memory.create_memory_version(
            candidate_profile=self._candidate("v2"),
            target_strategy=self._strategy("objective-v2"),
            activate=True,
        )

        self.assertEqual(second.version_number, 2)
        self.assertEqual(second.parent_version_id, first.id)

        changed = strategy_memory.rollback_memory_version(
            first.id,
            note="regression detected",
        )
        self.assertTrue(changed)

        active = strategy_memory.get_active_memory()
        self.assertEqual(active["version"]["id"], first.id)

        session = self.Session()
        try:
            self.assertEqual(
                session.query(StrategyMemoryVersion).count(),
                2,
            )
            activations = list(
                session.scalars(
                    select(StrategyMemoryActivation).order_by(
                        StrategyMemoryActivation.id
                    )
                )
            )
            self.assertEqual(len(activations), 3)
            self.assertEqual(
                activations[-1].previous_version_id,
                second.id,
            )
            self.assertEqual(
                activations[-1].version_id,
                first.id,
            )
            self.assertEqual(
                activations[-1].reason,
                "rollback",
            )
        finally:
            session.close()

    def test_identical_content_is_idempotent(self) -> None:
        kwargs = {
            "candidate_profile": self._candidate(),
            "target_strategy": self._strategy(),
        }
        first = strategy_memory.create_memory_version(**kwargs)
        second = strategy_memory.create_memory_version(**kwargs)

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.version_number, second.version_number)

        session = self.Session()
        try:
            self.assertEqual(
                session.query(StrategyMemoryVersion).count(),
                1,
            )
        finally:
            session.close()

    def test_invalid_pattern_confidence_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "confidence_score must be between 0 and 100",
        ):
            strategy_memory.create_memory_version(
                candidate_profile=self._candidate(),
                target_strategy=self._strategy(),
                learned_patterns=[
                    {
                        "pattern_key": "bad-confidence",
                        "pattern_type": "positive",
                        "statement": "Nope",
                        "confidence_score": 101,
                    }
                ],
            )

    def test_example_without_provenance_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "strategy example requires",
        ):
            strategy_memory.create_memory_version(
                candidate_profile=self._candidate(),
                target_strategy=self._strategy(),
                good_examples=[
                    {
                        "label": "floating anecdote",
                        "rationale": "No application evidence",
                    }
                ],
            )

    def test_example_cross_application_mismatch_is_rejected(self) -> None:
        application_id, snapshot_id, event_id = self._application_refs()

        session = self.Session()
        try:
            vacancy = Vacancy(
                hh_id="654321",
                source="hh",
                external_id="654321",
                title="Project Manager",
                company="Other",
                url="https://hh.test/vacancy/654321",
                description="Project delivery " * 20,
            )
            session.add(vacancy)
            session.flush()
            other = Application(
                vacancy_id=vacancy.id,
                status="applied",
                account_key="clean",
            )
            session.add(other)
            session.commit()
            other_id = other.id
        finally:
            session.close()

        with self.assertRaisesRegex(
            ValueError,
            "decision snapshot does not belong to application",
        ):
            strategy_memory.create_memory_version(
                candidate_profile=self._candidate(),
                target_strategy=self._strategy(),
                bad_examples=[
                    {
                        "application_id": other_id,
                        "decision_snapshot_id": snapshot_id,
                        "outcome_event_id": event_id,
                    }
                ],
            )

    def test_activation_of_already_active_version_is_idempotent(self) -> None:
        version = strategy_memory.create_memory_version(
            candidate_profile=self._candidate(),
            target_strategy=self._strategy(),
            activate=True,
        )

        self.assertFalse(
            strategy_memory.activate_memory_version(version.id)
        )

        session = self.Session()
        try:
            self.assertEqual(
                session.query(StrategyMemoryActivation).count(),
                1,
            )
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
