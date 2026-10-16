import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import hh_collect
from app.db import Base, CleanLiveQueue, Evaluation, HhVacancyDiscovery, Vacancy


class CleanRediscoveryQueueTests(unittest.TestCase):
    def test_rediscovery_only_queues_when_clean_assessment_is_not_current(self):
        cases = (
            ("current", False),
            ("outdated_legacy", True),
            ("missing", True),
            ("no_legacy", True),
        )
        for assessment_state, expected_queued in cases:
            with self.subTest(assessment_state=assessment_state):
                engine = create_engine("sqlite:///:memory:")
                Base.metadata.create_all(engine)
                Session = sessionmaker(bind=engine)
                session = Session()
                try:
                    vacancy = Vacancy(
                        hh_id=f"rediscovery-{assessment_state}",
                        source="hh",
                        external_id=f"rediscovery-{assessment_state}",
                        title="Senior Project Manager",
                        company="Example",
                        url="https://hh.ru/vacancy/123",
                        description="Own end-to-end IT delivery",
                    )
                    session.add(vacancy)
                    session.flush()

                    older = latest = None
                    if assessment_state != "no_legacy":
                        def evaluated(score):
                            return Evaluation(
                                vacancy_id=vacancy.id,
                                score=score,
                                decision="apply",
                                role_match=90,
                                seniority_match=90,
                                domain_match=80,
                                responsibility_match=90,
                                must_have_missing="[]",
                                nice_to_have_missing="[]",
                                strengths="[]",
                                gaps="[]",
                                red_flags="[]",
                                summary="Test assessment",
                                recommendation="Apply",
                                cover_letter="Test letter",
                                model="test",
                            )
                        older = evaluated(88)
                        latest = evaluated(90)
                        session.add_all([older, latest])
                        session.flush()

                    if assessment_state == "current":
                        assessment = SimpleNamespace(legacy_evaluation_id=latest.id)
                    elif assessment_state == "outdated_legacy":
                        assessment = SimpleNamespace(legacy_evaluation_id=older.id)
                    else:
                        assessment = None

                    with patch.object(
                        hh_collect, "current_clean_assessment", return_value=assessment
                    ) as lookup:
                        hh_collect.record_hh_discovery(
                            session, vacancy, source_label="HH_RECOMMENDATION",
                            account_key="clean",
                        )
                        hh_collect.record_hh_discovery(
                            session, vacancy, source_label="HH_SEARCH",
                            account_key="clean",
                        )
                        session.commit()

                    if assessment_state == "no_legacy":
                        lookup.assert_not_called()
                    elif assessment_state == "current":
                        self.assertEqual(lookup.call_count, 2)
                    else:
                        self.assertEqual(lookup.call_count, 1)

                    discovered = session.scalars(
                        select(HhVacancyDiscovery).where(
                            HhVacancyDiscovery.vacancy_id == vacancy.id,
                            HhVacancyDiscovery.account_key == "clean",
                        )
                    ).all()
                    self.assertEqual(
                        {row.discovery_source for row in discovered},
                        {"search", "recommendation"},
                    )

                    queued = session.scalars(
                        select(CleanLiveQueue).where(
                            CleanLiveQueue.vacancy_id == vacancy.id
                        )
                    ).all()
                    self.assertEqual(bool(queued), expected_queued)
                    self.assertLessEqual(len(queued), 1)
                finally:
                    session.close()
                    engine.dispose()
