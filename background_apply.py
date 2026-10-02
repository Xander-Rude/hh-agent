from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from background_common import (
    APPLY_STATE,
    HHProfileLock,
    append_log,
    apply_state_path,
    now_iso,
    run_python,
    write_state,
)
from hh_accounts import account_label, apply_accounts
from hh_session_guard import check_hh_session
from app.hh_apply_control import (
    captcha_pause,
    captcha_recheck_due,
    clear_captcha_pause,
    is_captcha_paused,
)


def log(message: str) -> None:
    print(f"[{now_iso()}] {message}", flush=True)
    append_log("apply_supervisor.log", message)


def _recheck_old_pause(account) -> bool:
    if account.key != "old" or not captcha_recheck_due(account.key):
        return False
    try:
        with HHProfileLock(account.key):
            status = check_hh_session(account=account, headless=True)
    except RuntimeError as exc:
        if str(exc) == "agent_lock_busy":
            log(f"CAPTCHA recheck deferred {account_label(account.key)}: profile busy")
            return False
        raise
    final_url = str(status.final_url or "").lower()
    if (
        status.authenticated
        and status.identity_verified
        and "captcha" not in final_url
        and "challenge" not in final_url
    ):
        clear_captcha_pause(account.key)
        log(
            f"AUTO-RESUME {account_label(account.key)}: backoff expired and "
            "HH session/identity verified"
        )
        return True
    log(
        f"CAPTCHA pause retained {account_label(account.key)}: "
        f"{status.reason} final_url={status.final_url or '-'}"
    )
    return False


def _run_account(account, *, dispatch_external: bool) -> int:
    state_path = apply_state_path(account.key)

    if is_captcha_paused(account.key):
        pause = captcha_pause(account.key) or {}
        reason = str(pause.get("reason") or "captcha")
        log(
            f"SKIP {account_label(account.key)}: persistent CAPTCHA pause | "
            f"{reason}"
        )
        write_state(
            state_path,
            status="skipped",
            stage="captcha_pause",
            started_at=now_iso(),
            finished_at=now_iso(),
            exit_code=0,
            account_key=account.key,
            last_error="captcha_pause",
        )
        return 0

    write_state(
        state_path,
        status="starting",
        stage="profile_lock",
        started_at=now_iso(),
        pid=os.getpid(),
        account_key=account.key,
        last_error=None,
    )

    log(f"APPLY ACCOUNT START {account_label(account.key)}")

    try:
        with HHProfileLock(account.key):
            write_state(
                state_path,
                status="running",
                stage="apply_dispatcher",
                pid=os.getpid(),
                account_key=account.key,
            )

            code = run_python(
                "apply_dispatcher.py",
                extra_env={
                    "HH_WORKER_ACCOUNT": account.key,
                    "HH_APPLY_HEADLESS": "true",
                    "APPLY_DISPATCH_HH": "true",
                    "APPLY_DISPATCH_EXTERNAL": (
                        "true" if dispatch_external else "false"
                    ),
                    "YANDEX_APPLY_LIVE": "true",
                    "YANDEX_APPLY_HEADLESS": "true",
                    "VK_APPLY_LIVE": "true",
                    "VK_APPLY_HEADLESS": "false",
                    "TBANK_APPLY_LIVE": "true",
                    "TBANK_APPLY_HEADLESS": "true",
                    "OZON_APPLY_LIVE": "true",
                    "OZON_APPLY_HEADLESS": "true",
                },
                log_filename=f"apply_dispatcher_{account.key}.log",
                timeout_seconds=30 * 60,
            )

    except RuntimeError as exc:
        if str(exc) == "agent_lock_busy":
            log(
                f"SKIP {account_label(account.key)}: "
                "its HH profile is already in use"
            )
            write_state(
                state_path,
                status="skipped",
                stage="profile_lock",
                finished_at=now_iso(),
                exit_code=0,
                account_key=account.key,
                last_error="hh_profile_lock_busy",
            )
            return 0
        raise

    if code != 0:
        message = (
            f"apply_dispatcher.py failed for {account.key} with code={code}"
        )
        log(message)
        write_state(
            state_path,
            status="failed",
            stage="apply_dispatcher",
            finished_at=now_iso(),
            exit_code=code,
            account_key=account.key,
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
        last_error=None,
    )
    log(f"APPLY ACCOUNT DONE {account_label(account.key)}")
    return 0


def main() -> int:
    accounts = apply_accounts()
    started_at = now_iso()

    write_state(
        APPLY_STATE,
        status="starting",
        stage="multi_account_init",
        started_at=started_at,
        pid=os.getpid(),
        accounts=[item.key for item in accounts],
        last_error=None,
    )

    if not accounts:
        write_state(
            APPLY_STATE,
            status="ok",
            stage="done",
            finished_at=now_iso(),
            exit_code=0,
            accounts=[],
            last_error=None,
        )
        log("APPLY DONE: no configured HH accounts")
        return 0

    log(
        "APPLY START accounts="
        + ",".join(item.key for item in accounts)
    )

    write_state(
        APPLY_STATE,
        status="running",
        stage="parallel_account_dispatch",
        pid=os.getpid(),
        accounts=[item.key for item in accounts],
    )

    results: dict[str, int] = {}
    max_workers = max(1, len(accounts))

    # APPLY is coordinated only by per-account HHProfileLock.  This allows
    # OLD to apply while CLEAN collection is running (and vice versa).
    # Deployment safety is handled by the deploy lock holder reserving both
    # profile locks before changing production files.
    with ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="hh-apply-account",
    ) as pool:
        futures = {
            pool.submit(
                _run_account,
                account,
                dispatch_external=(index == 0),
            ): account
            for index, account in enumerate(accounts)
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
                    apply_state_path(account.key),
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
        APPLY_STATE,
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

    log(
        "APPLY DONE "
        + " ".join(f"{key}={code}" for key, code in sorted(results.items()))
    )
    return aggregate_code


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
