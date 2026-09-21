from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.db_snapshot import create_snapshot_outputs


class DatabaseSnapshotTests(unittest.TestCase):
    def test_snapshot_is_consistent_and_export_is_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "hh_agent.db"
            state_dir = root / "state"

            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE vacancies (
                        id INTEGER PRIMARY KEY,
                        source TEXT,
                        external_id TEXT,
                        hh_id TEXT,
                        title TEXT,
                        company TEXT,
                        url TEXT,
                        salary_from INTEGER,
                        salary_to INTEGER,
                        salary_currency TEXT,
                        published_at TEXT,
                        found_at TEXT
                    );
                    CREATE TABLE applications (
                        id INTEGER PRIMARY KEY,
                        vacancy_id INTEGER,
                        status TEXT,
                        career_status TEXT,
                        applied_at TEXT,
                        created_at TEXT,
                        response_checked_at TEXT,
                        manual_recovery_attempts INTEGER,
                        manual_recovery_last_at TEXT
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
                        selected_resume_title TEXT,
                        selected_resume_score INTEGER,
                        created_at TEXT,
                        model TEXT
                    );
                    CREATE TABLE application_events (
                        id INTEGER PRIMARY KEY,
                        application_id INTEGER,
                        event_type TEXT,
                        source TEXT,
                        details TEXT,
                        observed_at TEXT
                    );
                    """
                )
                connection.execute(
                    """
                    INSERT INTO vacancies
                    VALUES (1, 'hh', '123', '123', 'Delivery Lead', 'Example',
                            'https://hh.ru/vacancy/123', NULL, NULL, 'RUB',
                            NULL, '2026-09-21 10:00:00')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO applications
                    VALUES (7, 1, 'applied', 'workflow_invited',
                            '2026-09-21 11:00:00', '2026-09-21 10:30:00',
                            '2026-09-21 12:00:00', 1, NULL)
                    """
                )
                connection.execute(
                    """
                    INSERT INTO evaluations
                    VALUES (3, 1, 81, 'apply', 90, 82, 60, 80,
                            'Руководитель проектов', 95,
                            '2026-09-21 10:20:00', 'test-model')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO application_events
                    VALUES (1, 7, 'career_workflow_invited', 'hh_negotiations',
                            '{"human_response": false}', '2026-09-21 12:00:00')
                    """
                )
                connection.commit()
            finally:
                connection.close()

            outputs = create_snapshot_outputs(state_dir, db_path=db_path)

            snapshot = sqlite3.connect(outputs["database"])
            try:
                self.assertEqual(
                    snapshot.execute(
                        "SELECT career_status FROM applications WHERE id=7"
                    ).fetchone()[0],
                    "workflow_invited",
                )
            finally:
                snapshot.close()

            payload = json.loads(
                outputs["funnel"].read_text(encoding="utf-8")
            )
            self.assertEqual(payload["counts"]["applications"], 1)
            self.assertEqual(
                payload["counts"]["career_status"]["workflow_invited"],
                1,
            )
            self.assertIn(
                "NOT a human response",
                payload["semantics"]["career_status"],
            )


if __name__ == "__main__":
    unittest.main()
