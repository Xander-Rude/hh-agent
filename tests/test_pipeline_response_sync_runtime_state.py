import unittest
from unittest.mock import patch

import background_pipeline as pipeline


class EmbeddedResponseSyncRuntimeStateTests(unittest.TestCase):
    def _state_writes(self, write_state_mock):
        return [
            call.kwargs
            for call in write_state_mock.call_args_list
            if call.args
            and call.args[0] == pipeline.RESPONSE_SYNC_STATE
        ]

    def test_success_replaces_stale_state_with_ok(self) -> None:
        with (
            patch.object(pipeline, "run_python", return_value=0),
            patch.object(pipeline, "write_state") as write_state,
        ):
            code = pipeline._run_response_sync()

        self.assertEqual(code, 0)
        states = self._state_writes(write_state)
        self.assertGreaterEqual(len(states), 2)
        self.assertEqual(states[0]["status"], "running")
        self.assertEqual(states[0]["stage"], "response_sync_worker")
        self.assertEqual(states[0]["owner"], "pipeline")
        self.assertIsNone(states[0]["finished_at"])
        self.assertEqual(states[-1]["status"], "ok")
        self.assertEqual(states[-1]["stage"], "done")
        self.assertEqual(states[-1]["exit_code"], 0)
        self.assertIsNone(states[-1]["last_error"])

    def test_nonzero_worker_code_is_persisted_as_failed(self) -> None:
        with (
            patch.object(pipeline, "run_python", return_value=6),
            patch.object(pipeline, "write_state") as write_state,
        ):
            code = pipeline._run_response_sync()

        self.assertEqual(code, 6)
        state = self._state_writes(write_state)[-1]
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["stage"], "response_sync_worker")
        self.assertEqual(state["exit_code"], 6)
        self.assertIn("code=6", state["last_error"])

    def test_unexpected_exception_is_persisted_before_reraise(self) -> None:
        with (
            patch.object(
                pipeline,
                "run_python",
                side_effect=RuntimeError("boom"),
            ),
            patch.object(pipeline, "write_state") as write_state,
        ):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                pipeline._run_response_sync()

        state = self._state_writes(write_state)[-1]
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["stage"], "response_sync_worker")
        self.assertEqual(state["exit_code"], 99)
        self.assertIn("RuntimeError: boom", state["last_error"])


if __name__ == "__main__":
    unittest.main()
