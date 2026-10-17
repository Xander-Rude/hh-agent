import unittest
from unittest.mock import patch

import background_shadow


class IndependentShadowWorkerTests(unittest.TestCase):
    def test_success_seeds_old_after_shadow_with_batch_limit(self):
        with (
            patch.dict(
                background_shadow.os.environ,
                {"HH_SHADOW_WORKER_ENABLED": "true", "HH_SHADOW_WORKER_BATCH_SIZE": "12"},
            ),
            patch.object(background_shadow, "AgentLock") as lock,
            patch.object(background_shadow, "run_python", side_effect=[0, 0]) as runner,
            patch.object(background_shadow, "append_log"),
        ):
            self.assertEqual(background_shadow.main(), 0)
        lock.assert_called_once_with(background_shadow.SHADOW_LOCK)
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(runner.call_args_list[0].args, ("clean_shadow.py",))
        self.assertEqual(
            runner.call_args_list[0].kwargs["extra_env"],
            {"CLEAN_SHADOW_MAX_ASSESSMENTS": "12"},
        )
        self.assertEqual(runner.call_args_list[1].args, ("old_auto_queue.py",))

    def test_error_still_seeds_old_queue_but_returns_error(self):
        with (
            patch.dict(background_shadow.os.environ, {"HH_SHADOW_WORKER_ENABLED": "true"}),
            patch.object(background_shadow, "AgentLock"),
            patch.object(background_shadow, "run_python", side_effect=[1, 0]) as runner,
            patch.object(background_shadow, "append_log"),
        ):
            self.assertEqual(background_shadow.main(), 1)
        self.assertEqual(runner.call_count, 2)

    def test_busy_lock_skips_without_scoring(self):
        with (
            patch.dict(background_shadow.os.environ, {"HH_SHADOW_WORKER_ENABLED": "true"}),
            patch.object(
                background_shadow, "AgentLock",
                side_effect=RuntimeError("agent_lock_busy"),
            ),
            patch.object(background_shadow, "run_python") as runner,
            patch.object(background_shadow, "append_log") as log,
        ):
            self.assertEqual(background_shadow.main(), 0)
        runner.assert_not_called()
        self.assertIn("SKIP", log.call_args.args[1])

    def test_disabled_exits_without_touching_lock(self):
        with (
            patch.dict(background_shadow.os.environ, {"HH_SHADOW_WORKER_ENABLED": "false"}),
            patch.object(background_shadow, "AgentLock") as lock,
            patch.object(background_shadow, "run_python") as runner,
            patch.object(background_shadow, "append_log"),
        ):
            self.assertEqual(background_shadow.main(), 0)
        lock.assert_not_called()
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
