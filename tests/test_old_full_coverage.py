import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import hh_collect
from app import hh_apply_control
from app.db import (
    Application,
    ApplicationDecisionSnapshot,
    Base,
    CoverLetterArtifact,
    Vacancy,
)
from old_auto_queue import (
    AUTO_PENDING_STATUS,
    ensure_old_auto_application,
    promote_old_application_if_ready,
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

    def test_old_discovery_auto_queues_application_and_letter(self) -> None:
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

            application = session.scalar(
                select(Application).where(
                    Application.vacancy_id == vacancy.id,
                    Application.account_key == "old",
                )
            )
            artifact = session.scalar(
                select(CoverLetterArtifact).where(
                    CoverLetterArtifact.vacancy_id == vacancy.id,
                    CoverLetterArtifact.account_key == "old",
                )
            )

            self.assertIsNotNone(application)
            self.assertEqual(application.status, AUTO_PENDING_STATUS)
            self.assertIsNotNone(artifact)
            self.assertEqual(artifact.status, "pending")
        finally:
            session.close()

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


if __name__ == "__main__":
    unittest.main()
