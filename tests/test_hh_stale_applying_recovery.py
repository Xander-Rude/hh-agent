import apply_dispatcher as dispatcher
from app.db import Application, SessionLocal, Vacancy


def test_recover_stale_hh_applying_quarantines_without_retry(monkeypatch):
    session = SessionLocal()
    try:
        vacancy = Vacancy(
            hh_id="test-stale-applying",
            title="Stale applying regression",
            url="https://hh.ru/vacancy/test-stale-applying",
            source="hh",
            processed=True,
        )
        session.add(vacancy)
        session.flush()
        application = Application(
            vacancy_id=vacancy.id,
            status="applying",
            account_key=dispatcher.hh_worker.ACTIVE_ACCOUNT.key,
        )
        session.add(application)
        session.commit()
        application_id = application.id

        assert dispatcher.recover_stale_hh_applying() >= 1

        session.expire_all()
        recovered = session.get(Application, application_id)
        assert recovered.status == "manual_required"
    finally:
        try:
            if "application_id" in locals():
                row = session.get(Application, application_id)
                if row is not None:
                    session.delete(row)
            if "vacancy" in locals():
                row = session.get(Vacancy, vacancy.id)
                if row is not None:
                    session.delete(row)
            session.commit()
        finally:
            session.close()
