from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import hh_response_analysis as analysis


class ClassificationTests(unittest.TestCase):
    def test_title_segments(self) -> None:
        cases = {
            "Senior Project Manager": analysis.SEGMENT_PMO,
            "Руководитель портфеля проектов": analysis.SEGMENT_PMO,
            "Delivery Manager": analysis.SEGMENT_DELIVERY,
            "Руководитель разработки": analysis.SEGMENT_DELIVERY,
            "Владелец продукта (Витрина продуктов)": analysis.SEGMENT_PRODUCT,
            "CTO": analysis.SEGMENT_PRODUCT,
            "Бизнес-аналитик": analysis.SEGMENT_OTHER,
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(analysis.classify_title(title), expected)

    def test_score_buckets(self) -> None:
        self.assertEqual(analysis.score_bucket(None), "No score")
        self.assertEqual(analysis.score_bucket(71), "<72")
        self.assertEqual(analysis.score_bucket(72), "72-79")
        self.assertEqual(analysis.score_bucket(80), "80-89")
        self.assertEqual(analysis.score_bucket(90), "90+")


class MetricsTests(unittest.TestCase):
    def test_silent_after_view_metric(self) -> None:
        rows = [
            {
                "viewed": 1,
                "rejected": 0,
                "substantive_contact": 0,
                "acknowledged": 1,
                "invited": 0,
                "silent_after_view": 1,
            },
            {
                "viewed": 1,
                "rejected": 1,
                "substantive_contact": 0,
                "acknowledged": 0,
                "invited": 0,
                "silent_after_view": 0,
            },
            {
                "viewed": 0,
                "rejected": 0,
                "substantive_contact": 0,
                "acknowledged": 0,
                "invited": 0,
                "silent_after_view": 0,
            },
        ]
        metric = analysis.metric_row(rows)
        self.assertEqual(metric.total, 3)
        self.assertEqual(metric.viewed, 2)
        self.assertEqual(metric.rejected, 1)
        self.assertEqual(metric.acknowledged, 1)
        self.assertEqual(metric.silent_after_view, 1)
        self.assertEqual(metric.not_viewed, 1)


class JoinTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.audit_path = root / "audit.sqlite"
        self.agent_path = root / "agent.sqlite"

        audit = sqlite3.connect(self.audit_path)
        audit.executescript(
            """
            CREATE TABLE responses (
                application_id TEXT PRIMARY KEY,
                negotiation_id TEXT,
                vacancy_id TEXT,
                vacancy_title TEXT,
                company TEXT,
                vacancy_url TEXT,
                chat_negotiation_url TEXT,
                applied_at TEXT,
                current_status TEXT,
                viewed_by_employer INTEGER,
                viewed_at TEXT,
                employer_replied INTEGER,
                first_reply_at TEXT,
                rejected INTEGER,
                invited INTEGER,
                active_dialog INTEGER,
                messages_count INTEGER,
                last_message_at TEXT,
                source TEXT,
                source_updated_at TEXT,
                raw_json TEXT,
                first_collected_at TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                detail_collected_at TEXT
            );
            CREATE TABLE response_events (
                event_id TEXT PRIMARY KEY,
                application_id TEXT NOT NULL,
                source_event_id TEXT,
                timestamp TEXT,
                author TEXT,
                event_type TEXT,
                text TEXT,
                raw_json TEXT,
                collected_at TEXT NOT NULL
            );
            """
        )
        audit.execute(
            """
            INSERT INTO responses (
                application_id, negotiation_id, vacancy_id, vacancy_title,
                company, applied_at, current_status, viewed_by_employer,
                rejected, invited, first_collected_at, collected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "neg-1",
                "neg-1",
                "137000001",
                "Senior Project Manager",
                "Example",
                "2026-09-10T12:00:00+03:00",
                "RESPONSE",
                1,
                0,
                0,
                "2026-09-10T12:01:00+03:00",
                "2026-09-19T12:00:00+03:00",
            ),
        )
        audit.execute(
            """
            INSERT INTO response_events (
                event_id, application_id, timestamp, event_type, collected_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "event-1",
                "neg-1",
                "2026-09-10T12:05:00+03:00",
                "employer_acknowledgement",
                "2026-09-19T12:00:00+03:00",
            ),
        )
        audit.commit()
        audit.close()

        agent = sqlite3.connect(self.agent_path)
        agent.executescript(
            """
            CREATE TABLE vacancies (
                id INTEGER PRIMARY KEY,
                hh_id TEXT,
                source TEXT,
                external_id TEXT,
                title TEXT,
                company TEXT,
                url TEXT,
                found_at TEXT
            );
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY,
                vacancy_id INTEGER,
                status TEXT,
                cover_letter TEXT,
                selected_resume_key TEXT,
                selected_resume_title TEXT,
                selected_resume_id TEXT,
                selected_resume_score INTEGER,
                applied_at TEXT,
                created_at TEXT
            );
            CREATE TABLE evaluations (
                id INTEGER PRIMARY KEY,
                vacancy_id INTEGER,
                score INTEGER,
                decision TEXT,
                role_match INTEGER,
                seniority_match INTEGER,
                domain_match INTEGER,
                responsibility_match INTEGER,
                selected_resume_key TEXT,
                selected_resume_title TEXT,
                selected_resume_id TEXT,
                selected_resume_score INTEGER,
                created_at TEXT
            );
            """
        )
        agent.execute(
            """
            INSERT INTO vacancies (
                id, hh_id, source, external_id, title, company, url, found_at
            ) VALUES (1, '137000001', 'hh', '137000001',
                      'Senior Project Manager', 'Example',
                      'https://hh.ru/vacancy/137000001',
                      '2026-09-10T10:00:00')
            """
        )
        agent.execute(
            """
            INSERT INTO applications (
                id, vacancy_id, status, cover_letter, selected_resume_title,
                selected_resume_id, applied_at, created_at
            ) VALUES (10, 1, 'applied', 'hello', 'PM Resume', 'resume-1',
                      '2026-09-10T12:00:00', '2026-09-10T11:55:00')
            """
        )
        agent.execute(
            """
            INSERT INTO evaluations (
                id, vacancy_id, score, decision, role_match, seniority_match,
                domain_match, responsibility_match, selected_resume_title,
                selected_resume_id, created_at
            ) VALUES (20, 1, 84, 'apply', 90, 88, 80, 82,
                      'PM Resume', 'resume-1', '2026-09-10T11:50:00')
            """
        )
        agent.commit()
        agent.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_build_merged_rows_joins_by_hh_vacancy_id(self) -> None:
        audit = analysis.open_ro(self.audit_path)
        agent = analysis.open_ro(self.agent_path)
        try:
            rows, meta = analysis.build_merged_rows(audit, agent)
        finally:
            audit.close()
            agent.close()

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(meta["matched_to_agent"], 1)
        self.assertEqual(row["agent_matched"], 1)
        self.assertEqual(row["agent_application_id"], 10)
        self.assertEqual(row["evaluation_score"], 84)
        self.assertEqual(row["score_bucket"], "80-89")
        self.assertEqual(row["has_cover_letter"], 1)
        self.assertEqual(row["selected_resume_title"], "PM Resume")
        self.assertEqual(row["acknowledged"], 1)
        self.assertEqual(row["substantive_contact"], 0)
        self.assertEqual(row["silent_after_view"], 1)


if __name__ == "__main__":
    unittest.main()
