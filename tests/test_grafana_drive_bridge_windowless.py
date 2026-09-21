from pathlib import Path
import importlib.util
import sys
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

    def test_manual_required_counter_deduplicates_application_ids(self) -> None:
        module_path = ROOT / "tools" / "grafana_drive_bridge.py"
        spec = importlib.util.spec_from_file_location("grafana_drive_bridge_test", module_path)
        module = importlib.util.module_from_spec(spec)
        self.assertIsNotNone(spec.loader)
        sys.modules["grafana_drive_bridge_test"] = module
        spec.loader.exec_module(module)
        records = [
            {"line": "[1/2] application_id=1835 result=manual_required"},
            {"line": "[RECOVERY] application_id=1835 result=manual_required"},
            {"line": "[2/2] application_id=1837 result=manual_required"},
        ]
        self.assertEqual(
            module.count_unique_application_ids(
                records, contains="manual_required"
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
