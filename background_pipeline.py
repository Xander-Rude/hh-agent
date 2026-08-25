from __future__ import annotations

import os
from datetime import datetime

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

load_dotenv()

BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or ""
).strip()

CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or ""
).strip()

TRIGGERED_BY_TELEGRAM = (
    os.getenv(
        "HH_TRIGGERED_BY_TELEGRAM",
        "false",
    ).lower()
    == "true"
)


def log(message: str) -> None:
    print(
        f"[{now_iso()}] {message}",
        flush=True,
    )
    append_log(
        "pipeline_supervisor.log",
        message,
    )


def notify(
    message: str,
) -> None:
    if (
        not TRIGGERED_BY_TELEGRAM
        or not BOT_TOKEN
        or not CHAT_ID
    ):
        return

    try:
        httpx.post(
            (
                "https://api.telegram.org/bot"
                f"{BOT_TOKEN}/sendMessage"
            ),
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

    write_state(
        PIPELINE_STATE,
        **values,
    )


def main() -> int:
    started_at = now_iso()

    write_state(
        PIPELINE_STATE,
        status="starting",
        stage="init",
        started_at=started_at,
        pid=os.getpid(),
        triggered_by=(
            "telegram"
            if TRIGGERED_BY_TELEGRAM
            else "scheduler"
        ),
        last_error=None,
    )

    log("PIPELINE START")
    notify(
        "▶️ HH Agent: pipeline запущен.\n"
        "Этап: подготовка."
    )

    try:
        with AgentLock():
            set_stage("collect_hh")

            notify(
                "🔎 HH Agent: собираю свежие вакансии HH..."
            )

            log("1/3 hh_collect.py")

            collect_code = run_python(
                "hh_collect.py",
                extra_env={
                    "HH_COLLECT_HEADLESS": "true",
                },
                log_filename="collector.log",
                timeout_seconds=25 * 60,
            )

            if collect_code != 0:
                message = (
                    "hh_collect.py failed "
                    f"with code={collect_code}"
                )

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

            set_stage("collect_yandex")

            notify(
                "🔎 HH Agent: собираю вакансии Yandex Jobs..."
            )

            log("2/3 yandex_collect.py")

            yandex_code = run_python(
                "yandex_collect.py",
                log_filename="yandex_collector.log",
                timeout_seconds=5 * 60,
            )

            if yandex_code != 0:
                # Yandex — дополнительный источник. Его временная недоступность
                # не должна блокировать обработку уже собранных вакансий HH.
                message = (
                    "yandex_collect.py failed "
                    f"with code={yandex_code}; continue pipeline"
                )
                log("WARN: " + message)
                notify(
                    "⚠️ HH Agent: Yandex Jobs временно не собран "
                    f"(code={yandex_code}). Продолжаю обработку HH.\n"
                    "Подробности: logs\\yandex_collector.log"
                )

            set_stage("process")

            notify(
                "🧠 HH Agent: сбор закончен, "
                "обрабатываю новые вакансии..."
            )

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
            log(
                "SKIP: another HH background job "
                "is still running"
            )

            write_state(
                PIPELINE_STATE,
                status="skipped",
                stage="lock",
                finished_at=now_iso(),
                exit_code=0,
                last_error="agent_lock_busy",
            )

            notify(
                "⏳ HH Agent: другой фоновый процесс "
                "уже работает. Новый pipeline не запущен."
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
        "✅ HH Agent: pipeline завершён успешно."
    )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(
            main()
        )
    except Exception as exc:
        message = (
            f"{type(exc).__name__}: {exc}"
        )

        write_state(
            PIPELINE_STATE,
            status="failed",
            stage="supervisor",
            finished_at=now_iso(),
            exit_code=99,
            last_error=message,
        )

        append_log(
            "pipeline_supervisor.log",
            "FATAL " + message,
        )

        notify(
            "❌ HH Agent: pipeline аварийно остановлен.\n"
            + message
        )

        raise
