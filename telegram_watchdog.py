from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path

from background_common import ROOT, TELEGRAM_STATE, append_log, read_state, write_state


TELEGRAM_TASK_NAME = "HH Agent - Telegram"
WATCHDOG_STATE = ROOT / "data" / "runtime" / "telegram_watchdog.json"
WATCHDOG_LOG = "telegram_watchdog.log"
DEFAULT_STALE_SECONDS = 180


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed
    except ValueError:
        return None


def heartbeat_age_seconds(state: dict, *, now: datetime | None = None) -> float | None:
    updated_at = _parse_timestamp(state.get("updated_at"))
    if updated_at is None:
        return None

    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()
    return max(0.0, (current - updated_at).total_seconds())


def is_heartbeat_stale(
    state: dict,
    *,
    now: datetime | None = None,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
) -> bool:
    if state.get("status") != "running":
        return True
    age = heartbeat_age_seconds(state, now=now)
    return age is None or age > stale_seconds


def _run_powershell(script: str) -> subprocess.CompletedProcess[str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
        check=False,
    )


def is_telegram_process_running(pid: object) -> bool:
    try:
        numeric_pid = int(pid)
    except (TypeError, ValueError):
        return False
    if numeric_pid <= 0:
        return False

    if os.name != "nt":
        try:
            os.kill(numeric_pid, 0)
            return True
        except OSError:
            return False

    script = rf"""
$proc = Get-CimInstance Win32_Process -Filter "ProcessId = {numeric_pid}" -ErrorAction SilentlyContinue
if ($proc -and $proc.CommandLine -match 'telegram_bot_entry\.py') {{ exit 0 }}
exit 1
"""
    return _run_powershell(script).returncode == 0


def restart_telegram_bot(reason: str) -> None:
    if os.name != "nt":
        raise RuntimeError("Telegram watchdog recovery is configured for Windows only.")

    append_log(WATCHDOG_LOG, f"recovery requested: {reason}")
    write_state(
        WATCHDOG_STATE,
        status="restarting",
        reason=reason,
    )

    script = rf"""
$ErrorActionPreference = 'Stop'
Stop-ScheduledTask -TaskName '{TELEGRAM_TASK_NAME}' -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process |
    Where-Object {{
        $_.Name -match '^python(w)?\.exe$' -and
        $_.CommandLine -match 'telegram_bot_entry\.py'
    }} |
    ForEach-Object {{
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }}
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName '{TELEGRAM_TASK_NAME}' -ErrorAction Stop
"""
    result = _run_powershell(script)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown PowerShell error").strip()
        write_state(
            WATCHDOG_STATE,
            status="error",
            reason=reason,
            last_error=detail,
        )
        append_log(WATCHDOG_LOG, f"recovery failed: {detail}")
        raise RuntimeError(detail)

    write_state(
        WATCHDOG_STATE,
        status="restarted",
        reason=reason,
        last_error=None,
    )
    append_log(WATCHDOG_LOG, "Telegram task restarted successfully")


def check_once(*, stale_seconds: int | None = None) -> str:
    threshold = stale_seconds or int(
        os.getenv("TELEGRAM_WATCHDOG_STALE_SECONDS", str(DEFAULT_STALE_SECONDS))
    )
    state = read_state(TELEGRAM_STATE)

    if not state:
        reason = "telegram_state_missing"
    elif is_heartbeat_stale(state, stale_seconds=threshold):
        age = heartbeat_age_seconds(state)
        reason = (
            f"heartbeat_stale age={age:.0f}s threshold={threshold}s"
            if age is not None
            else "heartbeat_missing_or_invalid"
        )
    elif not is_telegram_process_running(state.get("pid")):
        reason = f"telegram_process_missing pid={state.get('pid')}"
    else:
        age = heartbeat_age_seconds(state)
        write_state(
            WATCHDOG_STATE,
            status="healthy",
            telegram_pid=state.get("pid"),
            telegram_updated_at=state.get("updated_at"),
            heartbeat_age_seconds=round(age or 0.0, 1),
            last_error=None,
        )
        return "healthy"

    restart_telegram_bot(reason)
    return reason


def main() -> int:
    try:
        check_once()
        return 0
    except Exception as exc:
        append_log(WATCHDOG_LOG, f"watchdog error: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
