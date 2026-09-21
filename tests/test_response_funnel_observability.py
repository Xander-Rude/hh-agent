import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
for path in (ROOT, TOOLS):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

import grafana_drive_bridge as bridge  # noqa: E402
import grafana_drive_bridge_runner as runner  # noqa: E402
from hh_response_sync_worker import classify_negotiation_text  # noqa: E402


class ResponseClassificationTests(unittest.TestCase):
    def test_hh_invitation_is_workflow_state_not_interview(self):
        self.assertEqual(
            classify_negotiation_text("Работодатель пригласил вас продолжить общение"),
            "workflow_invitation",
        )

    def test_interview_collection_is_workflow_only(self):
        self.assertEqual(
            classify_negotiation_text(
                "Собеседование",
                collection_status="interview",
            ),
            "workflow_interview",
        )

    def test_discard_collection_is_rejection(self):
        self.assertEqual(
            classify_negotiation_text(
                "Отклик",
                collection_status="discard",
            ),
            "rejected",
        )

    def test_rejection_has_priority(self):
        self.assertEqual(
            classify_negotiation_text("Приглашение закрыто: работодатель отказал"),
            "rejected",
        )

    def test_viewed_state(self):
        self.assertEqual(
            classify_negotiation_text("Работодатель просмотрел ваше резюме"),
            "viewed",
        )


class ObservabilityCounterTests(unittest.TestCase):
    def test_manual_required_counts_unique_applications(self):
        records = [
            {"line": "[1/2] application_id=1835 result=manual_required"},
            {"line": "[RECOVERY] application_id=1835 result=manual_required"},
            {"line": "[2/2] application_id=1837 result=manual_required"},
            {"line": "manual_required without application id"},
        ]
        self.assertEqual(
            bridge.count_unique_application_ids(
                records,
                contains="manual_required",
            ),
            2,
        )


class SqliteSnapshotTests(unittest.TestCase):
    def test_backup_is_consistent_and_summary_separates_funnels(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.sqlite3"
            snapshot = root / "snapshot.sqlite3"
            summary = root / "summary.json"

            db = sqlite3.connect(source)
            db.executescript(
                """
                CREATE TABLE applications (
                    id INTEGER PRIMARY KEY,
                    status TEXT,
                    career_state TEXT
                );
                CREATE TABLE application_events (
                    id INTEGER PRIMARY KEY,
                    application_id INTEGER,
                    event_type TEXT,
                    is_human_contact INTEGER
                );
                INSERT INTO applications(id, status, career_state)
                VALUES
                    (1, 'applied', 'submitted'),
                    (2, 'manual_required', 'workflow_invitation'),
                    (3, 'applied', 'human_response');
                INSERT INTO application_events(
                    application_id, event_type, is_human_contact
                ) VALUES
                    (1, 'technical_status:applied', 0),
                    (2, 'career_state:workflow_invitation', 0),
                    (3, 'career_state:human_response', 1);
                """
            )
            db.commit()
            db.close()

            runner.create_sqlite_snapshot(source, snapshot)
            runner.write_db_summary(snapshot, summary)

            source_db = sqlite3.connect(source)
            source_db.execute(
                "INSERT INTO applications(id, status, career_state) "
                "VALUES (4, 'applied', 'submitted')"
            )
            source_db.commit()
            source_db.close()

            snap_db = sqlite3.connect(snapshot)
            try:
                self.assertEqual(
                    snap_db.execute(
                        "SELECT COUNT(*) FROM applications"
                    ).fetchone()[0],
                    3,
                )
                self.assertEqual(
                    snap_db.execute("PRAGMA quick_check").fetchone()[0],
                    "ok",
                )
            finally:
                snap_db.close()

            payload = json.loads(summary.read_text(encoding="utf-8"))
            self.assertEqual(payload["technical_statuses"]["applied"], 2)
            self.assertEqual(
                payload["career_states"]["workflow_invitation"],
                1,
            )
            self.assertEqual(payload["verified_human_contacts"], 1)
            self.assertIn(
                "not a verified human contact",
                payload["semantics"]["workflow_invitation"],
            )


if __name__ == "__main__":
    unittest.main()
