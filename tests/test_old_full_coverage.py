import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import hh_collect
import old_auto_queue
from app import hh_apply_control
from app import old_auto_policy
from app.db import (
    Application,
    ApplicationDecisionSnapshot,
    Base,
    CoverLetterArtifact,
    Evaluation,
    Vacancy,
)
from old_auto_queue import (
    AUTO_PENDING_STATUS,
    ensure_old_auto_application,
    promote_old_application_if_ready,
    select_old_letter_artifacts,
)


VALID_OLD_LETTER = (
    "Здравствуйте!\n\n"
    "Я управляю полным циклом IT-проектов: от формализации задачи и планирования "
    "до разработки, приемки, запуска и дальнейшего развития. В работе связываю "
    "бизнес, аналитику, разработку, QA, архитектуру и DevOps, держу прозрачными "
    "сроки, риски, зависимости и изменения.\n\n"
    "Мне близки задачи, где нужно одновременно выстроить управляемый delivery, "
    "синхронизировать несколько команд и сохранить понятный след решений в "
    "документации. Такой подход помогает не терять бизнес-цель за техническими "
    "деталями и доводить изменения до фактического результата.\n\n"
    "С уважением,\nАлександр Руденко"
)


class OldFullCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _vacancy(self, session, suffix: str = "1") -> Vacancy:
        vacancy = Vacancy(
            hh_id=f"old-full-{suffix}",
            source="hh",
            external_id=f"old-full-{suffix}",
            title="IT Project Manager",
            company="Example Company",
            url=f"https://hh.ru/vacancy/{suffix}",
            description="Delivery, integrations, stakeholders and IT systems.",
        )
        session.add(vacancy)
        session.flush()
        return vacancy

    def test_old_discovery_waits_for_score_gate_before_auto_queue(self) -> None:
        session = self.Session()
        try:
            vacancy = self._vacancy(session, "101")
            hh_collect.record_hh_discovery(
                session,
                vacancy,
                source_label="SEARCH:Project Manager",
                account_key="old",
            )
            session.commit()

            self.assertIsNone(
                session.scalar(
                    select(Application).where(
                        Application.vacancy_id == vacancy.id,
                        Application.account_key == "old",
                    )
                )
            )
            self.assertIsNone(
                session.scalar(
                    select(CoverLetterArtifact).where(
                        CoverLetterArtifact.vacancy_id == vacancy.id,
                        CoverLetterArtifact.account_key == "old",
                    )
                )
            )

            session.add(
                Evaluation(
                    vacancy_id=vacancy.id,
                    score=81,
                    decision="reject",
                    role_match=90,
                    seniority_match=80,
                    domain_match=60,
                    responsibility_match=80,
                    must_have_missing="[]",
                    nice_to_have_missing="[]",
                    strengths="[]",
                    gaps="[]",
                    red_flags="[]",
                    summary="",
                    recommendation="",
                    cover_letter="",
                    model="test",
                )
            )
            session.commit()
            vacancy_id = vacancy.id
        finally:
            session.close()

        with (
            patch.object(old_auto_queue, "SessionLocal", self.Session),
            patch.object(
                old_auto_policy,
                "current_clean_assessment",
                return_value=SimpleNamespace(routing_class="OLD_REVIEW"),
            ),
        ):
            stats = old_auto_queue.seed_old_auto_queue()

        verify = self.Session()
        try:
            application = verify.scalar(
                select(Application).where(
                    Application.vacancy_id == vacancy_id,
                    Application.account_key == "old",
                )
            )
            artifact = verify.scalar(
                select(CoverLetterArtifact).where(
                    CoverLetterArtifact.vacancy_id == vacancy_id,
                    CoverLetterArtifact.account_key == "old",
                )
            )
            self.assertEqual(stats["eligible"], 1)
            self.assertIsNotNone(application)
            self.assertEqual(application.status, AUTO_PENDING_STATUS)
            self.assertIsNotNone(artifact)
            self.assertEqual(artifact.status, "pending")
        finally:
            verify.close()

    def test_old_score_below_80_is_not_auto_queued(self) -> None:
        session = self.Session()
        try:
            vacancy = self._vacancy(session, "101b")
            hh_collect.record_hh_discovery(
                session,
                vacancy,
                source_label="HH_RECOMMENDATION",
                account_key="old",
            )
            session.add(
                Evaluation(
                    vacancy_id=vacancy.id,
                    score=79,
                    decision="reject",
                    role_match=90,
                    seniority_match=80,
                    domain_match=60,
                    responsibility_match=80,
                    must_have_missing="[]",
                    nice_to_have_missing="[]",
                    strengths="[]",
                    gaps="[]",
                    red_flags="[]",
                    summary="",
                    recommendation="",
                    cover_letter="",
                    model="test",
                )
            )
            session.commit()
            vacancy_id = vacancy.id
        finally:
            session.close()

        with patch.object(old_auto_queue, "SessionLocal", self.Session):
            stats = old_auto_queue.seed_old_auto_queue()

        verify = self.Session()
        try:
            self.assertEqual(stats["eligible"], 0)
            self.assertEqual(stats["ineligible"], 1)
            self.assertIsNone(
                verify.scalar(
                    select(Application).where(
                        Application.vacancy_id == vacancy_id,
                        Application.account_key == "old",
                    )
                )
            )
        finally:
            verify.close()

    def test_old_ineligible_backlog_is_suspended(self) -> None:
        session = self.Session()
        try:
            vacancy = self._vacancy(session, "101c")
            hh_collect.record_hh_discovery(
                session,
                vacancy,
                source_label="SEARCH:Project Manager",
                account_key="old",
            )
            application, _ = ensure_old_auto_application(session, vacancy)
            session.add(
                Evaluation(
                    vacancy_id=vacancy.id,
                    score=70,
                    decision="reject",
                    role_match=70,
                    seniority_match=70,
                    domain_match=70,
                    responsibility_match=70,
                    must_have_missing="[]",
                    nice_to_have_missing="[]",
                    strengths="[]",
                    gaps="[]",
                    red_flags="[]",
                    summary="",
                    recommendation="",
                    cover_letter="",
                    model="test",
                )
            )
            session.commit()
            application_id = application.id
        finally:
            session.close()

        with patch.object(old_auto_queue, "SessionLocal", self.Session):
            stats = old_auto_queue.seed_old_auto_queue()

        verify = self.Session()
        try:
            current = verify.get(Application, application_id)
            artifact = verify.scalar(
                select(CoverLetterArtifact).where(
                    CoverLetterArtifact.vacancy_id == current.vacancy_id,
                    CoverLetterArtifact.account_key == "old",
                )
            )
            self.assertEqual(stats["suspended"], 1)
            self.assertEqual(current.status, "skipped")
            self.assertEqual(artifact.status, "policy_skipped")
        finally:
            verify.close()

    def test_final_letter_promotes_old_application_with_exact_snapshot(self) -> None:
        session = self.Session()
        try:
            vacancy = self._vacancy(session, "102")
            application, _ = ensure_old_auto_application(session, vacancy)
            artifact = session.scalar(
                select(CoverLetterArtifact).where(
                    CoverLetterArtifact.vacancy_id == vacancy.id,
                    CoverLetterArtifact.account_key == "old",
                )
            )
            artifact.status = "final"
            artifact.final_text = VALID_OLD_LETTER
            session.commit()

            promoted = promote_old_application_if_ready(session, vacancy)
            session.commit()

            self.assertIsNotNone(promoted)
            self.assertEqual(application.status, "approved")
            self.assertEqual(application.cover_letter, VALID_OLD_LETTER)

            snapshot = session.scalar(
                select(ApplicationDecisionSnapshot).where(
                    ApplicationDecisionSnapshot.application_id == application.id
                )
            )
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot.cover_letter_final, VALID_OLD_LETTER)
            self.assertEqual(snapshot.account_key, "old")
        finally:
            session.close()

    def test_manual_required_is_never_requeued(self) -> None:
        session = self.Session()
        try:
            vacancy = self._vacancy(session, "103")
            application = Application(
                vacancy_id=vacancy.id,
                account_key="old",
                status="manual_required",
            )
            session.add(application)
            session.commit()

            same, created = ensure_old_auto_application(session, vacancy)
            session.commit()

            self.assertFalse(created)
            self.assertEqual(same.id, application.id)
            self.assertEqual(same.status, "manual_required")
            self.assertIsNone(
                session.scalar(
                    select(CoverLetterArtifact.id).where(
                        CoverLetterArtifact.vacancy_id == vacancy.id
                    )
                )
            )
        finally:
            session.close()

    def test_only_latest_exhausted_cover_artifact_uses_old_fallback(self) -> None:
        session = self.Session()
        try:
            vacancy = self._vacancy(session, "104")
            application, _ = ensure_old_auto_application(session, vacancy)
            old_artifact = session.scalar(
                select(CoverLetterArtifact).where(
                    CoverLetterArtifact.vacancy_id == vacancy.id,
                    CoverLetterArtifact.account_key == "old",
                )
            )
            old_artifact.status = "error"
            old_artifact.generation_attempts = 2
            old_artifact.last_error = "old failure"

            newer = CoverLetterArtifact(
                vacancy_id=vacancy.id,
                account_key="old",
                candidate_profile_version="new-candidate",
                recruiter_resume_version="new-resume",
                vacancy_content_hash="new-content",
                prompt_version="new-prompt",
                status="pending",
                generation_attempts=0,
                validation_json="[]",
            )
            session.add(newer)
            session.flush()
            application_id = application.id
            newer_id = newer.id
            session.commit()
        finally:
            session.close()

        with (
            patch.object(old_auto_queue, "SessionLocal", self.Session),
        ):
            changed = old_auto_queue.recover_exhausted_old_letters(
                max_attempts=2
            )

        verify = self.Session()
        try:
            current = verify.get(Application, application_id)
            self.assertEqual(changed, 0)
            self.assertEqual(current.status, AUTO_PENDING_STATUS)

            newer_db = verify.get(CoverLetterArtifact, newer_id)
            newer_db.status = "error"
            newer_db.generation_attempts = 2
            newer_db.last_error = "latest failure"
            verify.commit()
        finally:
            verify.close()

        with (
            patch.object(old_auto_queue, "SessionLocal", self.Session),
        ):
            changed = old_auto_queue.recover_exhausted_old_letters(
                max_attempts=2
            )

        verify = self.Session()
        try:
            current = verify.get(Application, application_id)
            self.assertEqual(changed, 1)
            self.assertEqual(current.status, "approved")
            recovered_artifact = verify.get(CoverLetterArtifact, newer_id)
            self.assertEqual(recovered_artifact.status, "final")
            self.assertIn("deterministic_old_fallback", recovered_artifact.validation_json)
        finally:
            verify.close()

    def test_old_semantic_skip_beats_high_score(self) -> None:
        session = self.Session()
        try:
            vacancy = self._vacancy(session, "105")
            session.add(
                Evaluation(
                    vacancy_id=vacancy.id,
                    score=99,
                    decision="apply",
                    role_match=99,
                    seniority_match=99,
                    domain_match=99,
                    responsibility_match=99,
                    must_have_missing="[]",
                    nice_to_have_missing="[]",
                    strengths="[]",
                    gaps="[]",
                    red_flags="[]",
                    summary="",
                    recommendation="",
                    cover_letter="",
                    model="test",
                )
            )
            session.commit()

            with patch.object(
                old_auto_policy,
                "current_clean_assessment",
                return_value=SimpleNamespace(routing_class="SKIP"),
            ):
                eligibility = old_auto_policy.old_auto_eligibility(
                    session,
                    vacancy.id,
                )

            self.assertFalse(eligibility.eligible)
            self.assertEqual(eligibility.reason, "routing_class=SKIP")
            self.assertEqual(eligibility.score, 99)
        finally:
            session.close()

    def test_old_rate_state_persists_next_allowed_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "runtime"
            now = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
            with (
                patch.object(hh_apply_control, "ROOT", root),
                patch.object(hh_apply_control, "STATE_DIR", state_dir),
                patch.object(hh_apply_control, "OLD_MIN_DELAY_SECONDS", 180),
                patch.object(hh_apply_control, "OLD_MAX_DELAY_SECONDS", 300),
                patch.object(hh_apply_control, "OLD_MAX_PER_HOUR", 12),
                patch.object(hh_apply_control, "OLD_MAX_PER_DAY", 180),
            ):
                hh_apply_control.record_old_apply_attempt(
                    now=now,
                    rng=lambda _low, _high: 240,
                )
                wait, reason = hh_apply_control.seconds_until_old_slot(
                    now=now + timedelta(seconds=60)
                )

            self.assertEqual(reason, "inter-apply delay")
            self.assertAlmostEqual(wait, 180, delta=0.1)

    def test_old_captcha_switches_to_safe_rate_for_24_hours(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "runtime"
            now = datetime(2026, 9, 30, 0, 0, tzinfo=UTC)
            with (
                patch.object(hh_apply_control, "ROOT", root),
                patch.object(hh_apply_control, "STATE_DIR", state_dir),
                patch.object(hh_apply_control, "OLD_MIN_DELAY_SECONDS", 120),
                patch.object(hh_apply_control, "OLD_MAX_DELAY_SECONDS", 180),
                patch.object(hh_apply_control, "OLD_MAX_PER_HOUR", 29),
                patch.object(hh_apply_control, "OLD_SAFE_MIN_DELAY_SECONDS", 180),
                patch.object(hh_apply_control, "OLD_SAFE_MAX_DELAY_SECONDS", 300),
                patch.object(hh_apply_control, "OLD_SAFE_MAX_PER_HOUR", 12),
                patch.object(hh_apply_control, "OLD_CAPTCHA_BACKOFF_HOURS", 24),
            ):
                fast = hh_apply_control.record_old_apply_attempt(
                    now=now,
                    rng=lambda _low, _high: 150,
                )
                self.assertEqual(fast["throttle_mode"], "fast")
                self.assertEqual(fast["max_per_hour"], 29)
                self.assertEqual(fast["delay_seconds"], 150)

                pause = hh_apply_control.pause_for_captcha(
                    "old",
                    "HH captcha/challenge",
                    application_id=77,
                    now=now + timedelta(minutes=1),
                )
                self.assertEqual(pause["throttle_mode"], "safe")

                safe_limits = hh_apply_control.old_rate_limits(
                    now=now + timedelta(hours=1)
                )
                self.assertEqual(safe_limits["mode"], "safe")
                self.assertEqual(safe_limits["max_per_hour"], 12)
                self.assertEqual(safe_limits["min_delay_seconds"], 180)
                self.assertEqual(safe_limits["max_delay_seconds"], 300)

                hh_apply_control.clear_captcha_pause("old")
                still_safe = hh_apply_control.old_rate_limits(
                    now=now + timedelta(hours=12)
                )
                self.assertEqual(still_safe["mode"], "safe")

                recovered = hh_apply_control.old_rate_limits(
                    now=now + timedelta(hours=25)
                )
                self.assertEqual(recovered["mode"], "fast")
                self.assertEqual(recovered["max_per_hour"], 29)

    def test_captcha_pause_is_persistent_until_explicit_clear(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "runtime"
            with (
                patch.object(hh_apply_control, "ROOT", root),
                patch.object(hh_apply_control, "STATE_DIR", state_dir),
            ):
                hh_apply_control.pause_for_captcha(
                    "old",
                    "HH captcha",
                    application_id=77,
                )
                self.assertTrue(hh_apply_control.is_captcha_paused("old"))
                self.assertEqual(
                    hh_apply_control.captcha_pause("old")["application_id"],
                    77,
                )

                cleared = hh_apply_control.clear_captcha_pause("old")
                self.assertTrue(cleared)
                self.assertFalse(hh_apply_control.is_captcha_paused("old"))


    def test_old_letter_selection_ignores_orphan_backlog(self) -> None:
        session = self.Session()
        try:
            for index in range(60):
                orphan = self._vacancy(session, f"orphan-{index}")
                session.add(
                    CoverLetterArtifact(
                        vacancy_id=orphan.id,
                        account_key="old",
                        candidate_profile_version="old",
                        recruiter_resume_version="old",
                        vacancy_content_hash=f"orphan-{index}",
                        prompt_version="canonical-cover-v1",
                        status="pending",
                        generation_attempts=0,
                        validation_json="[]",
                    )
                )

            vacancy = self._vacancy(session, "eligible-target")
            session.add(
                Evaluation(
                    vacancy_id=vacancy.id,
                    score=81,
                    decision="apply",
                    role_match=90,
                    seniority_match=80,
                    domain_match=60,
                    responsibility_match=80,
                    must_have_missing="[]",
                    nice_to_have_missing="[]",
                    strengths="[]",
                    gaps="[]",
                    red_flags="[]",
                    summary="",
                    recommendation="",
                    cover_letter="",
                    model="test",
                )
            )
            application, _ = ensure_old_auto_application(session, vacancy)
            session.commit()

            target = session.scalar(
                select(CoverLetterArtifact)
                .where(
                    CoverLetterArtifact.vacancy_id == vacancy.id,
                    CoverLetterArtifact.account_key == "old",
                )
                .order_by(CoverLetterArtifact.id.desc())
            )
            self.assertIsNotNone(target)

            with patch.object(
                old_auto_policy,
                "current_clean_assessment",
                return_value=SimpleNamespace(routing_class="OLD_REVIEW"),
            ):
                selected, stats = select_old_letter_artifacts(
                    session,
                    limit=1,
                    max_attempts=2,
                    fresh_budget=1,
                    retry_budget=0,
                    sla_minutes=30,
                )

            self.assertEqual(application.status, AUTO_PENDING_STATUS)
            self.assertEqual([item.id for item in selected], [target.id])
            self.assertEqual(stats["eligible"], 1)
            self.assertEqual(stats["actionable"], 1)
        finally:
            session.close()

    def test_old_letter_selection_uses_latest_artifact_only(self) -> None:
        session = self.Session()
        try:
            vacancy = self._vacancy(session, "latest-artifact")
            session.add(
                Evaluation(
                    vacancy_id=vacancy.id,
                    score=88,
                    decision="apply",
                    role_match=90,
                    seniority_match=90,
                    domain_match=70,
                    responsibility_match=90,
                    must_have_missing="[]",
                    nice_to_have_missing="[]",
                    strengths="[]",
                    gaps="[]",
                    red_flags="[]",
                    summary="",
                    recommendation="",
                    cover_letter="",
                    model="test",
                )
            )
            ensure_old_auto_application(session, vacancy)
            older = session.scalar(
                select(CoverLetterArtifact)
                .where(
                    CoverLetterArtifact.vacancy_id == vacancy.id,
                    CoverLetterArtifact.account_key == "old",
                )
                .order_by(CoverLetterArtifact.id.desc())
            )
            newer = CoverLetterArtifact(
                vacancy_id=vacancy.id,
                account_key="old",
                candidate_profile_version="new-candidate",
                recruiter_resume_version="new-resume",
                vacancy_content_hash="new-content",
                prompt_version="canonical-cover-v1",
                status="pending",
                generation_attempts=0,
                validation_json="[]",
            )
            session.add(newer)
            session.commit()

            with patch.object(
                old_auto_policy,
                "current_clean_assessment",
                return_value=SimpleNamespace(routing_class="COMPANY_RESERVE"),
            ):
                selected, stats = select_old_letter_artifacts(
                    session,
                    limit=10,
                    max_attempts=2,
                    fresh_budget=10,
                    retry_budget=0,
                    sla_minutes=30,
                )

            self.assertEqual([item.id for item in selected], [newer.id])
            self.assertEqual(older.status, "policy_skipped")
            self.assertEqual(
                older.last_error,
                f"superseded_by_artifact:{newer.id}",
            )
            self.assertEqual(stats["superseded"], 1)
        finally:
            session.close()

    def test_old_letter_selection_reserves_recent_and_oldest_lanes(self) -> None:
        session = self.Session()
        now = datetime.now(UTC).replace(tzinfo=None)
        try:
            records = []
            for suffix, age_minutes in (
                ("recent", 45),
                ("oldest", 120),
                ("older", 60),
            ):
                vacancy = self._vacancy(session, suffix)
                session.add(
                    Evaluation(
                        vacancy_id=vacancy.id,
                        score=85,
                        decision="apply",
                        role_match=90,
                        seniority_match=85,
                        domain_match=70,
                        responsibility_match=90,
                        must_have_missing="[]",
                        nice_to_have_missing="[]",
                        strengths="[]",
                        gaps="[]",
                        red_flags="[]",
                        summary="",
                        recommendation="",
                        cover_letter="",
                        model="test",
                    )
                )
                application, _ = ensure_old_auto_application(session, vacancy)
                application.created_at = now - timedelta(minutes=age_minutes)
                artifact = session.scalar(
                    select(CoverLetterArtifact)
                    .where(
                        CoverLetterArtifact.vacancy_id == vacancy.id,
                        CoverLetterArtifact.account_key == "old",
                    )
                    .order_by(CoverLetterArtifact.id.desc())
                )
                records.append((suffix, artifact.id))
            session.commit()

            with patch.object(
                old_auto_policy,
                "current_clean_assessment",
                return_value=SimpleNamespace(routing_class="OLD_REVIEW"),
            ):
                selected, stats = select_old_letter_artifacts(
                    session,
                    limit=2,
                    max_attempts=2,
                    fresh_budget=1,
                    retry_budget=0,
                    sla_minutes=30,
                )

            selected_ids = [item.id for item in selected]
            ids = dict(records)
            self.assertEqual(selected_ids[0], ids["recent"])
            self.assertEqual(selected_ids[1], ids["oldest"])
            self.assertEqual(stats["overdue"], 3)
            self.assertGreaterEqual(stats["oldest_wait_min"], 119)
        finally:
            session.close()



if __name__ == "__main__":
    unittest.main()
