import unittest
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Application, Base, CleanLiveQueue, Evaluation, Vacancy
from process_vacancies import pending_legacy_evaluations


class LegacyEvaluationRecoveryTests(unittest.TestCase):
    def test_recover_already_applied_without_evaluation_only_when_clean_waits(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            def create(hh_id, *, processed=True, enqueue=False, has_eval=False):
                vacancy = Vacancy(
                    hh_id=hh_id,
                    source="hh",
                    external_id=hh_id,
                    title="Senior Project Manager",
                    company="Example",
                    url=f"https://hh.ru/vacancy/{hh_id}",
                    description="End-to-end delivery management",
                    found_at=datetime.now(UTC).replace(tzinfo=None),
                    processed=processed,
                )
                session.add(vacancy)
                session.flush()
                if enqueue:
                    session.add(CleanLiveQueue(vacancy_id=vacancy.id))
                if has_eval:
                    session.add(
                        Evaluation(
                            vacancy_id=vacancy.id, score=83, decision="review",
                            role_match=90, seniority_match=90, domain_match=75,
                            responsibility_match=80, must_have_missing="[]",
                            nice_to_have_missing="[]", strengths="[]",
                            gaps="[]", red_flags="[]", summary="ok",
                            recommendation="review", cover_letter="", model="test",
                        )
                    )
                return vacancy

            stuck = create("stuck", enqueue=True)
            session.add(Application(vacancy_id=stuck.id, account_key="old", status="already_applied"))
            ignored = create("already-known-no-clean")
            handled = create("handled", enqueue=True, has_eval=True)
            pending = create("normal-pending", processed=False)
            session.commit()

            matches = {v.hh_id for v in pending_legacy_evaluations(session)}
            self.assertEqual(matches, {"stuck", "normal-pending"})
            self.assertEqual(stuck.processed, True)
            self.assertEqual(
                session.query(Application.status)
                .filter(Application.vacancy_id == stuck.id)
                .scalar(), "already_applied",
            )
            self.assertNotIn(ignored.hh_id, matches)
            self.assertNotIn(handled.hh_id, matches)
            self.assertIn(pending.hh_id, matches)
        finally:
            session.close()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
