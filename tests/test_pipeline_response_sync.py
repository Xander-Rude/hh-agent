import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = (ROOT / "background_pipeline.py").read_text(encoding="utf-8")
HARDEN = (ROOT / "deploy" / "harden_scheduled_tasks.ps1").read_text(
    encoding="utf-8"
)


class PipelineResponseSyncIntegrationTests(unittest.TestCase):
    def test_response_sync_runs_inside_main_pipeline(self) -> None:
        self.assertIn(
            'set_stage("response_sync")',
            PIPELINE,
        )
        self.assertIn(
            '"response_sync_worker.py"',
            PIPELINE,
        )
        self.assertIn(
            'log_filename="response_sync_worker.log"',
            PIPELINE,
        )

    def test_response_sync_is_fail_open_for_pipeline(self) -> None:
        self.assertIn(
            '"outcome collection is fail-open for pipeline"',
            PIPELINE,
        )

    def test_legacy_standalone_response_task_stays_disabled(self) -> None:
        self.assertIn(
            'Disable-ResponseSyncTask',
            HARDEN,
        )


if __name__ == "__main__":
    unittest.main()
