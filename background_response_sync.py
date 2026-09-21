from __future__ import annotations

from background_common import AgentLock, append_log, now_iso, run_python


WORKER_SCRIPT = "hh_response_sync_worker.py"
LOG_FILENAME = "response_sync_worker.log"


def log(message: str) -> None:
    print(f"[{now_iso()}] {message}", flush=True)
    append_log("response_sync_supervisor.log", message)


def main() -> int:
    log("RESPONSE SYNC START")
    try:
        with AgentLock():
            code = run_python(
                WORKER_SCRIPT,
                extra_env={"HH_RESPONSE_SYNC_HEADLESS": "true"},
                log_filename=LOG_FILENAME,
                timeout_seconds=3 * 60,
            )
    except RuntimeError as exc:
        if str(exc) == "agent_lock_busy":
            log("SKIP: another HH background job is still running")
            return 0
        raise

    if code == 0:
        log("RESPONSE SYNC DONE")
        return 0

    if code == 4:
        log("RESPONSE SYNC: HH session is not authenticated")
    else:
        log(f"RESPONSE SYNC: worker failed with code={code}")
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        append_log(
            "response_sync_supervisor.log",
            f"FATAL {type(exc).__name__}: {exc}",
        )
        raise
