import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import background_pipeline as pipeline


class BackgroundPipelineHeartbeatTests(unittest.TestCase):
    def test_pipeline_enabled_false_exits_before_agent_lock(self) -> None:
        with (
            patch.dict(
                pipeline.os.environ,
                {"HH_PIPELINE_ENABLED": "false"},
                clear=False,
            ),
            patch.object(pipeline, "AgentLock") as agent_lock,
            patch.object(pipeline, "write_state") as write_state,
            patch.object(pipeline, "log") as log,
            patch.object(pipeline, "notify") as notify,
        ):
            self.assertEqual(pipeline.main(), 0)

        agent_lock.assert_not_called()
        notify.assert_not_called()
        self.assertEqual(write_state.call_args.kwargs["status"], "skipped")
        self.assertEqual(write_state.call_args.kwargs["stage"], "disabled")
        self.assertEqual(write_state.call_args.kwargs["exit_code"], 0)
        log.assert_called_once_with("PIPELINE PAUSED: HH_PIPELINE_ENABLED=false")

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

    def test_hh_collect_retries_transient_network_failure(self) -> None:
        with (
            patch.object(pipeline, "HH_COLLECT_TRANSIENT_RETRIES", 2),
            patch.object(pipeline, "HH_COLLECT_RETRY_DELAY_SECONDS", 0),
            patch.object(
                pipeline,
                "_run_hh_collect",
                side_effect=[1, 0],
            ) as run_collect,
            patch.object(
                pipeline,
                "_collector_failure_is_transient_network",
                return_value=True,
            ),
            patch.object(pipeline, "set_stage"),
            patch.object(pipeline, "log"),
        ):
            self.assertEqual(pipeline._run_hh_collect_with_retry(), 0)

        self.assertEqual(run_collect.call_count, 2)

    def test_hh_collect_does_not_retry_non_network_failure(self) -> None:
        with (
            patch.object(pipeline, "HH_COLLECT_TRANSIENT_RETRIES", 2),
            patch.object(
                pipeline,
                "_run_hh_collect",
                return_value=1,
            ) as run_collect,
            patch.object(
                pipeline,
                "_collector_failure_is_transient_network",
                return_value=False,
            ),
        ):
            self.assertEqual(pipeline._run_hh_collect_with_retry(), 1)

        run_collect.assert_called_once()

    def test_transient_network_failure_is_detected_from_collector_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            (log_dir / "collector.log").write_text(
                "Page.goto: net::ERR_CONNECTION_TIMED_OUT at https://hh.ru/search/vacancy",
                encoding="utf-8",
            )

            with patch.object(pipeline, "LOG_DIR", log_dir):
                self.assertTrue(pipeline._collector_failure_is_transient_network())

    def test_agent_lock_retries_short_collision(self) -> None:
        attempts = 0

        class FakeLock:
            def __enter__(self):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise RuntimeError("agent_lock_busy")
                return self

            def __exit__(self, exc_type, exc, tb):
                return None

        with (
            patch.object(pipeline, "AgentLock", side_effect=FakeLock),
            patch.object(pipeline, "PIPELINE_LOCK_RETRY_TIMEOUT_SECONDS", 10),
            patch.object(pipeline, "PIPELINE_LOCK_RETRY_INTERVAL_SECONDS", 1),
            patch.object(pipeline.time, "monotonic", side_effect=[0, 0]),
            patch.object(pipeline.time, "sleep") as sleep,
            patch.object(pipeline, "log"),
        ):
            with pipeline._agent_lock_with_retry():
                pass

        self.assertEqual(attempts, 2)
        sleep.assert_called_once_with(1)

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
