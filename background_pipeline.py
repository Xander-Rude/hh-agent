from __future__ import annotations

import os
import threading
from datetime import datetime

import httpx
from dotenv import load_dotenv

from background_common import (
    AgentLock,
    LOG_DIR,
    PIPELINE_STATE,
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


def log(message: str) -> None:
    print(f"[{now_iso()}] {message}", flush=True)
    append_log("pipeline_supervisor.log", message)


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
    """Return the last real collector activity and the log's modification time."""
    log_path = LOG_DIR / "collector.log"
    if not log_path.exists():
        return None, None

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
            return line[:500], progress_at

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


def _run_hh_collect() -> int:
    """Run optimized HH collection while refreshing pipeline heartbeat."""
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
        return run_python(
            "hh_collect_optimized.py",
            extra_env={"HH_COLLECT_HEADLESS": "true"},
            log_filename="collector.log",
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


def main() -> int:
    started_at = now_iso()
    try:
        with AgentLock():
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
            session_status = check_hh_session(headless=True)

            if session_status.authenticated:
                log(
                    "HH session OK"
                    + (f" | {session_status.final_url}" if session_status.final_url else "")
                )
                set_stage("collect_hh")
                notify("🔎 HH Agent: собираю свежие вакансии HH...")
                log("1/3 hh_collect_optimized.py")
                collect_code = _run_hh_collect()
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
                    message = f"hh_collect_optimized.py failed with code={collect_code}"
                    log(message)
                    write_state(
                        PIPELINE_STATE,
                        status="failed",
                        stage="collect_hh",
                        finished_at=now_iso(),
                        exit_code=collect_code,
                        last_error=message,
                    )
                    notify(
                        "❌ HH Agent: сбор вакансий HH завершился "
                        f"ошибкой (code={collect_code}).\n"
                        "Подробности: logs\\collector.log"
                    )
                    return collect_code
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

            set_stage("collect_careers")
            notify("🔎 HH Agent: собираю корпоративные карьерные сайты...")
            log("2/3 collect_careers.py")
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
            log("3/3 process_vacancies.py")
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
