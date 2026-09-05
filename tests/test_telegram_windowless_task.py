from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_telegram_task_launches_pythonw_directly() -> None:
    source = (ROOT / "install_tasks.ps1").read_text(encoding="utf-8-sig")
    section = source.split("# ---------------- Telegram bot ----------------", 1)[1]
    action = section.split("$TelegramTrigger", 1)[0]

    assert "$TelegramPython = Join-Path $Root \".venv\\Scripts\\pythonw.exe\"" in source
    assert "-Execute $TelegramPython" in action
    assert "-WorkingDirectory $Root" in action
    assert "-Execute $PowerShell" not in action


def test_pythonw_entry_redirects_output_to_telegram_log() -> None:
    source = (ROOT / "telegram_bot_entry.py").read_text(encoding="utf-8")

    assert 'LOG_PATH = Path(__file__).resolve().parent / "logs" / "telegram.log"' in source
    assert "def configure_windowless_output()" in source
    assert "if sys.stdout is None:" in source
    assert "if sys.stderr is None:" in source
    assert "configure_windowless_output()" in source
