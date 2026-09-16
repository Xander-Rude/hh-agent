from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GrafanaDriveBridgeWindowlessTests(unittest.TestCase):
    def test_scheduled_runner_hides_rclone_children(self) -> None:
        source = (ROOT / "tools" / "grafana_drive_bridge_runner.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("subprocess.CREATE_NO_WINDOW", source)
        self.assertIn("bridge.rclone_upload = hidden_rclone_upload", source)
        self.assertIn("proc = run_rclone(", source)
        self.assertNotIn("proc = subprocess.run(\n        [\n            str(rclone_path)", source)


if __name__ == "__main__":
    unittest.main()
