from __future__ import annotations

import os
import threading

import httpx
from dotenv import load_dotenv

from background_common import (
    AgentLock,
    PIPELINE_STATE,
    append_log,
    now_iso,
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
) -> None:
    values = {
        "status": status,
        "stage": stage,
        "pid": os.getpid(),
    }
    if last_error is not None:
        values["last_error"] = last_error
    write_state(PIPELINE_STATE, **values)


def _run_hh_collect() -> int:
    """Run the optimized HH collector and keep pipeline heartbeat fresh."""
    stop_event = threading.Event()

    def heartbeat_loop() -> None:
        while not stop_event.wait(PIPELINE_HEARTBEAT_SECONDS):
            set_stage("collect_hh")

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        name="pipeline-collect-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()

    try:
        # hh_collect_optimized.py preserves hh_collect.py's own 120-second
        # Playwright/Chromium watchdog. The old 25-minute supervisor timeout
        # could kill a healthy long collection, so no wall-clock cutoff here.
        return run_python(
            "hh_collect_optimized.py",
            extra_env={"HH_COLLECT_HEADLESS": "true"},
            log_filename="collector.log",
            timeout_seconds=None,
        )
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=2)


def main() -> int:
    started_at = now_iso()
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
    )

    log("PIPELINE START")
    notify("▶️ HH Agent: pipeline запущен.\nЭтап: подготовка.")

    try:
        with AgentLock():
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
                if collect_code != 0:
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
            process_code = run_python(
                "process_vacancies.py",
                log_filename="processor.log",
                timeout_seconds=40 * 60,
            )
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
            write_state(
                PIPELINE_STATE,
                status="skipped",
                stage="lock",
                finished_at=now_iso(),
                exit_code=0,
                last_error="agent_lock_busy",
            )
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
