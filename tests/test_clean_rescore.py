from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import app.clean_rescore as rescore
from app.clean_shadow import CleanShadowExtraction
from app.db import (
    Application,
    Base,
    CleanRescoreItem,
    CleanRescoreRun,
    CleanShadowAssessment,
    Evaluation,
    Vacancy,
)


NOW = datetime(2026, 9, 23, 12, 0, 0)


def strong_extraction() -> CleanShadowExtraction:
    return CleanShadowExtraction.model_validate(
        {
            "role_family_primary": "PROJECT_CORE",
            "role_family_secondary": None,
            "primary_object": "project",
            "project_lifecycle_ownership": "full",
            "clean_role_class": "core",
            "role_confidence": 0.95,
            "role_rationale": "E2E IT project delivery.",
            "complexity_seniority": "strong",
            "technical_context_fit": "strong",
            "domain_affinity": "direct",
            "change_outcome_fit": "strong",
            "role_narrative_coherence": "strong",
            "recent_relevant_evidence": "strong_recent",
            "seniority_autonomy_visibility": "strong",
            "domain_technical_visibility": "direct",
            "visible_differentiators": "strong",
            "cover_surfaced_evidence": "none",
            "unwanted_domain_status": "pass",
            "location_work_auth_status": "pass",
            "requirements": [
                {
                    "name": "IT project management",
                    "category": "other",
                    "criticality": "core",
                    "evidence_visibility": "CV_DIRECT",
                    "match_quality": "full",
                    "source_text": "Управление IT-проектами полного цикла",
                    "candidate_evidence": "Full lifecycle delivery",
                }
            ],
            "top_fit_reasons": [],
            "top_invite_reasons": [],
            "invite_risks": [],
        }
    )


class FakeEvaluator:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def evaluate(self, **kwargs):
        self.calls.append(kwargs)
        return strong_extraction()


class CleanRescoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session_patch = patch.object(
            rescore,
            "SessionLocal",
            self.Session,
        )
        self.memory_patch = patch.object(
            rescore,
            "get_active_memory",
            return_value={
                "version": {
                    "version_number": 1,
                    "content_hash": "a" * 64,
                },
                "learned_patterns": [],
            },
        )
        self.session_patch.start()
        self.memory_patch.start()

    def tearDown(self) -> None:
        self.memory_patch.stop()
        self.session_patch.stop()
        self.engine.dispose()

    def _vacancy(
        self,
        *,
        suffix: str,
        found_at: datetime,
        company: str = "Example",
        title: str = "Project Manager",
    ) -> int:
        session = self.Session()
        try:
            row = Vacancy(
                hh_id=f"hh-{suffix}",
                source="hh",
                external_id=f"hh-{suffix}",
                title=title,
                company=company,
                url=f"https://hh.ru/vacancy/{suffix}",
                description=f"Original description {suffix} " * 30,
                found_at=found_at,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.id
        finally:
            session.close()

    def _evaluation(
        self,
        vacancy_id: int,
        *,
        score: int = 1,
        decision: str = "REJECT",
        cover: str = "LEGACY SECRET COVER",
    ) -> int:
        session = self.Session()
        try:
            row = Evaluation(
                vacancy_id=vacancy_id,
                score=score,
                decision=decision,
                role_match=0,
                seniority_match=0,
                domain_match=0,
                responsibility_match=0,
                must_have_missing="legacy",
                nice_to_have_missing="legacy",
                strengths="legacy",
                gaps="legacy",
                red_flags="legacy",
                summary="legacy",
                recommendation="legacy",
                cover_letter=cover,
                model="legacy-model",
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.id
        finally:
            session.close()

    def _create_run(self):
        return rescore.create_rescore_run(
            candidate_profile_version="candidate-v1",
            recruiter_resume_version="clean-cv-v1",
            window_days=7,
            now=NOW,
            reuse_open=False,
        )

    def test_snapshot_includes_vacancy_without_legacy_evaluation(self) -> None:
        with_eval = self._vacancy(
            suffix="1",
            found_at=NOW - timedelta(days=1),
        )
        self._evaluation(with_eval)
        without_eval = self._vacancy(
            suffix="2",
            found_at=NOW - timedelta(days=2),
        )
        self._vacancy(
            suffix="old",
            found_at=NOW - timedelta(days=8),
        )

        run = self._create_run()

        session = self.Session()
        try:
            items = session.scalars(
                select(CleanRescoreItem)
                .where(CleanRescoreItem.run_id == run.id)
                .order_by(CleanRescoreItem.vacancy_id)
            ).all()
            self.assertEqual(run.selected_count, 2)
            self.assertEqual(len(items), 2)
            by_vacancy = {item.vacancy_id: item for item in items}
            self.assertIsNotNone(
                by_vacancy[with_eval].legacy_evaluation_id
            )
            self.assertIsNone(
                by_vacancy[without_eval].legacy_evaluation_id
            )
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(
                        CleanShadowAssessment
                    )
                ),
                0,
            )
        finally:
            session.close()

    def test_legacy_score_decision_and_cover_are_not_evaluator_inputs(self) -> None:
        vacancy_id = self._vacancy(
            suffix="legacy",
            found_at=NOW - timedelta(hours=1),
        )
        self._evaluation(
            vacancy_id,
            score=0,
            decision="REJECT",
            cover="AI AGENT MUST NOT LEAK",
        )
        run = self._create_run()
        evaluator = FakeEvaluator()

        result = rescore.process_rescore_batch(
            run_id=run.id,
            candidate_facts="INTERNAL FACTS",
            recruiter_visible_resume="CURRENT CLEAN CV",
            candidate_profile_version="candidate-v1",
            recruiter_resume_version="clean-cv-v1",
            evaluator=evaluator,
            limit=1,
            availability_probe=lambda snapshot: "active",
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(evaluator.calls), 1)
        call = evaluator.calls[0]
        self.assertEqual(call["cover_letter"], "")
        self.assertEqual(
            call["recruiter_visible_resume"],
            "CURRENT CLEAN CV",
        )
        self.assertNotIn("AI AGENT MUST NOT LEAK", call["vacancy"])

    def test_vacancy_payload_is_immutable_after_snapshot(self) -> None:
        vacancy_id = self._vacancy(
            suffix="immutable",
            found_at=NOW - timedelta(hours=1),
            title="Original PM",
        )
        run = self._create_run()

        session = self.Session()
        try:
            vacancy = session.get(Vacancy, vacancy_id)
            vacancy.title = "Mutated Product Role"
            vacancy.description = "MUTATED"
            session.commit()
        finally:
            session.close()

        evaluator = FakeEvaluator()
        rescore.process_rescore_batch(
            run_id=run.id,
            candidate_facts="facts",
            recruiter_visible_resume="cv",
            candidate_profile_version="candidate-v1",
            recruiter_resume_version="clean-cv-v1",
            evaluator=evaluator,
            availability_probe=lambda snapshot: "active",
        )

        self.assertIn("Original PM", evaluator.calls[0]["vacancy"])
        self.assertNotIn(
            "Mutated Product Role",
            evaluator.calls[0]["vacancy"],
        )

    def test_version_drift_blocks_mixed_run(self) -> None:
        self._vacancy(
            suffix="drift",
            found_at=NOW - timedelta(hours=1),
        )
        run = self._create_run()

        with patch.object(
            rescore,
            "get_active_memory",
            return_value={
                "version": {
                    "version_number": 2,
                    "content_hash": "b" * 64,
                },
                "learned_patterns": [],
            },
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "version drift",
            ):
                rescore.process_rescore_batch(
                    run_id=run.id,
                    candidate_facts="facts",
                    recruiter_visible_resume="cv",
                    candidate_profile_version="candidate-v1",
                    recruiter_resume_version="clean-cv-v1",
                    evaluator=FakeEvaluator(),
                )

    def test_closed_clean_candidate_is_downgraded(self) -> None:
        self._vacancy(
            suffix="closed",
            found_at=NOW - timedelta(hours=1),
        )
        run = self._create_run()

        result = rescore.process_rescore_batch(
            run_id=run.id,
            candidate_facts="facts",
            recruiter_visible_resume="cv",
            candidate_profile_version="candidate-v1",
            recruiter_resume_version="clean-cv-v1",
            evaluator=FakeEvaluator(),
            availability_probe=lambda snapshot: "closed",
        )
        self.assertEqual(result.status, "completed")

        session = self.Session()
        try:
            item = session.scalar(
                select(CleanRescoreItem).where(
                    CleanRescoreItem.run_id == run.id
                )
            )
            self.assertEqual(item.availability_status, "closed")
            self.assertEqual(item.routing_class, "SKIP")
            self.assertIn(
                "vacancy_closed",
                json.loads(item.route_reason_codes),
            )
        finally:
            session.close()

    def test_company_ranking_uses_fresher_vacancy_as_primary(self) -> None:
        older = self._vacancy(
            suffix="older",
            found_at=NOW - timedelta(hours=3),
            company="Same Co",
        )
        newer = self._vacancy(
            suffix="newer",
            found_at=NOW - timedelta(hours=1),
            company="Same Co",
        )
        run = self._create_run()

        rescore.process_rescore_batch(
            run_id=run.id,
            candidate_facts="facts",
            recruiter_visible_resume="cv",
            candidate_profile_version="candidate-v1",
            recruiter_resume_version="clean-cv-v1",
            evaluator=FakeEvaluator(),
            limit=10,
            availability_probe=lambda snapshot: "active",
        )

        session = self.Session()
        try:
            rows = session.scalars(
                select(CleanRescoreItem).where(
                    CleanRescoreItem.run_id == run.id
                )
            ).all()
            by_vacancy = {item.vacancy_id: item for item in rows}
            self.assertEqual(by_vacancy[newer].company_rank, 1)
            self.assertEqual(
                by_vacancy[newer].routing_class,
                "CLEAN_STRONG",
            )
            self.assertEqual(by_vacancy[older].company_rank, 2)
            self.assertEqual(
                by_vacancy[older].routing_class,
                "COMPANY_RESERVE",
            )
        finally:
            session.close()

    def test_existing_clean_application_reserves_other_company_vacancy(self) -> None:
        applied = self._vacancy(
            suffix="applied",
            found_at=NOW - timedelta(hours=2),
            company="Busy Co",
        )
        candidate = self._vacancy(
            suffix="candidate",
            found_at=NOW - timedelta(hours=1),
            company="Busy Co",
        )
        session = self.Session()
        try:
            session.add(
                Application(
                    vacancy_id=applied,
                    status="applied",
                    account_key="clean",
                )
            )
            session.commit()
        finally:
            session.close()

        run = self._create_run()
        rescore.process_rescore_batch(
            run_id=run.id,
            candidate_facts="facts",
            recruiter_visible_resume="cv",
            candidate_profile_version="candidate-v1",
            recruiter_resume_version="clean-cv-v1",
            evaluator=FakeEvaluator(),
            limit=10,
            availability_probe=lambda snapshot: "active",
        )

        session = self.Session()
        try:
            item = session.scalar(
                select(CleanRescoreItem).where(
                    CleanRescoreItem.run_id == run.id,
                    CleanRescoreItem.vacancy_id == candidate,
                )
            )
            self.assertEqual(item.company_state, "ACTIVE_CLEAN")
            self.assertEqual(
                item.routing_class,
                "COMPANY_RESERVE",
            )
        finally:
            session.close()

    def test_resumable_batches_finish_same_snapshot(self) -> None:
        for index in range(3):
            self._vacancy(
                suffix=f"batch-{index}",
                found_at=NOW - timedelta(hours=index + 1),
            )
        run = self._create_run()
        evaluator = FakeEvaluator()

        first = rescore.process_rescore_batch(
            run_id=run.id,
            candidate_facts="facts",
            recruiter_visible_resume="cv",
            candidate_profile_version="candidate-v1",
            recruiter_resume_version="clean-cv-v1",
            evaluator=evaluator,
            limit=2,
            availability_probe=lambda snapshot: "active",
        )
        self.assertEqual(first.status, "running")
        self.assertEqual(first.processed_count, 2)

        second = rescore.process_rescore_batch(
            run_id=run.id,
            candidate_facts="facts",
            recruiter_visible_resume="cv",
            candidate_profile_version="candidate-v1",
            recruiter_resume_version="clean-cv-v1",
            evaluator=evaluator,
            limit=2,
            availability_probe=lambda snapshot: "active",
        )
        self.assertEqual(second.status, "completed")
        self.assertEqual(second.processed_count, 3)
        self.assertEqual(second.selected_count, 3)

    def test_summary_uses_live_item_counts_after_interruption(self) -> None:
        self._vacancy(
            suffix="live-summary-1",
            found_at=NOW - timedelta(hours=1),
        )
        self._vacancy(
            suffix="live-summary-2",
            found_at=NOW - timedelta(hours=2),
        )
        run = self._create_run()

        session = self.Session()
        try:
            item = session.scalar(
                select(CleanRescoreItem)
                .where(CleanRescoreItem.run_id == run.id)
                .order_by(CleanRescoreItem.id)
            )
            item.status = "ok"
            item.routing_class = "SKIP"
            item.base_routing_class = "SKIP"
            item.availability_status = "not_required"
            session.commit()

            stale_run = session.get(CleanRescoreRun, run.id)
            self.assertEqual(stale_run.processed_count, 0)
        finally:
            session.close()

        summary = rescore.get_rescore_summary(run.id)
        self.assertEqual(summary["selected_count"], 2)
        self.assertEqual(summary["processed_count"], 1)
        self.assertEqual(summary["ok_count"], 1)
        self.assertEqual(summary["status"], "running")

    def test_resume_reconciles_stale_run_counters(self) -> None:
        self._vacancy(
            suffix="reconcile-1",
            found_at=NOW - timedelta(hours=1),
        )
        self._vacancy(
            suffix="reconcile-2",
            found_at=NOW - timedelta(hours=2),
        )
        run = self._create_run()

        session = self.Session()
        try:
            item = session.scalar(
                select(CleanRescoreItem)
                .where(CleanRescoreItem.run_id == run.id)
                .order_by(CleanRescoreItem.id)
            )
            item.status = "ok"
            item.routing_class = "SKIP"
            item.base_routing_class = "SKIP"
            item.availability_status = "not_required"
            session.commit()
        finally:
            session.close()

        result = rescore.process_rescore_batch(
            run_id=run.id,
            candidate_facts="facts",
            recruiter_visible_resume="cv",
            candidate_profile_version="candidate-v1",
            recruiter_resume_version="clean-cv-v1",
            evaluator=FakeEvaluator(),
            limit=1,
            availability_probe=lambda snapshot: "active",
        )
        self.assertEqual(result.processed_count, 2)
        self.assertEqual(result.ok_count, 2)
        self.assertEqual(result.status, "completed")

    def test_runtime_budget_stops_before_starting_new_item(self) -> None:
        self._vacancy(
            suffix="budget",
            found_at=NOW - timedelta(hours=1),
        )
        run = self._create_run()
        evaluator = FakeEvaluator()

        with patch.object(
            rescore.time,
            "monotonic",
            side_effect=[0.0, 1100.0],
        ):
            result = rescore.process_rescore_batch(
                run_id=run.id,
                candidate_facts="facts",
                recruiter_visible_resume="cv",
                candidate_profile_version="candidate-v1",
                recruiter_resume_version="clean-cv-v1",
                evaluator=evaluator,
                limit=1,
                availability_probe=lambda snapshot: "active",
                max_runtime_seconds=1200,
                item_start_guard_seconds=180,
            )

        self.assertTrue(result.budget_exhausted)
        self.assertEqual(result.batch_processed, 0)
        self.assertEqual(result.processed_count, 0)
        self.assertEqual(result.status, "running")
        self.assertEqual(evaluator.calls, [])

    def test_create_reuses_open_run_instead_of_forking_task19(self) -> None:
        self._vacancy(
            suffix="reuse",
            found_at=NOW - timedelta(hours=1),
        )
        first = rescore.create_rescore_run(
            candidate_profile_version="candidate-v1",
            recruiter_resume_version="clean-cv-v1",
            now=NOW,
        )
        second = rescore.create_rescore_run(
            candidate_profile_version="candidate-v1",
            recruiter_resume_version="clean-cv-v1",
            now=NOW + timedelta(minutes=5),
        )
        self.assertEqual(first.id, second.id)

        session = self.Session()
        try:
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(CleanRescoreRun)
                ),
                1,
            )
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
