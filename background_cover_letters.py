from __future__ import annotations

from background_common import AgentLock, DATA_DIR, append_log, run_python


COVER_LOCK = DATA_DIR / "cover_letter_worker.lock"


def main() -> int:
    """Run letter generation independently from the HH collection pipeline.

    A dedicated lock prevents the scheduled task and the pipeline from
    generating the same letters concurrently. No HH browser is involved.
    """
    try:
        with AgentLock(COVER_LOCK):
            return run_python(
                "cover_letter_worker.py",
                log_filename="cover_letter_worker.log",
                timeout_seconds=35 * 60,
            )
    except RuntimeError as exc:
        if str(exc) != "agent_lock_busy":
            raise
        append_log(
            "cover_letter_supervisor.log",
            "SKIP: cover-letter worker is already running",
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
