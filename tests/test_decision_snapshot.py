from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import (
    Application,
    Base,
    CleanShadowAssessment,
    Evaluation,
    Vacancy,
)
from app.decision_snapshot import (
    ensure_decision_snapshot,
    refresh_pending_decision_snapshot_cover_letter,
)
from app.cover_letter_runtime import (
    build_clean_cover_letter,
    is_vacancy_bound_cover_letter,
)
from app.clean_shadow import (
    COMPANY_POLICY_VERSION,
    GATE_VERSION,
    PROMPT_VERSION,
    ROUTING_VERSION,
    SCORING_VERSION,
)


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
                learned_patterns_version="strategy-memory-v1:2:abcdef1234567890",
                prompt_version=PROMPT_VERSION,
                scoring_version=SCORING_VERSION,
                gate_version=GATE_VERSION,
                routing_version=ROUTING_VERSION,
                company_policy_version=COMPANY_POLICY_VERSION,
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

            with patch(
                "app.decision_snapshot.current_clean_assessment",
                return_value=shadow,
            ):
                snapshot = ensure_decision_snapshot(
                    session,
                    application=application,
                    vacancy=vacancy,
                )
            session.commit()

            self.assertEqual(snapshot.fit_score, 91)
            self.assertEqual(snapshot.invite_score, 88)
            self.assertEqual(snapshot.routing_class, "CLEAN_STRONG")
            self.assertEqual(snapshot.prompt_version, PROMPT_VERSION)
            self.assertEqual(
                snapshot.learned_patterns_version,
                "strategy-memory-v1:2:abcdef1234567890",
            )
            self.assertEqual(snapshot.scoring_version, SCORING_VERSION)
            self.assertEqual(snapshot.gate_version, GATE_VERSION)
            self.assertEqual(snapshot.routing_version, ROUTING_VERSION)
            self.assertEqual(
                snapshot.company_policy_version,
                COMPANY_POLICY_VERSION,
            )
            self.assertEqual(
                snapshot.legacy_evaluation_id,
                first_eval.id,
            )
            self.assertEqual(
                snapshot.shadow_assessment_id,
                shadow.id,
            )
            expected_cover = build_clean_cover_letter(
                vacancy_title=vacancy.title,
                vacancy_company=vacancy.company,
                vacancy_description=vacancy.description or "",
                extraction_json=shadow.extraction_json,
            )
            self.assertEqual(
                snapshot.cover_letter_final,
                expected_cover,
            )
            self.assertEqual(
                application.cover_letter,
                expected_cover,
            )
            self.assertEqual(
                application.selected_resume_id,
                "b5d6fbf3ff1124b2890039ed1f394633454535",
            )
            self.assertEqual(
                application.selected_resume_key,
                "hh-clean",
            )
            self.assertIsNone(application.selected_resume_score)
            self.assertEqual(
                snapshot.selected_resume_id,
                "b5d6fbf3ff1124b2890039ed1f394633454535",
            )
            self.assertEqual(snapshot.selected_resume_key, "hh-clean")
            self.assertIsNone(snapshot.selected_resume_score)
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
                expected_cover,
            )
        finally:
            session.close()
            self.engine.dispose()


    def test_old_snapshot_cover_letter_is_vacancy_bound(self) -> None:
        session = self.Session()
        try:
            vacancy = Vacancy(
                hh_id="20001",
                source="hh",
                external_id="20001",
                title="Руководитель проектов",
                company="ЗДОРОВ.ру",
                url="https://hh.ru/vacancy/20001",
                description="Автоматизация, интеграции и полный цикл delivery.",
            )
            session.add(vacancy)
            session.flush()

            evaluation = self._evaluation(
                vacancy.id,
                cover=(
                    "Здравствуйте! У меня многолетний опыт управления IT-проектами "
                    "на уровне senior/lead. Вёл roadmap, сроки, риски, изменения, "
                    "ресурсы, бюджет и работу со стейкхолдерами."
                ),
            )
            session.add(evaluation)
            application = Application(
                vacancy_id=vacancy.id,
                status="notified",
                account_key="old",
            )
            session.add(application)
            session.commit()

            snapshot = ensure_decision_snapshot(
                session,
                application=application,
                vacancy=vacancy,
            )
            session.commit()

            self.assertIn("Руководитель проектов", snapshot.cover_letter_final)
            self.assertIn("ЗДОРОВ.ру", snapshot.cover_letter_final)
            self.assertTrue(
                is_vacancy_bound_cover_letter(
                    snapshot.cover_letter_final,
                    vacancy_title=vacancy.title,
                    vacancy_company=vacancy.company,
                )
            )
        finally:
            session.close()
            self.engine.dispose()

    def test_stale_pending_snapshot_is_replaced_without_rewriting_history(self) -> None:
        session = self.Session()
        try:
            vacancy = Vacancy(
                hh_id="20002",
                source="hh",
                external_id="20002",
                title="Project Manager",
                company="Ecom.tech",
                url="https://hh.ru/vacancy/20002",
                description="IT project delivery and integrations.",
            )
            session.add(vacancy)
            session.flush()
            evaluation = self._evaluation(
                vacancy.id,
                cover="Hello! Strong delivery background.",
            )
            session.add(evaluation)
            application = Application(
                vacancy_id=vacancy.id,
                status="applying",
                account_key="old",
            )
            session.add(application)
            session.commit()

            old_snapshot = ensure_decision_snapshot(
                session,
                application=application,
                vacancy=vacancy,
            )
            stale = (
                "Hello!\n\nMy core profile is end-to-end IT project management.\n\n"
                "Best regards,\nAleksandr Rudenko"
            )
            old_snapshot.cover_letter_final = stale
            application.cover_letter = stale
            session.commit()
            old_id = old_snapshot.id

            repaired = refresh_pending_decision_snapshot_cover_letter(
                session,
                application=application,
                vacancy=vacancy,
            )
            session.commit()

            self.assertIsNotNone(repaired)
            self.assertNotEqual(repaired.id, old_id)
            self.assertIn("Project Manager", repaired.cover_letter_final)
            self.assertIn("Ecom.tech", repaired.cover_letter_final)
            session.refresh(old_snapshot)
            self.assertEqual(old_snapshot.cover_letter_final, stale)
            self.assertEqual(application.cover_letter, repaired.cover_letter_final)
        finally:
            session.close()
            self.engine.dispose()

    def test_applied_snapshot_is_never_repaired(self) -> None:
        session = self.Session()
        try:
            vacancy = Vacancy(
                hh_id="20003",
                source="hh",
                external_id="20003",
                title="Project Manager",
                company="Historical Co",
                url="https://hh.ru/vacancy/20003",
                description="IT delivery.",
            )
            session.add(vacancy)
            session.flush()
            evaluation = self._evaluation(
                vacancy.id,
                cover="Hello! Delivery background.",
            )
            session.add(evaluation)
            application = Application(
                vacancy_id=vacancy.id,
                status="applied",
                account_key="old",
            )
            session.add(application)
            session.commit()

            snapshot = ensure_decision_snapshot(
                session,
                application=application,
                vacancy=vacancy,
            )
            stale = "Historical generic letter"
            snapshot.cover_letter_final = stale
            application.cover_letter = stale
            session.commit()

            same = refresh_pending_decision_snapshot_cover_letter(
                session,
                application=application,
                vacancy=vacancy,
            )
            session.commit()

            self.assertEqual(same.id, snapshot.id)
            self.assertEqual(same.cover_letter_final, stale)
        finally:
            session.close()
            self.engine.dispose()


if __name__ == "__main__":
    unittest.main()
