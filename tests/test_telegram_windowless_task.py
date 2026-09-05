from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TelegramWindowlessTaskTests(unittest.TestCase):
    def test_telegram_task_launches_pythonw_directly(self) -> None:
        source = (ROOT / "install_tasks.ps1").read_text(encoding="utf-8-sig")
        section = source.split("# ---------------- Telegram bot ----------------", 1)[1]
        action = section.split("$TelegramTrigger", 1)[0]

        self.assertIn(
            '$TelegramPython = Join-Path $Root ".venv\\Scripts\\pythonw.exe"',
            source,
        )
        self.assertIn("-Execute $TelegramPython", action)
        self.assertIn("-WorkingDirectory $Root", action)
        self.assertNotIn("-Execute $PowerShell", action)

    def test_pythonw_entry_redirects_output_to_telegram_log(self) -> None:
        source = (ROOT / "telegram_bot_entry.py").read_text(encoding="utf-8")

        self.assertIn(
            'LOG_PATH = Path(__file__).resolve().parent / "logs" / "telegram.log"',
            source,
        )
        self.assertIn("def configure_windowless_output()", source)
        self.assertIn("if sys.stdout is None:", source)
        self.assertIn("if sys.stderr is None:", source)
        self.assertIn("configure_windowless_output()", source)


if __name__ == "__main__":
    unittest.main()
