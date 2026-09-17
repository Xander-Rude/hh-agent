import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import background_pipeline as pipeline


class BackgroundPipelineHeartbeatTests(unittest.TestCase):
    def test_hh_collect_has_supervisor_timeout_and_pulses_state(self) -> None:
        calls: list[str] = []

        def fake_run_python(script_name, **kwargs):
            self.assertEqual(script_name, "hh_collect_optimized.py")
            self.assertEqual(
                kwargs["timeout_seconds"],
                pipeline.HH_COLLECT_SUPERVISOR_TIMEOUT_SECONDS,
            )
            self.assertGreater(kwargs["timeout_seconds"], 0)
            time.sleep(0.04)
            return 0

        with (
            patch.object(pipeline, "PIPELINE_HEARTBEAT_SECONDS", 0.01),
            patch.object(
                pipeline,
                "_collector_progress_snapshot",
                return_value=("[SEARCH 7/36] Technology Director", "2026-09-18T01:23:00+03:00"),
            ),
            patch.object(
                pipeline,
                "set_stage",
                side_effect=lambda stage, **kwargs: calls.append(stage),
            ),
            patch.object(pipeline, "run_python", side_effect=fake_run_python),
        ):
            self.assertEqual(pipeline._run_hh_collect(), 0)

        self.assertIn("collect_hh", calls)

    def test_process_has_no_wall_clock_timeout_and_pulses_state(self) -> None:
        calls: list[str] = []

        def fake_run_python(script_name, **kwargs):
            self.assertEqual(script_name, "process_vacancies.py")
            self.assertIsNone(kwargs["timeout_seconds"])
            time.sleep(0.04)
            return 0

        with (
            patch.object(pipeline, "PIPELINE_HEARTBEAT_SECONDS", 0.01),
            patch.object(
                pipeline,
                "set_stage",
                side_effect=lambda stage, **kwargs: calls.append(stage),
            ),
            patch.object(pipeline, "run_python", side_effect=fake_run_python),
        ):
            self.assertEqual(pipeline._run_process(), 0)

        self.assertIn("process", calls)

    def test_running_stage_clears_stale_terminal_fields(self) -> None:
        with patch.object(pipeline, "write_state") as write_state:
            pipeline.set_stage("collect_hh")

        kwargs = write_state.call_args.kwargs
        self.assertEqual(kwargs["status"], "running")
        self.assertIsNone(kwargs["finished_at"])
        self.assertIsNone(kwargs["exit_code"])
        self.assertIsNone(kwargs["last_error"])

    def test_active_other_pipeline_state_is_preserved_on_lock_busy(self) -> None:
        with patch.object(pipeline.os, "getpid", return_value=123):
            self.assertTrue(
                pipeline._preserve_active_pipeline_state(
                    {"status": "running", "pid": 999}
                )
            )
            self.assertTrue(
                pipeline._preserve_active_pipeline_state(
                    {"status": "starting", "pid": None}
                )
            )
            self.assertFalse(
                pipeline._preserve_active_pipeline_state(
                    {"status": "starting", "pid": 123}
                )
            )
            self.assertFalse(
                pipeline._preserve_active_pipeline_state(
                    {"status": "ok", "pid": 999}
                )
            )

    def test_lock_busy_does_not_overwrite_active_pipeline_state(self) -> None:
        with (
            patch.object(pipeline.os, "getpid", return_value=123),
            patch.object(
                pipeline,
                "read_state",
                return_value={"status": "running", "pid": 999},
            ),
            patch.object(pipeline, "write_state") as write_state,
            patch.object(pipeline, "log"),
        ):
            pipeline._record_lock_busy(started_at="2026-09-18T01:22:26+03:00")

        write_state.assert_not_called()

    def test_lock_busy_records_skip_when_this_attempt_owns_starting_state(self) -> None:
        with (
            patch.object(pipeline.os, "getpid", return_value=123),
            patch.object(
                pipeline,
                "read_state",
                return_value={"status": "starting", "pid": 123},
            ),
            patch.object(pipeline, "write_state") as write_state,
        ):
            pipeline._record_lock_busy(started_at="2026-09-18T01:22:26+03:00")

        kwargs = write_state.call_args.kwargs
        self.assertEqual(kwargs["status"], "skipped")
        self.assertEqual(kwargs["stage"], "lock")
        self.assertEqual(kwargs["last_error"], "agent_lock_busy")

    def test_collector_progress_snapshot_tracks_real_log_activity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            (log_dir / "collector.log").write_text(
                "\n".join(
                    [
                        "[SEARCH 7/36] Technology Director",
                        "[PAGE 1/2]",
                        "[VACANCY 3/12] [SEARCH:Technology Director] https://hh.ru/vacancy/123",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.object(pipeline, "LOG_DIR", log_dir):
                progress, progress_at = pipeline._collector_progress_snapshot()

        self.assertIn("[VACANCY 3/12]", progress or "")
        self.assertIsNotNone(progress_at)


if __name__ == "__main__":
    unittest.main()
