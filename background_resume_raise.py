from __future__ import annotations

import os

from background_common import (
    AgentLock,
    RESUME_RAISE_STATE,
    append_log,
    now_iso,
    run_python,
    write_state,
)


WORKER_SCRIPT = "resume_raise_worker_v2.py"


def log(message: str) -> None:
    print(f"[{now_iso()}] {message}", flush=True)
    append_log("resume_raise_supervisor.log", message)


def main() -> int:
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

            code = run_python(
                WORKER_SCRIPT,
                extra_env={"HH_RESUME_RAISE_HEADLESS": "true"},
                log_filename="resume_raise_worker.log",
                timeout_seconds=5 * 60,
            )

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
