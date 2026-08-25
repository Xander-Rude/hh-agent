from __future__ import annotations

import os

from background_common import (
    APPLY_STATE,
    AgentLock,
    append_log,
    now_iso,
    run_python,
    write_state,
)


def log(message: str) -> None:
    print(f"[{now_iso()}] {message}", flush=True)
    append_log("apply_supervisor.log", message)


def main() -> int:
    write_state(
        APPLY_STATE,
        status="starting",
        stage="init",
        started_at=now_iso(),
        pid=os.getpid(),
        last_error=None,
    )

    log("APPLY START")

    try:
        with AgentLock():
            write_state(
                APPLY_STATE,
                status="running",
                stage="apply_dispatcher",
                pid=os.getpid(),
            )

            code = run_python(
                "apply_dispatcher.py",
                extra_env={
                    "HH_APPLY_HEADLESS": "true",
                    "APPLY_DISPATCH_HH": "true",
                    "YANDEX_APPLY_LIVE": "true",
                    "YANDEX_APPLY_HEADLESS": "true",
                },
                log_filename="apply_dispatcher.log",
                timeout_seconds=30 * 60,
            )

    except RuntimeError as exc:
        if str(exc) == "agent_lock_busy":
            log("SKIP: another HH background job is still running")
            write_state(
                APPLY_STATE,
                status="skipped",
                stage="lock",
                finished_at=now_iso(),
                exit_code=0,
                last_error="agent_lock_busy",
            )
            return 0
        raise

    if code != 0:
        message = f"apply_dispatcher.py failed with code={code}"
        log(message)
        write_state(
            APPLY_STATE,
            status="failed",
            stage="apply_dispatcher",
            finished_at=now_iso(),
            exit_code=code,
            last_error=message,
        )
        return code

    write_state(
        APPLY_STATE,
        status="ok",
        stage="done",
        finished_at=now_iso(),
        exit_code=0,
        last_error=None,
    )

    log("APPLY DONE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        write_state(
            APPLY_STATE,
            status="failed",
            stage="supervisor",
            finished_at=now_iso(),
            exit_code=99,
            last_error=message,
        )
        append_log("apply_supervisor.log", "FATAL " + message)
        raise
