import time
import unittest
from unittest.mock import patch

import background_pipeline as pipeline


class BackgroundPipelineCollectHeartbeatTests(unittest.TestCase):
    def test_hh_collect_has_no_wall_clock_timeout_and_pulses_state(self) -> None:
        calls: list[str] = []

        def fake_run_python(script_name, **kwargs):
            self.assertEqual(script_name, "hh_collect_optimized.py")
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
            self.assertEqual(pipeline._run_hh_collect(), 0)

        self.assertIn("collect_hh", calls)


if __name__ == "__main__":
    unittest.main()
