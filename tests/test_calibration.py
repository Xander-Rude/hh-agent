import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.calibration as calibration
from app.db import (
    Application,
    ApplicationDecisionSnapshot,
    ApplicationEvent,
    Base,
    CalibrationRun,
    Vacancy,
)


class FakeLLM:
    model = "fake-calibration-model"

    def __init__(self, payload: dict | None = None) -> None:
        self.calls = 0
        self.payload = payload

    def chat(self, messages, format_schema=None):
        self.calls += 1
        if self.payload is None:
            raise AssertionError("LLM must not be called")
        return SimpleNamespace(
            message=SimpleNamespace(
                content=json.dumps(
                    self.payload,
                    ensure_ascii=False,
                )
            )
        )


class CalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session_patch = patch.object(
            calibration,
            "SessionLocal",
            self.Session,
        )
        self.hunt_patch = patch.object(
            calibration,
            "_sent_targeted_outreach",
            return_value=False,
        )
        self.session_patch.start()
        self.hunt_patch.start()
        self.base_time = datetime(2026, 9, 1, 12, 0, 0)

    def tearDown(self) -> None:
        self.hunt_patch.stop()
        self.session_patch.stop()
        self.engine.dispose()

    def _application(
        self,
        *,
        events: list[tuple[str, str, str]] | None = None,
        account_key: str = "clean",
        application_type: str = "fresh_clean",
        applied: bool = True,
        fit: int = 88,
        invite: int = 86,
        title: str = "Senior IT Project Manager",
    ) -> tuple[int, int]:
        session = self.Session()
        try:
            index = (
                session.scalar(select(Vacancy.id).order_by(Vacancy.id.desc()))
                or 0
            ) + 1
            vacancy = Vacancy(
                hh_id=str(900000 + index),
                source="hh",
                external_id=str(900000 + index),
                title=title,
                company=f"Company {index}",
                url=f"https://hh.ru/vacancy/{900000 + index}",
                description="Full-cycle IT delivery " * 20,
            )
            session.add(vacancy)
            session.flush()

            application = Application(
                vacancy_id=vacancy.id,
                status="applied" if applied else "notified",
                account_key=account_key,
                applied_at=self.base_time if applied else None,
                career_status="submitted" if applied else "unknown",
            )
            session.add(application)
            session.flush()

            snapshot = ApplicationDecisionSnapshot(
                application_id=application.id,
                vacancy_id=vacancy.id,
                account_key=account_key,
                application_type=application_type,
                routing_class="CLEAN_STRONG",
                route_reason_codes='["FIT_STRONG","INVITE_STRONG"]',
                fit_score=fit,
                invite_score=invite,
                hard_stops="[]",
                role_family="PROJECT_CORE",
                role_confidence_pct=95,
                candidate_profile_version="candidate-v1",
                recruiter_resume_version="resume-v1",
                prompt_version="clean-shadow-prompt-v3",
                scoring_version="clean-shadow-score-v3",
                gate_version="clean-shadow-gates-v3",
                routing_version="clean-shadow-routing-v1",
                company_policy_version="clean-shadow-company-v1",
                vacancy_snapshot=json.dumps(
                    {
                        "title": title,
                        "company": vacancy.company,
                    },
                    ensure_ascii=False,
                ),
            )
            session.add(snapshot)
            session.flush()

            for offset, item in enumerate(events or [], start=1):
                event_type, attribution, confidence = item
                session.add(
                    ApplicationEvent(
                        application_id=application.id,
                        decision_snapshot_id=snapshot.id,
                        event_type=event_type,
                        event_class="test",
                        source="test",
                        attribution=attribution,
                        confidence=confidence,
                        observed_at=(
                            self.base_time + timedelta(days=offset)
                        ),
                    )
                )

            application_id = application.id
            snapshot_id = snapshot.id
            session.commit()
            return application_id, snapshot_id
        finally:
            session.close()

    def _dataset(self):
        session = self.Session()
        try:
            return calibration.build_calibration_dataset(session)
        finally:
            session.close()

    def test_workflow_invited_is_not_mature_outcome(self) -> None:
        application_id, _ = self._application(
            events=[
                (
                    "workflow_invited",
                    "hh_clean",
                    "platform_observed",
                )
            ]
        )

        dataset = self._dataset()
        case = next(
            item
            for item in dataset.cases
            if item["application_id"] == application_id
        )

        self.assertFalse(case["mature"])
        self.assertFalse(case["eligible_clean_learning"])
        self.assertEqual(
            dataset.metrics["eligible_clean_mature_cases"],
            0,
        )

    def test_rejection_does_not_erase_completed_interview(self) -> None:
        application_id, _ = self._application(
            events=[
                (
                    "interview_completed",
                    "hh_clean",
                    "user_confirmed",
                ),
                (
                    "rejected",
                    "hh_clean",
                    "platform_observed",
                ),
            ]
        )

        dataset = self._dataset()
        case = next(
            item
            for item in dataset.cases
            if item["application_id"] == application_id
        )

        self.assertEqual(
            case["best_positive_stage"],
            "interview_completed",
        )
        self.assertEqual(case["terminal_outcome"], "rejected")
        self.assertTrue(case["eligible_clean_learning"])
        self.assertEqual(
            dataset.metrics["hh_clean_interview_completed"],
            1,
        )
        self.assertEqual(
            dataset.metrics["interviews_per_clean_application"],
            1.0,
        )

    def test_unknown_human_attribution_is_not_clean_learning(self) -> None:
        self._application(
            events=[
                (
                    "interview_completed",
                    "unknown",
                    "user_confirmed",
                )
            ]
        )

        dataset = self._dataset()
        case = dataset.cases[0]

        self.assertTrue(case["attribution_uncertain"])
        self.assertFalse(case["eligible_clean_learning"])
        self.assertEqual(
            dataset.metrics["hh_clean_pure_applied"],
            1,
        )
        self.assertEqual(
            dataset.metrics["hh_clean_interview_completed"],
            0,
        )

    def test_backfill_is_excluded_from_clean_learning(self) -> None:
        self._application(
            application_type="clean_backfill",
            events=[
                (
                    "recruiter_message",
                    "hh_clean",
                    "user_confirmed",
                )
            ],
        )

        case = self._dataset().cases[0]
        self.assertEqual(case["cohort"], "clean_backfill")
        self.assertFalse(case["eligible_clean_learning"])

    def test_already_applied_is_not_new_transport_apply(self) -> None:
        self._application(
            events=[
                (
                    "already_applied",
                    "hh_clean",
                    "platform_observed",
                ),
                (
                    "rejected",
                    "hh_clean",
                    "platform_observed",
                ),
            ]
        )

        case = self._dataset().cases[0]
        self.assertTrue(case["already_applied"])
        self.assertFalse(case["transport_applied"])
        self.assertFalse(case["eligible_clean_learning"])
        self.assertEqual(
            self._dataset().metrics["hh_clean_pure_applied"],
            0,
        )

    def test_targeted_outreach_moves_application_to_assisted_cohort(self) -> None:
        self.hunt_patch.stop()
        with patch.object(
            calibration,
            "_sent_targeted_outreach",
            return_value=True,
        ):
            self._application(
                events=[
                    (
                        "recruiter_message",
                        "assisted_multi_touch",
                        "user_confirmed",
                    )
                ]
            )
            case = self._dataset().cases[0]
        self.hunt_patch.start()

        self.assertEqual(case["cohort"], "assisted_multi_touch")
        self.assertFalse(case["eligible_clean_learning"])

    def test_unknown_targeted_hunt_lookup_excludes_pure_clean_learning(self) -> None:
        self.hunt_patch.stop()
        with patch.object(
            calibration,
            "_sent_targeted_outreach",
            return_value=None,
        ):
            self._application(
                events=[
                    (
                        "rejected",
                        "hh_clean",
                        "platform_observed",
                    )
                ]
            )
            case = self._dataset().cases[0]
        self.hunt_patch.start()

        self.assertEqual(case["cohort"], "attribution_unknown")
        self.assertIsNone(case["targeted_outreach"])
        self.assertFalse(case["eligible_clean_learning"])
        self.assertEqual(
            self._dataset().metrics["eligible_clean_mature_cases"],
            0,
        )

    def test_insufficient_data_does_not_call_llm(self) -> None:
        self._application(
            events=[
                (
                    "recruiter_message",
                    "hh_clean",
                    "user_confirmed",
                )
            ]
        )
        llm = FakeLLM()

        run = calibration.run_calibration(
            llm=llm,
            settings=calibration.CalibrationSettings(
                min_mature=2,
                min_new_mature=1,
                min_pattern_support=2,
                max_llm_cases=20,
            ),
        )

        self.assertEqual(run.status, "insufficient_data")
        self.assertEqual(llm.calls, 0)
        self.assertEqual(run.mature_sample_count, 1)

    def test_completed_report_filters_unsupported_patterns(self) -> None:
        ids = []
        for stage in (
            "recruiter_message",
            "interview_completed",
            "rejected",
        ):
            application_id, _ = self._application(
                events=[
                    (
                        stage,
                        "hh_clean",
                        (
                            "platform_observed"
                            if stage == "rejected"
                            else "user_confirmed"
                        ),
                    )
                ]
            )
            ids.append(application_id)

        llm = FakeLLM(
            {
                "summary": "Grounded report",
                "pattern_candidates": [
                    {
                        "title": "Repeated signal",
                        "hypothesis": "A repeated observed pattern",
                        "observed_signal": "Two grounded cases",
                        "supporting_application_ids": ids[:2],
                        "counterexample_application_ids": [ids[2]],
                        "confidence": "high",
                        "proposed_action": "monitor",
                        "caution": "Small sample",
                    },
                    {
                        "title": "Single anecdote",
                        "hypothesis": "Too weak",
                        "observed_signal": "One case",
                        "supporting_application_ids": [ids[2]],
                        "counterexample_application_ids": [],
                        "confidence": "high",
                        "proposed_action": "candidate_for_threshold_review",
                        "caution": "Anecdotal",
                    },
                ],
                "data_limitations": ["Small sample"],
                "follow_up_checks": ["Collect more outcomes"],
            }
        )

        run = calibration.run_calibration(
            llm=llm,
            settings=calibration.CalibrationSettings(
                min_mature=3,
                min_new_mature=1,
                min_pattern_support=2,
                max_llm_cases=20,
            ),
        )

        self.assertEqual(run.status, "completed")
        self.assertEqual(llm.calls, 1)
        report = json.loads(run.llm_report_json)
        self.assertEqual(len(report["pattern_candidates"]), 1)
        pattern = report["pattern_candidates"][0]
        self.assertEqual(
            pattern["supporting_application_ids"],
            sorted(ids[:2]),
        )
        self.assertEqual(pattern["confidence"], "medium")

    def test_completed_run_requires_new_mature_outcomes(self) -> None:
        for _ in range(2):
            self._application(
                events=[
                    (
                        "recruiter_message",
                        "hh_clean",
                        "user_confirmed",
                    )
                ]
            )

        dataset = self._dataset()
        session = self.Session()
        try:
            session.add(
                CalibrationRun(
                    status="completed",
                    scope="hh_clean",
                    event_high_watermark=dataset.event_high_watermark,
                    snapshot_high_watermark=dataset.snapshot_high_watermark,
                    sample_count=2,
                    mature_sample_count=2,
                    new_mature_sample_count=2,
                    dataset_hash=dataset.dataset_hash,
                    metrics_json="{}",
                    case_summaries_json="[]",
                    prompt_version=calibration.PROMPT_VERSION,
                )
            )
            session.commit()
        finally:
            session.close()

        llm = FakeLLM()
        run = calibration.run_calibration(
            llm=llm,
            settings=calibration.CalibrationSettings(
                min_mature=2,
                min_new_mature=1,
                min_pattern_support=2,
                max_llm_cases=20,
            ),
        )

        self.assertEqual(run.status, "insufficient_data")
        self.assertEqual(run.new_mature_sample_count, 0)
        self.assertEqual(llm.calls, 0)

    def test_dataset_hash_is_stable_without_new_data(self) -> None:
        self._application(
            events=[
                (
                    "no_response_30d",
                    "hh_clean",
                    "derived",
                )
            ]
        )

        first = self._dataset()
        second = self._dataset()
        self.assertEqual(first.dataset_hash, second.dataset_hash)


if __name__ == "__main__":
    unittest.main()
