from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import clean_shadow as worker
from app.db import (
    Base,
    CleanShadowAssessment,
    Evaluation,
    Vacancy,
)


MEMORY_TOKEN = "strategy-memory-v1:1:testhash"


class CleanShadowWorkerVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session_patch = patch.object(
            worker,
            "SessionLocal",
            self.Session,
        )
        self.session_patch.start()

    def tearDown(self) -> None:
        self.session_patch.stop()
        self.engine.dispose()

    def _vacancy_and_evaluation(
        self,
        suffix: str,
        company: str = "Example Co",
    ) -> tuple[int, int]:
        session = self.Session()
        try:
            vacancy = Vacancy(
                hh_id=f"hh-{suffix}",
                source="hh",
                external_id=f"hh-{suffix}",
                title="Project Manager",
                company=company,
                url=f"https://hh.ru/vacancy/{suffix}",
                description="IT project delivery " * 20,
                found_at=datetime(2026, 9, 23, 12, 0, 0),
            )
            session.add(vacancy)
            session.flush()
            evaluation = Evaluation(
                vacancy_id=vacancy.id,
                score=80,
                decision="REVIEW",
                role_match=80,
                seniority_match=80,
                domain_match=80,
                responsibility_match=80,
                must_have_missing="[]",
                nice_to_have_missing="[]",
                strengths="[]",
                gaps="[]",
                red_flags="[]",
                summary="legacy",
                recommendation="legacy",
                cover_letter="",
                model="legacy",
            )
            session.add(evaluation)
            session.commit()
            return vacancy.id, evaluation.id
        finally:
            session.close()

    def _shadow(
        self,
        *,
        vacancy_id: int,
        evaluation_id: int,
        gate_version: str | None = None,
        candidate_profile_version: str | None = None,
        recruiter_resume_version: str | None = None,
        routing_version: str | None = None,
        company_policy_version: str | None = None,
    ) -> int:
        session = self.Session()
        try:
            row = CleanShadowAssessment(
                vacancy_id=vacancy_id,
                legacy_evaluation_id=evaluation_id,
                status="ok",
                fit_score=90,
                invite_score=90,
                role_family="PROJECT_CORE",
                role_confidence_pct=95,
                hard_stops="[]",
                base_routing_class="CLEAN_STRONG",
                routing_class="CLEAN_STRONG",
                route_reason_codes="[]",
                extraction_json="{}",
                candidate_profile_version=(
                    candidate_profile_version
                    or worker.CANDIDATE_PROFILE_VERSION
                ),
                recruiter_resume_version=(
                    recruiter_resume_version
                    or worker.RECRUITER_RESUME_VERSION
                ),
                learned_patterns_version=MEMORY_TOKEN,
                prompt_version=worker.PROMPT_VERSION,
                scoring_version=worker.SCORING_VERSION,
                gate_version=gate_version or worker.GATE_VERSION,
                routing_version=routing_version or worker.ROUTING_VERSION,
                company_policy_version=(
                    company_policy_version
                    or worker.COMPANY_POLICY_VERSION
                ),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.id
        finally:
            session.close()

    def test_existing_current_rejects_any_stale_logic_dimension(self) -> None:
        dimensions = {
            "gate_version": "clean-shadow-gates-v3",
            "candidate_profile_version": "candidate-old",
            "recruiter_resume_version": "resume-old",
            "routing_version": "routing-old",
            "company_policy_version": "company-old",
        }

        for index, (field, stale_value) in enumerate(
            dimensions.items(),
            start=1,
        ):
            with self.subTest(field=field):
                vacancy_id, evaluation_id = self._vacancy_and_evaluation(
                    f"stale-{index}"
                )
                self._shadow(
                    vacancy_id=vacancy_id,
                    evaluation_id=evaluation_id,
                    **{field: stale_value},
                )
                session = self.Session()
                try:
                    current = worker._existing_current(
                        session,
                        evaluation_id,
                        MEMORY_TOKEN,
                    )
                    self.assertIsNone(current)
                finally:
                    session.close()

    def test_company_ranking_uses_only_current_logic_versions(self) -> None:
        stale_vacancy, stale_eval = self._vacancy_and_evaluation(
            "stale-rank",
            company="Same Co",
        )
        current_vacancy, current_eval = self._vacancy_and_evaluation(
            "current-rank",
            company="Same Co",
        )
        stale_id = self._shadow(
            vacancy_id=stale_vacancy,
            evaluation_id=stale_eval,
            gate_version="clean-shadow-gates-v3",
        )
        current_id = self._shadow(
            vacancy_id=current_vacancy,
            evaluation_id=current_eval,
        )

        worker._rank_companies(self.Session(), MEMORY_TOKEN)

        session = self.Session()
        try:
            stale = session.get(CleanShadowAssessment, stale_id)
            current = session.get(CleanShadowAssessment, current_id)
            self.assertIsNone(stale.company_rank)
            self.assertEqual(current.company_rank, 1)
            self.assertEqual(current.company_state, "PRIMARY")
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
