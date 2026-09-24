from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.resume_metrics import record_raises
from background_common import (
    HHProfileLock,
    LOG_DIR,
    RESUME_RAISE_STATE,
    append_log,
    now_iso,
    read_state,
    resume_raise_state_path,
    run_python,
    write_state,
)
from hh_accounts import (
    account_label,
    account_resume_id,
    apply_accounts,
)
from resume_raise_schedule import (
    local_now,
    next_raise_at_from_text,
    retry_at,
    should_run_for_due_at,
    to_iso,
)


WORKER_SCRIPT = "resume_raise_worker_v2.py"
TELEMETRY_SCRIPT = "resume_telemetry_worker.py"
RAISED_RE = re.compile(
    r"\[DONE\]\s+Поднятий выполнено:\s*(\d+)",
    re.IGNORECASE,
)


def log(message: str) -> None:
    print(f"[{now_iso()}] {message}", flush=True)
    append_log("resume_raise_supervisor.log", message)


def _log_path(account_key: str) -> Path:
    return LOG_DIR / f"resume_raise_worker_{account_key}.log"


def _telemetry_log_name(account_key: str) -> str:
    return f"resume_telemetry_worker_{account_key}.log"


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


def _set_next_due(
    state_path: Path,
    account_key: str,
    due_at,
    reason: str,
) -> None:
    due_iso = to_iso(due_at)
    write_state(
        state_path,
        account_key=account_key,
        next_due_at=due_iso,
        schedule_reason=reason,
    )
    log(
        f"{account_label(account_key)} NEXT RESUME RAISE CHECK: "
        f"{due_iso} ({reason})"
    )


def _plan_next_run(
    *,
    code: int,
    worker_output: str,
    state_path: Path,
    account_key: str,
) -> None:
    now = local_now()

    if code == 0:
        advertised = next_raise_at_from_text(worker_output, now=now)
        if advertised is not None:
            if advertised <= now:
                _set_next_due(
                    state_path,
                    account_key,
                    retry_at(5, now=now),
                    "hh_due_cta_not_rendered",
                )
            else:
                _set_next_due(
                    state_path,
                    account_key,
                    advertised,
                    "hh_advertised_time",
                )
            return

        raised = [int(value) for value in RAISED_RE.findall(worker_output)]
        if raised and max(raised) > 0:
            _set_next_due(
                state_path,
                account_key,
                retry_at(10, now=now),
                "post_raise_refresh",
            )
            return

        _set_next_due(
            state_path,
            account_key,
            retry_at(15, now=now),
            "no_hh_due_time_found",
        )
        return

    if code == 2:
        minutes, reason = 5, "network_retry"
    elif code in {5, 124}:
        minutes, reason = 5, "worker_retry"
    elif code in {3, 6}:
        minutes, reason = 30, "hh_antibot_backoff"
    elif code == 4:
        minutes, reason = 15, "hh_session_retry"
    elif code == 8:
        minutes, reason = 30, "hh_account_identity_mismatch"
    else:
        minutes, reason = 15, f"worker_exit_{code}_retry"

    _set_next_due(
        state_path,
        account_key,
        retry_at(minutes, now=now),
        reason,
    )


def _resume_id(account) -> str | None:
    configured = account_resume_id(account)
    if configured:
        return configured

    if account.key == "old":
        return (
            os.getenv("HH_ACTIVE_RESUME_ID", "").strip()
            or "ed318343ff109278200039ed1f674d474e5336"
        )

    return None


def _record_worker_raises(
    worker_output: str,
    *,
    resume_id: str | None,
    account_key: str,
) -> int:
    values = [int(value) for value in RAISED_RE.findall(worker_output)]
    raised = max(values) if values else 0
    if raised > 0 and resume_id:
        record_raises(resume_id, raised)
        log(
            f"{account_label(account_key)} RESUME METRICS: "
            f"recorded raises_delta={raised}"
        )
    return raised


def _collect_resume_telemetry(account, resume_id: str | None) -> None:
    extra_env = {
        "HH_WORKER_ACCOUNT": account.key,
        "HH_RESUME_RAISE_HEADLESS": "true",
    }
    if resume_id:
        extra_env["HH_ACTIVE_RESUME_ID"] = resume_id

    code = run_python(
        TELEMETRY_SCRIPT,
        extra_env=extra_env,
        log_filename=_telemetry_log_name(account.key),
        timeout_seconds=90,
    )
    if code == 0:
        log(
            f"{account_label(account.key)} RESUME METRICS: "
            "views/invitations snapshot recorded"
        )
    else:
        log(
            f"{account_label(account.key)} RESUME METRICS WARN: "
            f"telemetry worker exited with code={code}"
        )


def _run_account(account) -> int:
    state_path = resume_raise_state_path(account.key)
    state = read_state(state_path)
    if not should_run_for_due_at(state.get("next_due_at")):
        return 0

    resume_id = _resume_id(account)
    started_at = now_iso()
    write_state(
        state_path,
        status="starting",
        stage="profile_lock",
        started_at=started_at,
        finished_at=None,
        exit_code=None,
        pid=os.getpid(),
        account_key=account.key,
        resume_id=resume_id,
        last_error=None,
    )

    log(f"{account_label(account.key)} RESUME RAISE START")

    try:
        with HHProfileLock(account.key):
            write_state(
                state_path,
                status="running",
                stage="resume_raise_worker",
                pid=os.getpid(),
                account_key=account.key,
                resume_id=resume_id,
                last_error=None,
            )

            worker_log_path = _log_path(account.key)
            log_offset = _log_size(worker_log_path)
            code = run_python(
                WORKER_SCRIPT,
                extra_env={
                    "HH_WORKER_ACCOUNT": account.key,
                    "HH_RESUME_RAISE_HEADLESS": "true",
                    "HH_RESUME_RAISE_NAV_RETRIES": "5",
                    "HH_RESUME_RAISE_NAV_RETRY_DELAY_MS": "10000",
                },
                log_filename=worker_log_path.name,
                timeout_seconds=5 * 60,
            )
            worker_output = _read_log_tail(worker_log_path, log_offset)

            _record_worker_raises(
                worker_output,
                resume_id=resume_id,
                account_key=account.key,
            )
            _plan_next_run(
                code=code,
                worker_output=worker_output,
                state_path=state_path,
                account_key=account.key,
            )

            if code == 0:
                write_state(
                    state_path,
                    status="running",
                    stage="resume_telemetry",
                    pid=os.getpid(),
                    account_key=account.key,
                    resume_id=resume_id,
                    last_error=None,
                )
                _collect_resume_telemetry(account, resume_id)

    except RuntimeError as exc:
        if str(exc) == "agent_lock_busy":
            log(
                f"{account_label(account.key)} RESUME RAISE SKIP: "
                "profile busy"
            )
            write_state(
                state_path,
                status="skipped",
                stage="profile_lock",
                finished_at=now_iso(),
                exit_code=0,
                account_key=account.key,
                resume_id=resume_id,
                last_error="hh_profile_lock_busy",
            )
            return 0
        raise

    if code != 0:
        if code == 4:
            message = (
                f"{account_label(account.key)} HH session expired; "
                f"run hh_login.py --account {account.key}"
            )
        elif code == 8:
            message = (
                f"{account_label(account.key)} HH identity mismatch; "
                "resume raise blocked"
            )
        else:
            message = (
                f"{WORKER_SCRIPT} failed for {account.key} with code={code}"
            )

        log(message)
        write_state(
            state_path,
            status="failed",
            stage="resume_raise_worker",
            finished_at=now_iso(),
            exit_code=code,
            account_key=account.key,
            resume_id=resume_id,
            last_error=message,
        )
        return code

    write_state(
        state_path,
        status="ok",
        stage="done",
        finished_at=now_iso(),
        exit_code=0,
        account_key=account.key,
        resume_id=resume_id,
        last_error=None,
    )
    log(f"{account_label(account.key)} RESUME RAISE DONE")
    return 0


def main() -> int:
    accounts = apply_accounts()
    write_state(
        RESUME_RAISE_STATE,
        status="starting",
        stage="multi_account_init",
        started_at=now_iso(),
        finished_at=None,
        exit_code=None,
        pid=os.getpid(),
        accounts=[item.key for item in accounts],
        last_error=None,
    )

    if not accounts:
        write_state(
            RESUME_RAISE_STATE,
            status="ok",
            stage="done",
            finished_at=now_iso(),
            exit_code=0,
            accounts=[],
            last_error=None,
        )
        return 0

    results: dict[str, int] = {}
    with ThreadPoolExecutor(
        max_workers=max(1, len(accounts)),
        thread_name_prefix="hh-resume-raise",
    ) as pool:
        futures = {
            pool.submit(_run_account, account): account
            for account in accounts
        }
        for future in as_completed(futures):
            account = futures[future]
            try:
                results[account.key] = int(future.result())
            except Exception as exc:
                results[account.key] = 99
                message = (
                    f"{account.key}: {type(exc).__name__}: {exc}"
                )
                log("FATAL " + message)
                write_state(
                    resume_raise_state_path(account.key),
                    status="failed",
                    stage="supervisor",
                    finished_at=now_iso(),
                    exit_code=99,
                    account_key=account.key,
                    last_error=message,
                )

    failed = {
        key: code
        for key, code in results.items()
        if code != 0
    }
    aggregate_code = max(failed.values(), default=0)

    write_state(
        RESUME_RAISE_STATE,
        status="failed" if failed else "ok",
        stage="done",
        finished_at=now_iso(),
        exit_code=aggregate_code,
        accounts=[item.key for item in accounts],
        results=results,
        last_error=(
            "account_failures="
            + ",".join(f"{key}:{code}" for key, code in failed.items())
            if failed
            else None
        ),
    )
    return aggregate_code


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
