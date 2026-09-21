from __future__ import annotations

import os

from background_common import (
    AgentLock,
    RESPONSE_SYNC_STATE,
    append_log,
    now_iso,
    run_python,
    write_state,
)


def log(message: str) -> None:
    print(f"[{now_iso()}] {message}", flush=True)
    append_log("response_sync_supervisor.log", message)


def main() -> int:
    write_state(
        RESPONSE_SYNC_STATE,
        status="starting",
        stage="init",
        started_at=now_iso(),
        pid=os.getpid(),
        last_error=None,
    )
    log("RESPONSE SYNC START")

    try:
        with AgentLock():
            write_state(
                RESPONSE_SYNC_STATE,
                status="running",
                stage="response_sync_worker",
                pid=os.getpid(),
            )
            code = run_python(
                "response_sync_worker.py",
                extra_env={"HH_RESPONSE_SYNC_HEADLESS": "true"},
                log_filename="response_sync_worker.log",
                timeout_seconds=20 * 60,
            )
    except RuntimeError as exc:
        if str(exc) == "agent_lock_busy":
            log("SKIP: another HH background job is still running")
            write_state(
                RESPONSE_SYNC_STATE,
                status="skipped",
                stage="lock",
                finished_at=now_iso(),
                exit_code=0,
                last_error="agent_lock_busy",
            )
            return 0
        raise

    if code != 0:
        message = f"response_sync_worker.py failed with code={code}"
        log(message)
        write_state(
            RESPONSE_SYNC_STATE,
            status="failed",
            stage="response_sync_worker",
            finished_at=now_iso(),
            exit_code=code,
            last_error=message,
        )
        return code

    write_state(
        RESPONSE_SYNC_STATE,
        status="ok",
        stage="done",
        finished_at=now_iso(),
        exit_code=0,
        last_error=None,
    )
    log("RESPONSE SYNC DONE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        write_state(
            RESPONSE_SYNC_STATE,
            status="failed",
            stage="supervisor",
            finished_at=now_iso(),
            exit_code=99,
            last_error=message,
        )
        append_log("response_sync_supervisor.log", "FATAL " + message)
        raise
