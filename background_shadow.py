"""Independent HH semantic scorer: no browser, shared inference lock.

Scheduled separately so collection latency cannot starve OLD/CLEAN scoring.
Only one shadow process is allowed, even during a pipeline run. Uses the
same score/gates/resume-version checks as the existing clean_shadow.py.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

from background_common import AgentLock, DATA_DIR, append_log, run_python

load_dotenv()

SHADOW_LOCK = DATA_DIR / "shadow_evaluation.lock"


def main() -> int:
    enabled = os.getenv("HH_SHADOW_WORKER_ENABLED", "true").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        append_log("shadow_supervisor.log", "SKIP: worker disabled")
        return 0

    batch_size = max(1, min(50, int(os.getenv("HH_SHADOW_WORKER_BATCH_SIZE", "12"))))
    timeout_seconds = max(
        5 * 60, int(os.getenv("HH_SHADOW_WORKER_TIMEOUT_SECONDS", "840"))
    )
    try:
        with AgentLock(SHADOW_LOCK):
            append_log(
                "shadow_supervisor.log",
                f"START batch={batch_size} timeout={timeout_seconds}s",
            )
            shadow_code = run_python(
                "clean_shadow.py",
                log_filename="clean_shadow.log",
                extra_env={"CLEAN_SHADOW_MAX_ASSESSMENTS": str(batch_size)},
                timeout_seconds=timeout_seconds,
            )
            # Promote scored OLD candidates immediately; the existing separate
            # letter worker finishes pending letters on its own schedule.
            queue_code = run_python(
                "old_auto_queue.py",
                log_filename="old_auto_queue.log",
                timeout_seconds=5 * 60,
            )
            append_log(
                "shadow_supervisor.log",
                f"DONE shadow_code={shadow_code} old_queue_code={queue_code}",
            )
            return shadow_code or queue_code
    except RuntimeError as exc:
        if str(exc) != "agent_lock_busy":
            raise
        append_log(
            "shadow_supervisor.log",
            "SKIP: pipeline or another shadow worker already scoring",
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
