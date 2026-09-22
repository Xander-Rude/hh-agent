from __future__ import annotations

import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import (
    Application,
    Base,
    CleanShadowAssessment,
    Evaluation,
    Vacancy,
)
from app.decision_snapshot import ensure_decision_snapshot


class DecisionSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def _evaluation(
        self,
        vacancy_id: int,
        *,
        cover: str,
        score: int = 90,
    ) -> Evaluation:
        return Evaluation(
            vacancy_id=vacancy_id,
            score=score,
            decision="apply",
            role_match=95,
            seniority_match=90,
            domain_match=80,
            responsibility_match=90,
            must_have_missing="[]",
            nice_to_have_missing="[]",
            strengths='["full-cycle IT delivery"]',
            gaps="[]",
            red_flags="[]",
            summary="Strong PM match",
            recommendation="Apply",
            cover_letter=cover,
            model="test-model",
            selected_resume_key="project",
            selected_resume_title="Руководитель проектов",
            selected_resume_id="clean-resume",
            selected_resume_score=95,
        )

    def test_snapshot_is_idempotent_and_pins_approved_versions(self) -> None:
        session = self.Session()
        try:
            vacancy = Vacancy(
                hh_id="10001",
                source="hh",
                external_id="10001",
                title="Senior IT Project Manager",
                company="Example",
                url="https://hh.ru/vacancy/10001",
                description="Full-cycle IT project delivery " * 20,
            )
            session.add(vacancy)
            session.flush()

            first_eval = self._evaluation(
                vacancy.id,
                cover="Approved cover letter",
            )
            session.add(first_eval)
            session.flush()

            shadow = CleanShadowAssessment(
                vacancy_id=vacancy.id,
                legacy_evaluation_id=first_eval.id,
                status="ok",
                fit_score=91,
                invite_score=88,
                role_family="PROJECT_CORE",
                role_confidence_pct=94,
                hard_stops="[]",
                base_routing_class="CLEAN_STRONG",
                routing_class="CLEAN_STRONG",
                route_reason_codes='["FIT_STRONG","INVITE_STRONG"]',
                company_entity_key="example",
                company_rank=1,
                company_state="PRIMARY",
                extraction_json="{}",
                candidate_profile_version="candidate-v1",
                recruiter_resume_version="resume-v1",
                prompt_version="prompt-v1",
                scoring_version="score-v1",
                gate_version="gate-v1",
                routing_version="routing-v1",
                company_policy_version="company-v1",
            )
            session.add(shadow)

            application = Application(
                vacancy_id=vacancy.id,
                status="notified",
                account_key="clean",
                cover_letter="stale letter",
            )
            session.add(application)
            session.commit()

            snapshot = ensure_decision_snapshot(
                session,
                application=application,
                vacancy=vacancy,
            )
            session.commit()

            self.assertEqual(snapshot.fit_score, 91)
            self.assertEqual(snapshot.invite_score, 88)
            self.assertEqual(snapshot.routing_class, "CLEAN_STRONG")
            self.assertEqual(
                snapshot.legacy_evaluation_id,
                first_eval.id,
            )
            self.assertEqual(
                snapshot.shadow_assessment_id,
                shadow.id,
            )
            self.assertEqual(
                snapshot.cover_letter_final,
                "Approved cover letter",
            )
            self.assertEqual(
                application.cover_letter,
                "Approved cover letter",
            )
            vacancy_data = json.loads(snapshot.vacancy_snapshot)
            self.assertEqual(
                vacancy_data["title"],
                "Senior IT Project Manager",
            )

            second_eval = self._evaluation(
                vacancy.id,
                cover="Newer letter that must not replace approval",
                score=99,
            )
            session.add(second_eval)
            session.commit()

            same = ensure_decision_snapshot(
                session,
                application=application,
                vacancy=vacancy,
            )
            session.commit()

            self.assertEqual(same.id, snapshot.id)
            self.assertEqual(
                same.legacy_evaluation_id,
                first_eval.id,
            )
            self.assertEqual(
                same.cover_letter_final,
                "Approved cover letter",
            )
        finally:
            session.close()
            self.engine.dispose()


if __name__ == "__main__":
    unittest.main()
