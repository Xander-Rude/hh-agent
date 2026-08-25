from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
STATE_DIR = DATA_DIR / "runtime"

LOCK_PATH = DATA_DIR / "hh_agent_background.lock"

PIPELINE_STATE = STATE_DIR / "pipeline.json"
APPLY_STATE = STATE_DIR / "apply.json"
TELEGRAM_STATE = STATE_DIR / "telegram.json"
RESUME_RAISE_STATE = STATE_DIR / "resume_raise.json"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(
        timespec="seconds"
    )


def ensure_runtime_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def write_state(
    path: Path,
    **values: Any,
) -> None:
    ensure_runtime_dirs()

    current: dict[str, Any] = {}

    if path.exists():
        try:
            current = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )
        except Exception:
            current = {}

    current.update(values)
    current["updated_at"] = now_iso()

    temp = path.with_suffix(
        path.suffix + ".tmp"
    )

    temp.write_text(
        json.dumps(
            current,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temp.replace(path)


def read_state(
    path: Path,
) -> dict[str, Any]:
    if not path.exists():
        return {}

    try:
        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return {}


def append_log(
    filename: str,
    message: str,
) -> None:
    ensure_runtime_dirs()

    path = LOG_DIR / filename

    with path.open(
        "a",
        encoding="utf-8",
    ) as file:
        file.write(
            f"[{now_iso()}] {message}\n"
        )
        file.flush()


class AgentLock:
    """
    Cross-process Windows lock.

    Collector and apply worker share one Playwright persistent profile,
    so only one of them may run at a time.
    """

    def __init__(
        self,
        path: Path = LOCK_PATH,
    ):
        self.path = path
        self.handle = None

    def __enter__(self):
        ensure_runtime_dirs()

        if os.name != "nt":
            self.handle = self.path.open(
                "a+",
                encoding="utf-8",
            )
            return self

        import msvcrt

        self.handle = self.path.open(
            "a+b"
        )

        self.handle.seek(0)

        try:
            msvcrt.locking(
                self.handle.fileno(),
                msvcrt.LK_NBLCK,
                1,
            )
        except OSError:
            self.handle.close()
            self.handle = None
            raise RuntimeError(
                "agent_lock_busy"
            )

        return self

    def __exit__(
        self,
        exc_type,
        exc,
        tb,
    ):
        if self.handle is None:
            return

        if os.name == "nt":
            import msvcrt

            try:
                self.handle.seek(0)
                msvcrt.locking(
                    self.handle.fileno(),
                    msvcrt.LK_UNLCK,
                    1,
                )
            except OSError:
                pass

        self.handle.close()
        self.handle = None


def _kill_process_tree(
    pid: int,
) -> None:
    """
    Kill a child process and all of its descendants.

    On Windows this is important because Playwright can leave Chromium
    descendants alive after the Python worker itself gets stuck.
    """
    if os.name == "nt":
        subprocess.run(
            [
                "taskkill",
                "/PID",
                str(pid),
                "/T",
                "/F",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return

    try:
        os.kill(pid, 9)
    except OSError:
        pass


def run_python(
    script_name: str,
    *,
    extra_env: dict[str, str] | None = None,
    log_filename: str | None = None,
    timeout_seconds: int | None = None,
) -> int:
    """
    Run a project Python script and stream stdout/stderr into a UTF-8 log.

    timeout_seconds is a supervisor-level hard timeout. If the child hangs,
    the whole process tree is killed so AgentLock can be released.
    """
    ensure_runtime_dirs()

    python_exe = (
        ROOT
        / ".venv"
        / "Scripts"
        / "python.exe"
    )

    script = ROOT / script_name

    if not python_exe.exists():
        raise FileNotFoundError(
            f"Python venv not found: {python_exe}"
        )

    if not script.exists():
        raise FileNotFoundError(
            f"Script not found: {script}"
        )

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    if extra_env:
        env.update(extra_env)

    log_path = (
        LOG_DIR
        / (
            log_filename
            or f"{Path(script_name).stem}.log"
        )
    )

    with log_path.open(
        "a",
        encoding="utf-8",
        buffering=1,
    ) as log:
        log.write(
            f"\n[{now_iso()}] START {script_name}\n"
        )
        log.flush()

        creation_flags = (
            (
                subprocess.CREATE_NO_WINDOW
                | subprocess.BELOW_NORMAL_PRIORITY_CLASS
            )
            if os.name == "nt"
            else 0
        )

        process = subprocess.Popen(
            [
                str(python_exe),
                "-u",
                str(script),
            ],
            cwd=str(ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creation_flags,
        )

        try:
            code = int(
                process.wait(
                    timeout=timeout_seconds
                )
            )
        except subprocess.TimeoutExpired:
            log.write(
                f"[{now_iso()}] TIMEOUT {script_name} "
                f"after {timeout_seconds}s; killing process tree pid={process.pid}\n"
            )
            log.flush()

            _kill_process_tree(
                process.pid
            )

            try:
                process.wait(
                    timeout=15
                )
            except subprocess.TimeoutExpired:
                pass

            code = 124

        log.write(
            f"[{now_iso()}] END {script_name} code={code}\n"
        )
        log.flush()

    return code
