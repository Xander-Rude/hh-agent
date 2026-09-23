import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.strategy_memory as strategy_memory
import app.strategy_memory_refresh as refresh
from app.calibration import CalibrationLLMReport
from app.db import (
    Application,
    Base,
    CalibrationRun,
    StrategyMemoryVersion,
    Vacancy,
)


class StrategyMemoryRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.memory_patch = patch.object(
            strategy_memory,
            "SessionLocal",
            self.Session,
        )
        self.refresh_patch = patch.object(
            refresh,
            "SessionLocal",
            self.Session,
        )
        self.memory_patch.start()
        self.refresh_patch.start()

    def tearDown(self) -> None:
        self.refresh_patch.stop()
        self.memory_patch.stop()
        self.engine.dispose()

    def _baseline(self):
        return strategy_memory.create_memory_version(
            candidate_profile={
                "schema_version": "candidate-profile-v1",
                "name": "baseline",
            },
            target_strategy={
                "schema_version": "target-strategy-v1",
                "objective": "interviews_per_clean_apply",
            },
            source="baseline",
            activate=True,
        )

    def _applications(self, count: int) -> list[int]:
        session = self.Session()
        try:
            ids = []
            for index in range(count):
                vacancy = Vacancy(
                    hh_id=f"test-{index}",
                    source="hh",
                    external_id=f"test-{index}",
                    title="Project Manager",
                    company=f"Example {index}",
                    url=f"https://hh.test/vacancy/{index}",
                    description="IT project delivery " * 20,
                )
                session.add(vacancy)
                session.flush()
                application = Application(
                    vacancy_id=vacancy.id,
                    status="applied",
                    account_key="clean",
                )
                session.add(application)
                session.flush()
                ids.append(application.id)
            session.commit()
            return ids
        finally:
            session.close()

    def _run(
        self,
        *,
        status: str = "completed",
        patterns: list[dict] | None = None,
        suffix: str = "a",
    ) -> CalibrationRun:
        report = CalibrationLLMReport.model_validate(
            {
                "summary": "test",
                "pattern_candidates": patterns or [],
                "data_limitations": [],
                "follow_up_checks": [],
            }
        )
        session = self.Session()
        try:
            run = CalibrationRun(
                status=status,
                scope="hh_clean",
                event_high_watermark=10,
                snapshot_high_watermark=10,
                sample_count=10,
                mature_sample_count=10,
                new_mature_sample_count=5,
                dataset_hash=f"hash-{suffix}",
                metrics_json="{}",
                case_summaries_json="[]",
                llm_report_json=report.model_dump_json(),
                prompt_version="calibration-prompt-v1",
                llm_model="test-llm",
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            run_id = run.id
        finally:
            session.close()

        session = self.Session()
        try:
            return session.get(CalibrationRun, run_id)
        finally:
            session.close()

    @staticmethod
    def _pattern(
        support: list[int],
        *,
        counterexamples: list[int] | None = None,
        confidence: str = "medium",
        title: str = "Visible lifecycle evidence",
    ) -> dict:
        return {
            "title": title,
            "hypothesis": (
                "Visible end-to-end project lifecycle evidence is associated "
                "with stronger outcomes."
            ),
            "observed_signal": "More mature positive outcomes in similar cases.",
            "supporting_application_ids": support,
            "counterexample_application_ids": counterexamples or [],
            "confidence": confidence,
            "proposed_action": "candidate_for_evidence_rule_review",
            "caution": "Historical association only; do not change score directly.",
        }

    def test_completed_run_proposes_inactive_child_version(self) -> None:
        baseline = self._baseline()
        app_ids = self._applications(4)
        run = self._run(
            patterns=[
                self._pattern(
                    app_ids[:3],
                    counterexamples=[app_ids[3]],
                    confidence="high",
                )
            ]
        )

        result = refresh.propose_memory_refresh(
            calibration_run_id=run.id
        )

        self.assertEqual(result.status, "proposed")
        self.assertFalse(result.activated)
        self.assertEqual(result.accepted_pattern_count, 1)
        self.assertEqual(result.rejected_pattern_count, 0)

        active = strategy_memory.get_active_memory()
        self.assertEqual(active["version"]["id"], baseline.id)

        proposed = strategy_memory.get_memory_version(
            result.memory_version_id
        )
        self.assertEqual(
            proposed["version"]["parent_version_id"],
            baseline.id,
        )
        self.assertEqual(
            proposed["version"]["calibration_run_id"],
            run.id,
        )
        self.assertEqual(
            proposed["candidate_profile"],
            active["candidate_profile"],
        )
        self.assertEqual(
            proposed["target_strategy"],
            active["target_strategy"],
        )
        self.assertEqual(len(proposed["learned_patterns"]), 1)
        pattern = proposed["learned_patterns"][0]
        self.assertEqual(pattern["pattern_type"], "evidence")
        self.assertEqual(pattern["confidence_score"], 90)
        self.assertEqual(pattern["support_count"], 3)
        self.assertEqual(
            pattern["source_calibration_run_id"],
            run.id,
        )
        self.assertEqual(
            pattern["evidence_application_ids"],
            sorted(app_ids[:3]),
        )

    def test_same_calibration_run_materializes_at_most_once(self) -> None:
        self._baseline()
        app_ids = self._applications(3)
        run = self._run(
            patterns=[self._pattern(app_ids)],
        )

        first = refresh.propose_memory_refresh(
            calibration_run_id=run.id
        )
        second = refresh.propose_memory_refresh(
            calibration_run_id=run.id
        )

        self.assertEqual(first.status, "proposed")
        self.assertEqual(second.status, "already_materialized")
        self.assertEqual(
            second.memory_version_id,
            first.memory_version_id,
        )

        session = self.Session()
        try:
            versions = (
                session.query(StrategyMemoryVersion)
                .filter(
                    StrategyMemoryVersion.calibration_run_id == run.id
                )
                .count()
            )
            self.assertEqual(versions, 1)
        finally:
            session.close()

    def test_low_confidence_pattern_is_not_materialized(self) -> None:
        baseline = self._baseline()
        app_ids = self._applications(3)
        run = self._run(
            patterns=[
                self._pattern(
                    app_ids,
                    confidence="low",
                )
            ],
            suffix="low",
        )

        result = refresh.propose_memory_refresh(
            calibration_run_id=run.id
        )

        self.assertEqual(result.status, "no_safe_patterns")
        self.assertEqual(result.accepted_pattern_count, 0)
        self.assertEqual(result.rejected_pattern_count, 1)
        self.assertEqual(
            strategy_memory.get_active_memory()["version"]["id"],
            baseline.id,
        )
        self.assertEqual(len(strategy_memory.list_memory_versions()), 1)

    def test_counterexamples_must_not_match_or_exceed_support(self) -> None:
        self._baseline()
        app_ids = self._applications(6)
        run = self._run(
            patterns=[
                self._pattern(
                    app_ids[:3],
                    counterexamples=app_ids[3:6],
                    confidence="high",
                )
            ],
            suffix="counter",
        )

        result = refresh.propose_memory_refresh(
            calibration_run_id=run.id
        )

        self.assertEqual(result.status, "no_safe_patterns")
        self.assertEqual(result.rejected_pattern_count, 1)
        self.assertEqual(len(strategy_memory.list_memory_versions()), 1)

    def test_non_completed_run_is_blocked(self) -> None:
        self._baseline()
        app_ids = self._applications(3)
        run = self._run(
            status="insufficient_data",
            patterns=[self._pattern(app_ids)],
            suffix="insufficient",
        )

        result = refresh.propose_memory_refresh(
            calibration_run_id=run.id
        )

        self.assertEqual(result.status, "blocked")
        self.assertIn("not completed", result.reason)
        self.assertEqual(len(strategy_memory.list_memory_versions()), 1)


if __name__ == "__main__":
    unittest.main()
