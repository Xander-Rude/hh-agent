import hashlib
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from dashboard.server import create_app
from dashboard.sources import RESUME_ID, SnapshotReader, read_database, read_log, read_runtime, readonly_connection


NOW = datetime(2026, 9, 12, 10, tzinfo=UTC)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "data" / "hh_agent.db"

    def tearDown(self):
        self.temp.cleanup()

    def database(self):
        self.db.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db)
        connection.executescript("""
            CREATE TABLE vacancies (id INTEGER PRIMARY KEY);
            CREATE TABLE evaluations (id INTEGER PRIMARY KEY);
            CREATE TABLE applications (id INTEGER PRIMARY KEY, status TEXT, applied_at TEXT, selected_resume_id TEXT);
            INSERT INTO vacancies VALUES (1), (2);
            INSERT INTO evaluations VALUES (1);
        """)
        rows = [
            ("applied", "2026-09-11 20:59:59.999999", RESUME_ID),
            ("applied", "2026-09-11 21:00:00.000000", RESUME_ID),
            ("applied", "2026-09-12 09:59:00.000000", "another-resume"),
            ("approved", None, RESUME_ID),
            ("manual_required", "2026-09-12 09:00:00.000000", RESUME_ID),
            ("applied", "2026-09-12 21:00:00.000000", RESUME_ID),
        ]
        connection.executemany("INSERT INTO applications(status,applied_at,selected_resume_id) VALUES (?,?,?)", rows)
        connection.commit()
        return connection

    def test_missing_sources_are_na_and_never_created(self):
        with TestClient(create_app(self.root), base_url="http://localhost") as client:
            snapshot = client.get("/api/snapshot").json()
            self.assertIsNone(snapshot["database"]["apply_today"])
            self.assertIsNone(snapshot["database"]["counters"]["vacancies"])
            self.assertTrue(all(worker["heartbeat"] is None for worker in snapshot["workers"]))
            self.assertIsNone(client.get("/api/logs/apply_worker.log").json()["lines"])
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertNotIn("app.db", sys.modules)
        self.assertNotIn("background_common", sys.modules)

    def test_readonly_queries_and_moscow_date_boundaries(self):
        self.database().close()
        before = hashlib.sha256(self.db.read_bytes()).digest()
        snapshot = read_database(self.root, NOW)
        self.assertEqual(snapshot["availability"], "available")
        self.assertEqual(snapshot["counters"], {"vacancies": 2, "evaluations": 1, "applications": 6})
        self.assertEqual(snapshot["apply_today"], 2)
        self.assertEqual(snapshot["experiment"]["applied_since_start"], 1)
        self.assertIsNone(snapshot["experiment"]["invitations"])
        self.assertEqual(sum(group["count"] for group in snapshot["application_statuses"]), 6)
        with readonly_connection(self.db) as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM applications")
            # Even switching off the second guard cannot remove SQLite's mode=ro.
            connection.execute("PRAGMA query_only=OFF")
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("CREATE TABLE unwanted (id INTEGER)")
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).digest())

    def test_empty_is_zero_but_old_schema_is_na(self):
        connection = self.database()
        connection.execute("DELETE FROM applications")
        connection.commit()
        self.assertEqual(read_database(self.root, NOW)["apply_today"], 0)
        connection.executescript("DROP TABLE applications; CREATE TABLE applications (id INTEGER, status TEXT);")
        connection.close()
        snapshot = read_database(self.root, NOW)
        self.assertEqual(snapshot["availability"], "partial")
        self.assertEqual(snapshot["counters"]["applications"], 0)
        self.assertIsNone(snapshot["apply_today"])
        self.assertIsNone(snapshot["experiment"]["applied_since_start"])

    def test_busy_db_degrades_without_waiting_for_writer(self):
        connection = self.database()
        connection.execute("BEGIN EXCLUSIVE")
        try:
            started = time.monotonic()
            snapshot = read_database(self.root, NOW)
            self.assertLess(time.monotonic() - started, 1)
            self.assertEqual(snapshot["availability"], "unavailable")
            self.assertIsNone(snapshot["apply_today"])
        finally:
            connection.rollback()
            connection.close()

    def test_wal_reads_committed_data_without_blocking_writer(self):
        connection = self.database()
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("INSERT INTO vacancies VALUES (3)")
        connection.commit()
        try:
            self.assertEqual(read_database(self.root, NOW)["counters"]["vacancies"], 3)
            connection.execute("INSERT INTO vacancies VALUES (4)")
            self.assertEqual(read_database(self.root, NOW)["counters"]["vacancies"], 3)
            connection.commit()
            self.assertEqual(read_database(self.root, NOW)["counters"]["vacancies"], 4)
        finally:
            connection.close()

    def test_corrupt_db_is_unavailable(self):
        self.db.parent.mkdir()
        self.db.write_bytes(b"not a sqlite database")
        snapshot = read_database(self.root, NOW)
        self.assertEqual(snapshot["availability"], "unavailable")
        self.assertIsNone(snapshot["counters"]["vacancies"])

    def test_all_counters_share_one_snapshot_during_a_concurrent_commit(self):
        writer = self.database()
        writer.execute("PRAGMA journal_mode=WAL")
        original_connect = sqlite3.connect
        inserted = []

        def open_reader(*args, **kwargs):
            reader = original_connect(*args, **kwargs)

            def before_statement(sql):
                if sql.startswith("SELECT status,") and not inserted:
                    writer.execute("INSERT INTO applications(status,applied_at,selected_resume_id) VALUES (?,?,?)",
                                   ("applied", "2026-09-12 09:00:00", RESUME_ID))
                    writer.commit()
                    inserted.append(True)

            reader.set_trace_callback(before_statement)
            return reader

        try:
            with patch("dashboard.sources.sqlite3.connect", side_effect=open_reader):
                snapshot = read_database(self.root, NOW)
            self.assertEqual(inserted, [True])
            self.assertEqual(snapshot["counters"]["applications"], 6)
            self.assertEqual(sum(group["count"] for group in snapshot["application_statuses"]), 6)
            self.assertEqual(snapshot["apply_today"], 2)
            self.assertEqual(writer.execute("SELECT count(*) FROM applications").fetchone()[0], 7)
        finally:
            writer.close()

    def test_chart_data_uses_existing_rows_and_moscow_days(self):
        connection = self.database()
        connection.executescript("""
            ALTER TABLE vacancies ADD COLUMN source TEXT;
            UPDATE vacancies SET source='hh' WHERE id=1;
            UPDATE vacancies SET source='tbank' WHERE id=2;
            ALTER TABLE evaluations ADD COLUMN score INTEGER;
            UPDATE evaluations SET score=87;
        """)
        connection.close()
        snapshot = read_database(self.root, NOW)
        self.assertEqual({item["source"]: item["count"] for item in snapshot["source_counts"]}, {"hh": 1, "tbank": 1})
        self.assertEqual(snapshot["evaluation_scores"], [{"band": 8, "count": 1}])
        self.assertEqual(len(snapshot["application_daily"]), 14)
        self.assertEqual(snapshot["application_daily"][-1], {"day": "2026-09-12", "count": 2})
        self.assertEqual(snapshot["application_daily"][-2], {"day": "2026-09-11", "count": 1})
        self.assertEqual(sum(day["count"] for day in snapshot["application_daily"]), 3)

    def test_runtime_is_a_record_not_process_liveness(self):
        state_path = self.root / "data/runtime/apply.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text(json.dumps({
            "status": "running", "stage": "apply_dispatcher", "pid": 123,
            "updated_at": "2026-09-12T12:00:00+03:00",
            "finished_at": "2026-09-11T10:00:00+03:00", "exit_code": 0,
            "unknown_secret_field": "do-not-expose",
        }), encoding="utf-8")
        before = state_path.read_bytes()
        state = read_runtime(self.root, "apply", NOW)
        self.assertEqual(state["age_seconds"], 3600)
        self.assertIsNone(state["process_alive"])
        self.assertIsNone(state["heartbeat"])
        self.assertNotIn("finished_at", state["state"])
        self.assertNotIn("exit_code", state["state"])
        self.assertNotIn("unknown_secret_field", state["state"])
        self.assertEqual(before, state_path.read_bytes())
        for raw in ('{', '[]', '{"updated_at": "bad"}', '{"updated_at": "2026-09-12T09:00:00"}'):
            state_path.write_text(raw, encoding="utf-8")
            self.assertIsNone(read_runtime(self.root, "apply", NOW)["age_seconds"])
        state_path.write_bytes(b"x" * 65537)
        self.assertEqual(read_runtime(self.root, "apply", NOW)["availability"], "unavailable")

    def test_log_tail_is_bounded_raw_utf8_and_handles_rotation(self):
        path = self.root / "logs/apply_worker.log"
        path.parent.mkdir()
        path.write_text("x" * 70000 + "\n" + "\n".join(f"строка {i}" for i in range(300)), encoding="utf-8")
        before = hashlib.sha256(path.read_bytes()).digest()
        log = read_log(self.root, path.name, 5)
        self.assertEqual(log["lines"], [f"строка {i}" for i in range(295, 300)])
        self.assertTrue(log["truncated"])
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).digest())
        path.write_bytes(b"new\n\xff\n")
        self.assertEqual(read_log(self.root, path.name)["lines"], ["new", "\ufffd"])
        path.unlink()
        self.assertIsNone(read_log(self.root, path.name)["lines"])

    def test_api_serves_assets_and_rejects_writes_and_arbitrary_paths(self):
        with TestClient(create_app(self.root), base_url="http://localhost") as client:
            for path in ("/", "/static/style.css", "/static/theme.css", "/static/app.js",
                         "/static/fonts/manrope-latin.woff2", "/static/fonts/manrope-cyrillic.woff2"):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
            for method in ("post", "put", "patch", "delete"):
                self.assertEqual(getattr(client, method)("/api/snapshot").status_code, 405)
            self.assertEqual(client.get("/api/logs/.env").status_code, 404)
            self.assertEqual(client.get("/api/logs/apply_worker.log?lines=10000").status_code, 422)
            self.assertEqual(client.get("/api/snapshot", headers={"Host": "untrusted.example"}).status_code, 400)
            self.assertNotIn("access-control-allow-origin", client.get("/api/snapshot").headers)

    def test_database_cache_is_shared_and_expires(self):
        reader = SnapshotReader(self.root)
        with patch("dashboard.sources.read_database", return_value={"sentinel": True}) as read:
            reader.snapshot()
            reader.snapshot()
            self.assertEqual(read.call_count, 1)
            reader._expires = 0
            reader.snapshot()
            self.assertEqual(read.call_count, 2)


if __name__ == "__main__":
    unittest.main()
