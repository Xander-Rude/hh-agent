from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GrafanaDriveBridgeOAuthGuardTests(unittest.TestCase):
    def test_setup_rejects_shared_rclone_google_oauth(self) -> None:
        source = (ROOT / "tools" / "setup_grafana_drive_bridge.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn('config", "redacted"', source)
        self.assertIn("Assert-CustomDriveOAuth", source)
        self.assertIn("Shared rclone client_id is not allowed", source)
        self.assertIn("client_secret", source)
        self.assertNotIn("config_is_local=true", source)


if __name__ == "__main__":
    unittest.main()
