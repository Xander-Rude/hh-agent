from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

import apply_worker
from app.db import Application, Vacancy


def test_materialize_transient_rehydrates_expired_columns():
    engine = create_engine("sqlite:///:memory:")
    Vacancy.__table__.create(engine)
    Application.__table__.create(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=True)
    session = Session()

    vacancy = Vacancy(
        hh_id="queue-detach-test",
        source="hh",
        external_id="queue-detach-test",
        title="Test role",
        company="Test company",
        url="https://hh.ru/vacancy/test",
        description="Test description",
    )
    session.add(vacancy)
    session.flush()

    application = Application(
        vacancy_id=vacancy.id,
        status="approved",
        account_key="old",
        cover_letter="Test letter",
    )
    session.add(application)
    session.commit()

    assert inspect(application).expired
    assert inspect(vacancy).expired

    apply_worker._materialize_transient(application)
    apply_worker._materialize_transient(vacancy)
    session.close()

    assert inspect(application).transient
    assert inspect(vacancy).transient
    assert application.id is not None
    assert application.status == "approved"
    assert application.cover_letter == "Test letter"
    assert vacancy.id == application.vacancy_id
    assert vacancy.title == "Test role"
