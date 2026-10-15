from __future__ import annotations

import os
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import background_cover_letters
import background_pipeline


class IndependentCoverTests(unittest.TestCase):
    def test_cover_worker_holds_dedicated_lock(self):
        with (
            patch.object(background_cover_letters, "AgentLock") as lock,
            patch.object(background_cover_letters, "run_python", return_value=0) as run,
        ):
            self.assertEqual(background_cover_letters.main(), 0)
            lock.assert_called_once_with(background_cover_letters.COVER_LOCK)
            run.assert_called_once_with(
                "cover_letter_worker.py",
                log_filename="cover_letter_worker.log",
                timeout_seconds=35 * 60,
            )

    def test_parallel_cover_worker_is_skipped(self):
        with (
            patch.object(
                background_cover_letters,
                "AgentLock",
                side_effect=RuntimeError("agent_lock_busy"),
            ),
            patch.object(background_cover_letters, "run_python") as run,
            patch.object(background_cover_letters, "append_log") as log,
        ):
            self.assertEqual(background_cover_letters.main(), 0)
            run.assert_not_called()
            log.assert_called_once()

    def test_non_lock_error_is_not_hidden(self):
        with patch.object(
            background_cover_letters,
            "AgentLock",
            side_effect=RuntimeError("storage_error"),
        ):
            with self.assertRaisesRegex(RuntimeError, "storage_error"):
                background_cover_letters.main()

    def test_processor_failure_keeps_status_failed_but_runs_cover(self):
        unauthenticated = SimpleNamespace(
            authenticated=False,
            identity_verified=False,
            reason="test_unauthenticated",
            final_url=None,
        )
        with (
            patch.dict(
                os.environ,
                {"HH_PIPELINE_ENABLED": "true", "CLEAN_SHADOW_ENABLED": "true"},
            ),
            patch.object(
                background_pipeline, "_agent_lock_with_retry",
                return_value=nullcontext(),
            ),
            patch.object(background_pipeline, "is_captcha_paused", return_value=False),
            patch.object(background_pipeline, "check_hh_session", return_value=unauthenticated),
            patch.object(background_pipeline, "run_python", return_value=0),
            patch.object(background_pipeline, "_run_process", return_value=2),
            patch.object(background_pipeline, "_run_cover_letters", return_value=0) as cover,
            patch.object(background_pipeline, "write_state") as state,
            patch.object(background_pipeline, "set_stage"),
            patch.object(background_pipeline, "notify"),
            patch.object(background_pipeline, "log"),
        ):
            self.assertEqual(background_pipeline.main(), 2)
            cover.assert_called_once()
            self.assertEqual(state.call_args.kwargs["status"], "failed")
            self.assertEqual(state.call_args.kwargs["stage"], "process")
            self.assertEqual(state.call_args.kwargs["exit_code"], 2)

    def test_worker_task_installed_on_deploy(self):
        root = Path(__file__).resolve().parents[1]
        installer = (root / "deploy" / "install_cover_task.ps1").read_text(
            encoding="utf-8"
        )
        hardener = (root / "deploy" / "harden_scheduled_tasks.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("HH Agent - Cover Letters", installer)
        self.assertIn("background_cover_letters.py", installer)
        self.assertIn("MultipleInstances IgnoreNew", installer)
        self.assertIn("RepetitionInterval", installer)
        self.assertIn("install_cover_task.ps1", hardener)


if __name__ == "__main__":
    unittest.main()
