from __future__ import annotations

import os
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime

import httpx
from dotenv import load_dotenv

from background_common import (
    AgentLock,
    HHProfileLock,
    LOG_DIR,
    PIPELINE_STATE,
    RESPONSE_SYNC_STATE,
    append_log,
    now_iso,
    read_state,
    run_python,
    write_state,
)
from hh_session_guard import check_hh_session

load_dotenv()

BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
TRIGGERED_BY_TELEGRAM = (
    os.getenv("HH_TRIGGERED_BY_TELEGRAM", "false").lower() == "true"
)
PIPELINE_HEARTBEAT_SECONDS = max(
    1.0,
    float(os.getenv("HH_PIPELINE_HEARTBEAT_SECONDS", "30")),
)
HH_COLLECT_SUPERVISOR_TIMEOUT_SECONDS = max(
    5 * 60,
    int(os.getenv("HH_COLLECT_SUPERVISOR_TIMEOUT_SECONDS", str(40 * 60))),
)
HH_COLLECT_TRANSIENT_RETRIES = max(
    0,
    int(os.getenv("HH_COLLECT_TRANSIENT_RETRIES", "2")),
)
HH_COLLECT_RETRY_DELAY_SECONDS = max(
    0.0,
    float(os.getenv("HH_COLLECT_RETRY_DELAY_SECONDS", "20")),
)
PIPELINE_LOCK_RETRY_INTERVAL_SECONDS = max(
    1.0,
    float(os.getenv("HH_PIPELINE_LOCK_RETRY_INTERVAL_SECONDS", "15")),
)
PIPELINE_LOCK_RETRY_TIMEOUT_SECONDS = max(
    0.0,
    float(os.getenv("HH_PIPELINE_LOCK_RETRY_TIMEOUT_SECONDS", "180")),
)


def _safe_console_print(message: str) -> None:
    try:
        print(message, flush=True)
        return
    except UnicodeEncodeError:
        pass

    stream = sys.stdout
    encoding = getattr(stream, "encoding", None) or "ascii"
    safe_message = message.encode(
        encoding,
        errors="backslashreplace",
    ).decode(
        encoding,
        errors="replace",
    )
    print(safe_message, flush=True)


def log(message: str) -> None:
    append_log("pipeline_supervisor.log", message)
    _safe_console_print(f"[{now_iso()}] {message}")


def notify(message: str, *, force: bool = False) -> None:
    if (not force and not TRIGGERED_BY_TELEGRAM) or not BOT_TOKEN or not CHAT_ID:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": message[:4000],
                "disable_web_page_preview": True,
            },
            timeout=10.0,
        ).raise_for_status()
    except Exception as exc:
        log(
            "Telegram progress notification failed: "
            f"{type(exc).__name__}: {exc}"
        )


def set_stage(
    stage: str,
    *,
    status: str = "running",
    last_error: str | None = None,
    progress: str | None = None,
    progress_at: str | None = None,
) -> None:
    values = {
        "status": status,
        "stage": stage,
        "pid": os.getpid(),
        "progress": progress,
        "progress_at": progress_at,
    }

    if status in {"starting", "running"}:
        # Runtime state is merge-based. Explicitly clear terminal fields so an
        # older skipped/failed run cannot leak into an active pipeline.
        values.update(
            finished_at=None,
            exit_code=None,
            last_error=last_error,
        )
    elif last_error is not None:
        values["last_error"] = last_error

    write_state(PIPELINE_STATE, **values)


def _collector_progress_snapshot() -> tuple[str | None, str | None]:
    """Return the freshest OLD/CLEAN collector activity."""
    candidates = [
        LOG_DIR / "collector.log",
        LOG_DIR / "collector_clean.log",
    ]
    existing = [path for path in candidates if path.exists()]
    if not existing:
        return None, None

    log_path = max(existing, key=lambda path: path.stat().st_mtime)
    try:
        progress_at = datetime.fromtimestamp(
            log_path.stat().st_mtime
        ).astimezone().isoformat(timespec="seconds")
        lines = log_path.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()
    except Exception:
        return None, None

    markers = (
        "[SEARCH ",
        "[PAGE ",
        "[RECOMMENDATION FEED ",
        "[RECOMMENDATION PAGE ",
        "[VACANCY ",
        "[WATCHDOG]",
    )
    for raw_line in reversed(lines[-250:]):
        line = raw_line.strip()
        if any(marker in line for marker in markers):
            account = "CLEAN" if log_path.name == "collector_clean.log" else "OLD"
            return f"[{account}] {line[:480]}", progress_at

    return None, progress_at


def _preserve_active_pipeline_state(state: dict) -> bool:
    """Keep another live pipeline's runtime state when this run loses AgentLock."""
    if state.get("status") not in {"starting", "running"}:
        return False

    pid = state.get("pid")
    if pid is None:
        return True

    try:
        return int(pid) != os.getpid()
    except (TypeError, ValueError):
        return True


def _record_lock_busy(*, started_at: str) -> None:
    current = read_state(PIPELINE_STATE)
    if _preserve_active_pipeline_state(current):
        log("SKIP state update: active pipeline runtime state preserved")
        return

    write_state(
        PIPELINE_STATE,
        status="skipped",
        stage="lock",
        started_at=started_at,
        finished_at=now_iso(),
        pid=os.getpid(),
        exit_code=0,
        last_error="agent_lock_busy",
        progress=None,
        progress_at=None,
    )


def _collector_failure_is_transient_network(account_key: str = "old") -> bool:
    """Detect browser/network failures that are safe to retry for one HH account."""
    log_path = LOG_DIR / (
        "collector.log" if account_key == "old" else f"collector_{account_key}.log"
    )
    if not log_path.exists():
        return False

    try:
        tail = "\n".join(
            log_path.read_text(
                encoding="utf-8",
                errors="replace",
            ).splitlines()[-250:]
        ).lower()
    except Exception:
        return False

    markers = (
        "err_connection_timed_out",
        "err_connection_reset",
        "err_connection_closed",
        "err_network_changed",
        "err_internet_disconnected",
        "err_name_not_resolved",
        "connecttimeout",
        "connect timeout",
        "connection timed out",
        "connection timeout",
    )
    return any(marker in tail for marker in markers)


def _run_hh_collect_with_retry(account_key: str = "old") -> int:
    """Retry one account's HH collector only for transient network failures."""
    total_attempts = HH_COLLECT_TRANSIENT_RETRIES + 1

    for attempt in range(1, total_attempts + 1):
        code = _run_hh_collect(account_key)
        if code in {0, 124}:
            return code
        if attempt >= total_attempts or not _collector_failure_is_transient_network(account_key):
            return code

        delay = HH_COLLECT_RETRY_DELAY_SECONDS
        log(
            "WARN: transient HH network failure detected; "
            f"retry collector in {delay:g}s "
            f"(attempt {attempt + 1}/{total_attempts})"
        )
        set_stage(
            "collect_hh",
            progress=f"network retry {attempt + 1}/{total_attempts}",
            progress_at=now_iso(),
        )
        if delay:
            time.sleep(delay)

    return 1


@contextmanager
def _hh_profile_lock_with_retry(account_key: str):
    """Wait for the specific HH browser profile without blocking other accounts."""
    deadline = time.monotonic() + PIPELINE_LOCK_RETRY_TIMEOUT_SECONDS
    attempt = 0

    while True:
        lock = HHProfileLock(account_key)
        try:
            lock.__enter__()
        except RuntimeError as exc:
            if str(exc) != "agent_lock_busy":
                raise

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise

            attempt += 1
            delay = min(PIPELINE_LOCK_RETRY_INTERVAL_SECONDS, remaining)
            log(
                f"HH profile {account_key} busy; waiting {delay:g}s "
                f"before retry #{attempt}"
            )
            time.sleep(delay)
            continue

        try:
            yield lock
        except BaseException as exc:
            lock.__exit__(type(exc), exc, exc.__traceback__)
            raise
        else:
            lock.__exit__(None, None, None)
        return


@contextmanager
def _agent_lock_with_retry():
    """Wait briefly for short APPLY/RESUME collisions before skipping pipeline."""
    deadline = time.monotonic() + PIPELINE_LOCK_RETRY_TIMEOUT_SECONDS
    attempt = 0

    while True:
        lock = AgentLock()
        try:
            lock.__enter__()
        except RuntimeError as exc:
            if str(exc) != "agent_lock_busy":
                raise

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise

            attempt += 1
            delay = min(PIPELINE_LOCK_RETRY_INTERVAL_SECONDS, remaining)
            log(
                "AgentLock busy; waiting "
                f"{delay:g}s before retry #{attempt} "
                f"(timeout {PIPELINE_LOCK_RETRY_TIMEOUT_SECONDS:g}s)"
            )
            time.sleep(delay)
            continue

        try:
            yield lock
        except BaseException as exc:
            lock.__exit__(type(exc), exc, exc.__traceback__)
            raise
        else:
            lock.__exit__(None, None, None)
        return


def _run_hh_collect(account_key: str = "old") -> int:
    """Run optimized HH collection for one isolated HH browser profile."""
    stop_event = threading.Event()

    def heartbeat_loop() -> None:
        while not stop_event.wait(PIPELINE_HEARTBEAT_SECONDS):
            progress, progress_at = _collector_progress_snapshot()
            set_stage(
                "collect_hh",
                progress=progress,
                progress_at=progress_at,
            )

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        name="pipeline-collect-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()

    try:
        with _hh_profile_lock_with_retry(account_key):
            return run_python(
                "hh_collect_optimized.py",
                extra_env={
                    "HH_COLLECT_HEADLESS": "true",
                    "HH_COLLECT_ACCOUNT": account_key,
                    "HH_WORKER_ACCOUNT": account_key,
                    "HH_ALWAYS_RUN_TARGET_SEARCH": "true",
                },
                log_filename=(
                    "collector.log"
                    if account_key == "old"
                    else f"collector_{account_key}.log"
                ),
                timeout_seconds=HH_COLLECT_SUPERVISOR_TIMEOUT_SECONDS,
            )
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=2)


def _run_process() -> int:
    """Run vacancy processing while refreshing heartbeat, without a wall-clock cap."""
    stop_event = threading.Event()

    def heartbeat_loop() -> None:
        while not stop_event.wait(PIPELINE_HEARTBEAT_SECONDS):
            set_stage("process")

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        name="pipeline-process-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()

    try:
        return run_python(
            "process_vacancies.py",
            log_filename="processor.log",
            timeout_seconds=None,
        )
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=2)


def _run_clean_shadow() -> int:
    """Run isolated CLEAN shadow scoring with pipeline heartbeat."""
    stop_event = threading.Event()

    def heartbeat_loop() -> None:
        while not stop_event.wait(PIPELINE_HEARTBEAT_SECONDS):
            set_stage("clean_shadow")

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        name="pipeline-clean-shadow-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()

    try:
        return run_python(
            "clean_shadow.py",
            log_filename="clean_shadow.log",
            timeout_seconds=None,
        )
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=2)


def _run_prepare_cover_letters() -> int:
    """Precompute immutable final cover letters before Telegram /new reads them."""
    stop_event = threading.Event()

    def heartbeat_loop() -> None:
        while not stop_event.wait(PIPELINE_HEARTBEAT_SECONDS):
            set_stage("prepare_cover_letters")

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        name="pipeline-cover-prepare-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()

    try:
        return run_python(
            "prepare_cover_letters.py",
            log_filename="cover_letter_prepare.log",
            timeout_seconds=None,
        )
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=2)


def _run_response_sync() -> int:
    """Run bounded HH outcome probes and own their runtime state."""
    stop_event = threading.Event()
    started_at = now_iso()

    write_state(
        RESPONSE_SYNC_STATE,
        status="running",
        stage="response_sync_worker",
        started_at=started_at,
        pid=os.getpid(),
        owner="pipeline",
        finished_at=None,
        exit_code=None,
        last_error=None,
    )

    def heartbeat_loop() -> None:
        while not stop_event.wait(PIPELINE_HEARTBEAT_SECONDS):
            set_stage("response_sync")
            write_state(
                RESPONSE_SYNC_STATE,
                status="running",
                stage="response_sync_worker",
                pid=os.getpid(),
                owner="pipeline",
                finished_at=None,
                exit_code=None,
                last_error=None,
            )

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        name="pipeline-response-sync-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()

    try:
        code = run_python(
            "response_sync_worker.py",
            extra_env={"HH_RESPONSE_SYNC_HEADLESS": "true"},
            log_filename="response_sync_worker.log",
            timeout_seconds=15 * 60,
        )
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        write_state(
            RESPONSE_SYNC_STATE,
            status="failed",
            stage="response_sync_worker",
            pid=os.getpid(),
            owner="pipeline",
            finished_at=now_iso(),
            exit_code=99,
            last_error=message,
        )
        raise
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=2)

    if code == 0:
        write_state(
            RESPONSE_SYNC_STATE,
            status="ok",
            stage="done",
            pid=os.getpid(),
            owner="pipeline",
            finished_at=now_iso(),
            exit_code=0,
            last_error=None,
        )
    else:
        message = f"response_sync_worker.py failed with code={code}"
        write_state(
            RESPONSE_SYNC_STATE,
            status="failed",
            stage="response_sync_worker",
            pid=os.getpid(),
            owner="pipeline",
            finished_at=now_iso(),
            exit_code=code,
            last_error=message,
        )

    return code


def main() -> int:
    started_at = now_iso()
    if os.getenv("HH_PIPELINE_ENABLED", "true").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        write_state(
            PIPELINE_STATE,
            status="skipped",
            stage="disabled",
            started_at=started_at,
            pid=os.getpid(),
            triggered_by=("telegram" if TRIGGERED_BY_TELEGRAM else "scheduler"),
            finished_at=now_iso(),
            exit_code=0,
            last_error=None,
            progress=None,
            progress_at=None,
        )
        log("PIPELINE PAUSED: HH_PIPELINE_ENABLED=false")
        return 0
    try:
        with _agent_lock_with_retry():
            write_state(
                PIPELINE_STATE,
                status="starting",
                stage="init",
                started_at=started_at,
                pid=os.getpid(),
                triggered_by=("telegram" if TRIGGERED_BY_TELEGRAM else "scheduler"),
                finished_at=None,
                exit_code=None,
                last_error=None,
                progress=None,
                progress_at=None,
            )
            log("PIPELINE START")
            notify("▶️ HH Agent: pipeline запущен.\nЭтап: подготовка.")
            set_stage("check_hh_session")
            session_status = check_hh_session(
                account="old",
                headless=True,
            )

            if (
                session_status.authenticated
                and session_status.identity_verified
            ):
                log(
                    "HH session OK"
                    + (f" | {session_status.final_url}" if session_status.final_url else "")
                )
                set_stage("collect_hh")
                notify("🔎 HH Agent: собираю свежие вакансии HH...")
                log("1/5 hh_collect_optimized.py")
                collect_code = _run_hh_collect_with_retry("old")
                if collect_code == 124:
                    message = (
                        "hh_collect_optimized.py hit supervisor timeout "
                        f"after {HH_COLLECT_SUPERVISOR_TIMEOUT_SECONDS}s; "
                        "continue pipeline with already collected vacancies"
                    )
                    log("WARN: " + message)
                    notify(
                        "⚠️ HH Agent: сбор HH превысил аварийный лимит времени.\n"
                        "Зависший процесс остановлен, продолжаю обработку уже "
                        "собранных вакансий.\n"
                        "Подробности: logs\\collector.log"
                    )
                elif collect_code != 0:
                    message = (
                        "OLD hh_collect_optimized.py failed "
                        f"with code={collect_code}; continue with CLEAN "
                        "and already collected vacancies"
                    )
                    log("WARN: " + message)
                    notify(
                        "⚠️ HH Agent: OLD-сбор HH завершился "
                        f"с ошибкой (code={collect_code}). "
                        "CLEAN-сбор и остальной pipeline продолжаются.\n"
                        "Подробности: logs\\collector.log"
                    )
            else:
                message = session_status.reason
                log("WARN: " + message)
                notify(
                    "⚠️ HH Agent: HH-сессия протухла или недоступна.\n"
                    "Персональный сбор HH и HH-отклики остановлены, чтобы агент "
                    "не подменял рекомендации обычным поиском и не создавал "
                    "ложные manual_required.\n\n"
                    "Запусти check_hh_session.py и войди в HH в открывшемся окне.",
                    force=True,
                )

            clean_session_status = check_hh_session(
                account="clean",
                headless=True,
            )
            if (
                clean_session_status.authenticated
                and clean_session_status.identity_verified
            ):
                log(
                    "HH CLEAN session OK"
                    + (
                        f" | {clean_session_status.final_url}"
                        if clean_session_status.final_url
                        else ""
                    )
                )
                set_stage("collect_hh_clean")
                notify("🟢 HH Agent: собираю поиск и рекомендации CLEAN...")
                log("1b/5 hh_collect_optimized.py account=clean")
                clean_collect_code = _run_hh_collect_with_retry("clean")
                if clean_collect_code == 124:
                    log(
                        "WARN: CLEAN collector hit supervisor timeout; "
                        "continue with already collected vacancies"
                    )
                elif clean_collect_code != 0:
                    log(
                        "WARN: CLEAN collector failed "
                        f"with code={clean_collect_code}; continue pipeline"
                    )
                    notify(
                        "⚠️ HH Agent: CLEAN-сбор завершился с ошибкой "
                        f"(code={clean_collect_code}). Продолжаю pipeline.\n"
                        "Подробности: logs\\collector_clean.log"
                    )
            else:
                log(
                    "WARN: CLEAN HH session unavailable: "
                    f"{clean_session_status.reason}"
                )
                notify(
                    "⚠️ HH Agent: CLEAN-сессия HH недоступна, "
                    "CLEAN-рекомендации и поиск пропущены."
                )

            set_stage("collect_careers")
            notify("🔎 HH Agent: собираю корпоративные карьерные сайты...")
            log("2/5 collect_careers.py")
            careers_code = run_python(
                "collect_careers.py",
                log_filename="careers_collector.log",
                timeout_seconds=10 * 60,
            )
            if careers_code != 0:
                message = (
                    "collect_careers.py failed "
                    f"with code={careers_code}; continue pipeline"
                )
                log("WARN: " + message)
                notify(
                    "⚠️ HH Agent: корпоративные карьерные сайты временно "
                    f"не собраны (code={careers_code}). Продолжаю обработку HH.\n"
                    "Подробности: logs\\careers_collector.log"
                )

            set_stage("process")
            notify("🧠 HH Agent: сбор закончен, обрабатываю новые вакансии...")
            log("3/5 process_vacancies.py")
            process_code = _run_process()
            if process_code != 0:
                message = (
                    "process_vacancies.py failed "
                    f"with code={process_code}"
                )
                log(message)
                write_state(
                    PIPELINE_STATE,
                    status="failed",
                    stage="process",
                    finished_at=now_iso(),
                    exit_code=process_code,
                    last_error=message,
                )
                notify(
                    "❌ HH Agent: обработка вакансий "
                    f"завершилась ошибкой (code={process_code}).\n"
                    "Подробности: logs\\processor.log"
                )
                return process_code

            if os.getenv("CLEAN_SHADOW_ENABLED", "true").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }:
                set_stage("clean_shadow")
                log("4/5 clean_shadow.py")
                shadow_code = _run_clean_shadow()
                if shadow_code != 0:
                    log(
                        "WARN: clean_shadow.py failed "
                        f"with code={shadow_code}; legacy pipeline remains valid"
                    )

            set_stage("prepare_cover_letters")
            log("5/6 prepare_cover_letters.py")
            cover_prepare_code = _run_prepare_cover_letters()
            if cover_prepare_code != 0:
                log(
                    "WARN: prepare_cover_letters.py failed "
                    f"with code={cover_prepare_code}; "
                    "unprepared vacancies will stay hidden from /new"
                )

            if session_status.authenticated or clean_session_status.authenticated:
                set_stage("response_sync")
                log("6/6 response_sync_worker.py")
                response_sync_code = _run_response_sync()
                if response_sync_code != 0:
                    log(
                        "WARN: response_sync_worker.py failed "
                        f"with code={response_sync_code}; "
                        "outcome collection is fail-open for pipeline"
                    )

    except RuntimeError as exc:
        if str(exc) == "agent_lock_busy":
            log("SKIP: another HH background job is still running")
            _record_lock_busy(started_at=started_at)
            notify(
                "⏳ HH Agent: другой фоновый процесс уже работает. "
                "Новый pipeline не запущен."
            )
            return 0
        raise

    write_state(
        PIPELINE_STATE,
        status="ok",
        stage="done",
        finished_at=now_iso(),
        exit_code=0,
        last_error=None,
    )
    log("PIPELINE DONE")
    notify(
        "✅ HH Agent: pipeline завершён успешно.\n"
        "Новые подходящие вакансии можно подтвердить в боте; "
        "отклики отправит отдельный Apply worker."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        write_state(
            PIPELINE_STATE,
            status="failed",
            stage="supervisor",
            finished_at=now_iso(),
            exit_code=99,
            last_error=message,
        )
        append_log("pipeline_supervisor.log", "FATAL " + message)
        raise
