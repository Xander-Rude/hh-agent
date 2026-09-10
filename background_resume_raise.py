from __future__ import annotations

import os
import re
from pathlib import Path

from background_common import (
    AgentLock,
    LOG_DIR,
    RESUME_RAISE_STATE,
    append_log,
    now_iso,
    read_state,
    run_python,
    write_state,
)
from resume_raise_schedule import (
    local_now,
    next_raise_at_from_text,
    retry_at,
    should_run_for_due_at,
    to_iso,
)


WORKER_SCRIPT = "resume_raise_worker_v2.py"
WORKER_LOG_FILENAME = "resume_raise_worker.log"
WORKER_LOG_PATH = LOG_DIR / WORKER_LOG_FILENAME
RAISED_RE = re.compile(r"\[DONE\]\s+Поднятий выполнено:\s*(\d+)", re.IGNORECASE)


def log(message: str) -> None:
    print(f"[{now_iso()}] {message}", flush=True)
    append_log("resume_raise_supervisor.log", message)


def _log_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _read_log_tail(path: Path, offset: int) -> str:
    try:
        with path.open("rb") as file:
            file.seek(offset)
            return file.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _set_next_due(due_at, reason: str) -> None:
    due_iso = to_iso(due_at)
    write_state(
        RESUME_RAISE_STATE,
        next_due_at=due_iso,
        schedule_reason=reason,
    )
    log(f"NEXT RESUME RAISE CHECK: {due_iso} ({reason})")


def _plan_next_run(code: int, worker_output: str) -> None:
    now = local_now()

    if code == 0:
        advertised = next_raise_at_from_text(worker_output, now=now)
        if advertised is not None:
            # If HH still advertises a clock time that has just passed, the SPA
            # probably has not rendered the CTA yet. Avoid a tight loop and let
            # the lightweight five-minute trigger retry shortly.
            if advertised <= now:
                _set_next_due(retry_at(5, now=now), "hh_due_cta_not_rendered")
            else:
                _set_next_due(advertised, "hh_advertised_time")
            return

        raised = [int(value) for value in RAISED_RE.findall(worker_output)]
        if raised and max(raised) > 0:
            # Right after a successful click the worker does not dump the new
            # HH clock times. Refresh once shortly afterwards; that run will
            # learn the exact next free-raise time and sleep until then.
            _set_next_due(retry_at(10, now=now), "post_raise_refresh")
            return

        _set_next_due(retry_at(15, now=now), "no_hh_due_time_found")
        return

    if code == 2:
        _set_next_due(retry_at(5, now=now), "network_retry")
    elif code in {5, 124}:
        _set_next_due(retry_at(5, now=now), "worker_retry")
    elif code in {3, 6}:
        _set_next_due(retry_at(30, now=now), "hh_antibot_backoff")
    elif code == 4:
        _set_next_due(retry_at(15, now=now), "hh_session_retry")
    else:
        _set_next_due(retry_at(15, now=now), f"worker_exit_{code}_retry")


def main() -> int:
    # The Windows task wakes this supervisor every five minutes. Most wakeups
    # stop here without starting Playwright: the expensive worker runs only
    # when HH's advertised time (or a retry deadline) has arrived.
    state = read_state(RESUME_RAISE_STATE)
    if not should_run_for_due_at(state.get("next_due_at")):
        return 0

    write_state(
        RESUME_RAISE_STATE,
        status="starting",
        stage="init",
        started_at=now_iso(),
        finished_at=None,
        exit_code=None,
        pid=os.getpid(),
        last_error=None,
    )

    log("RESUME RAISE START")

    try:
        with AgentLock():
            write_state(
                RESUME_RAISE_STATE,
                status="running",
                stage="resume_raise_worker",
                finished_at=None,
                exit_code=None,
                pid=os.getpid(),
                last_error=None,
            )

            log_offset = _log_size(WORKER_LOG_PATH)
            code = run_python(
                WORKER_SCRIPT,
                extra_env={
                    "HH_RESUME_RAISE_HEADLESS": "true",
                    # A short DNS hiccup must not cost an entire raise cycle.
                    # The worker retries locally first; after that the smart
                    # scheduler retries again five minutes later.
                    "HH_RESUME_RAISE_NAV_RETRIES": "5",
                    "HH_RESUME_RAISE_NAV_RETRY_DELAY_MS": "10000",
                },
                log_filename=WORKER_LOG_FILENAME,
                timeout_seconds=5 * 60,
            )
            worker_output = _read_log_tail(WORKER_LOG_PATH, log_offset)
            _plan_next_run(code, worker_output)

    except RuntimeError as exc:
        if str(exc) == "agent_lock_busy":
            log("SKIP: another HH background job is still running")

            write_state(
                RESUME_RAISE_STATE,
                status="skipped",
                stage="lock",
                finished_at=now_iso(),
                exit_code=0,
                last_error="agent_lock_busy",
            )
            # Keep next_due_at untouched. It is already due, so the lightweight
            # task will try again on its next five-minute wakeup.
            return 0

        raise

    if code != 0:
        if code == 4:
            message = (
                "HH session expired — запусти hh_login.py "
                "для повторной авторизации общего browser-profile"
            )
        else:
            message = f"{WORKER_SCRIPT} failed with code={code}"

        log(message)

        write_state(
            RESUME_RAISE_STATE,
            status="failed",
            stage="resume_raise_worker",
            finished_at=now_iso(),
            exit_code=code,
            last_error=message,
        )
        return code

    write_state(
        RESUME_RAISE_STATE,
        status="ok",
        stage="done",
        finished_at=now_iso(),
        exit_code=0,
        last_error=None,
    )

    log("RESUME RAISE DONE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"

        write_state(
            RESUME_RAISE_STATE,
            status="failed",
            stage="supervisor",
            finished_at=now_iso(),
            exit_code=99,
            last_error=message,
        )

        append_log(
            "resume_raise_supervisor.log",
            "FATAL " + message,
        )

        raise
