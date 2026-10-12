from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import grafana_drive_bridge_runner as runner  # noqa: E402
from tools import db_snapshot  # noqa: E402


class SnapshotSafetyTests(unittest.TestCase):
    def test_runtime_busy_detects_fresh_pipeline_and_apply(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            for name in ("pipeline.json", "apply.json"):
                f = path / name
                f.write_text(json.dumps({"status": "running"}), encoding="utf-8")
            self.assertCountEqual(
                runner.active_database_workers(path),
                ["pipeline.json", "apply.json"],
            )

    def test_stale_runtime_does_not_block_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            f = path / "pipeline.json"
            f.write_text(json.dumps({"status": "running"}), encoding="utf-8")
            old = time.time() - 600
            os.utime(f, (old, old))
            self.assertEqual(runner.active_database_workers(path), [])

    def test_completed_runtime_does_not_block_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "pipeline.json").write_text(
                json.dumps({"status": "ok"}), encoding="utf-8"
            )
            self.assertEqual(runner.active_database_workers(path), [])

    def test_online_backup_uses_bounded_page_steps(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            db_path = path / "live.db"
            target = path / "snapshot.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
                conn.execute("INSERT INTO t VALUES (1)")

            original_connect = sqlite3.connect
            options = {}

            class SourceProxy:
                def __init__(self, connection):
                    self.connection = connection

                def backup(self, target_connection, **kwargs):
                    options.update(kwargs)
                    return self.connection.backup(target_connection, **kwargs)

                def close(self):
                    self.connection.close()

            def connect(*args, **kwargs):
                connection = original_connect(*args, **kwargs)
                return SourceProxy(connection) if kwargs.get("uri") else connection

            with patch.object(db_snapshot.sqlite3, "connect", side_effect=connect):
                db_snapshot.create_sqlite_snapshot(db_path, target)

            self.assertEqual(options["pages"], 128)
            self.assertGreater(options["sleep"], 0)
            with original_connect(target) as conn:
                self.assertEqual(conn.execute("SELECT id FROM t").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
