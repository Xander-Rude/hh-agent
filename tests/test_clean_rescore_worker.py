from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch

import clean_rescore_7d_worker as worker


class CleanRescoreWorkerLockTests(unittest.TestCase):
    def test_processing_command_refuses_second_worker(self) -> None:
        lock = MagicMock()
        lock.__enter__.side_effect = RuntimeError("agent_lock_busy")

        with (
            patch.object(worker, "AgentLock", return_value=lock),
            patch.object(sys, "argv", ["clean_rescore_7d_worker.py", "--run-id", "1"]),
        ):
            self.assertEqual(worker.main(), 7)

    def test_pure_summary_does_not_take_worker_lock(self) -> None:
        lock_cls = MagicMock()

        with (
            patch.object(worker, "AgentLock", lock_cls),
            patch.object(worker, "init_db"),
            patch.object(worker, "get_rescore_summary", return_value={"run_id": 1}),
            patch.object(sys, "argv", [
                "clean_rescore_7d_worker.py",
                "--run-id",
                "1",
                "--summary-only",
            ]),
        ):
            self.assertEqual(worker.main(), 0)
            lock_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
