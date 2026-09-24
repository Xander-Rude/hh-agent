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


def log(message: str) -> None:
    print(f"[{now_iso()}] {message}", flush=True)
    append_log("apply_supervisor.log", message)


def _run_account(account, *, dispatch_external: bool) -> int:
    state_path = apply_state_path(account.key)
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
