from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import apply_worker
from app.db import (
    Application,
    ApplicationDecisionSnapshot,
    Base,
    Evaluation,
    Vacancy,
)


CANONICAL_OLD = (
    "Здравствуйте!\n\n"
    "Мой основной профиль - управление IT-проектами полного цикла, от требований "
    "и планирования до разработки, приемки, запуска в production и дальнейшего "
    "развития. Я координировал работу аналитики, разработки, QA, архитектуры и "
    "DevOps, управлял сроками, рисками, зависимостями и изменениями.\n\n"
    "В проектах связываю техническую реализацию с бизнес-результатом, фиксирую "
    "решения и договоренности, поддерживаю прозрачный delivery и довожу изменения "
    "до фактического результата в production.\n\n"
    "С уважением,\nАлександр Руденко"
)

LEGACY_OLD = (
    "Здравствуйте!\n\n"
    "Рассматриваю позицию «Руководитель проектов» в Example Company.\n"
    "Мой основной профиль - управление IT-проектами и delivery полного цикла.\n\n"
    "С уважением,\nАлександр Руденко"
)


class OldApplyCanonicalRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _records(self, account_key: str) -> tuple[Application, int]:
        session = self.Session()
        vacancy = Vacancy(
            hh_id=f"{account_key}-cover-recovery",
            source="hh",
            external_id=f"{account_key}-cover-recovery",
            title="Руководитель проектов",
            company="Example Company",
            url="https://hh.ru/vacancy/999999",
            description="Управление IT delivery, интеграциями, сроками и рисками.",
        )
        session.add(vacancy)
        session.flush()
        session.add(
            Evaluation(
                vacancy_id=vacancy.id,
                score=90,
                decision="apply",
                role_match=95,
                seniority_match=90,
                domain_match=80,
                responsibility_match=90,
                must_have_missing="[]",
                nice_to_have_missing="[]",
                strengths='["IT delivery"]',
                gaps="[]",
                red_flags="[]",
                summary="Strong match",
                recommendation="Apply",
                cover_letter=LEGACY_OLD,
                model="test-model",
            )
        )
        application = Application(
            vacancy_id=vacancy.id,
            status="approved",
            account_key=account_key,
            cover_letter=LEGACY_OLD,
        )
        session.add(application)
        session.commit()
        application_id = application.id
        session.expunge(application)
        session.close()
        return application, application_id

    def test_old_without_snapshot_recovers_canonical_and_never_uses_legacy(self) -> None:
        application, application_id = self._records("old")

        with (
            patch.object(apply_worker, "SessionLocal", self.Session),
            patch.object(
                apply_worker,
                "get_or_generate_cover_letter",
                return_value=(CANONICAL_OLD, False),
            ) as canonical,
            patch.object(
                apply_worker,
                "build_legacy_vacancy_cover_letter",
                side_effect=AssertionError("legacy OLD fallback must not run"),
            ),
        ):
            result = apply_worker.enforce_application_cover_letter_policy(
                application
            )

        self.assertEqual(result, CANONICAL_OLD)
        self.assertEqual(application.cover_letter, CANONICAL_OLD)
        canonical.assert_called_once()

        verify = self.Session()
        try:
            stored = verify.get(Application, application_id)
            snapshot = verify.scalar(
                select(ApplicationDecisionSnapshot).where(
                    ApplicationDecisionSnapshot.application_id == application_id
                )
            )
            self.assertEqual(stored.cover_letter, CANONICAL_OLD)
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot.cover_letter_final, CANONICAL_OLD)
            self.assertNotIn("Example Company", snapshot.cover_letter_final)
            self.assertNotIn("Рассматриваю позицию", snapshot.cover_letter_final)
        finally:
            verify.close()

    def test_clean_without_snapshot_keeps_existing_legacy_branch(self) -> None:
        application, _ = self._records("clean")

        with (
            patch.object(apply_worker, "SessionLocal", self.Session),
            patch.object(
                apply_worker,
                "get_or_generate_cover_letter",
                side_effect=AssertionError("CLEAN must not enter OLD recovery"),
            ),
            patch.object(
                apply_worker,
                "build_legacy_vacancy_cover_letter",
                return_value="clean-existing-path",
            ) as legacy,
        ):
            result = apply_worker.enforce_application_cover_letter_policy(
                application
            )

        self.assertEqual(result, "clean-existing-path")
        self.assertEqual(application.cover_letter, "clean-existing-path")
        legacy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
