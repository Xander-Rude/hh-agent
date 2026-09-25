from __future__ import annotations

import os
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright
from sqlalchemy import select

from app.db import Application, SessionLocal, Vacancy
from app.external_apply_policy import approved_for_dispatch


load_dotenv()

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
SUCCESS_LOG = LOG_DIR / "ozon_apply_worker.log"
ATTENTION_LOG = LOG_DIR / "ozon_apply_worker_attention.log"

HEADLESS = os.getenv("OZON_APPLY_HEADLESS", "true").lower() == "true"
LIVE = os.getenv("OZON_APPLY_LIVE", "false").lower() == "true"
MAX_PER_RUN = int(os.getenv("OZON_APPLY_MAX_PER_RUN", "5"))
TARGET_APPLICATION_ID = os.getenv("OZON_APPLY_APPLICATION_ID", "").strip()

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

ANTIBOT_MARKERS = (
    "antibot challenge",
    "похоже, нет соединения",
    "выключите vpn",
    "нам нужно убедиться, что вы не робот",
    "пожалуйста, включите javascript для продолжения",
    "fab_chlg_",
    "инцидент: fab_",
)


def set_status(application_id: int, status: str) -> None:
    if not LIVE:
        return

    session = SessionLocal()
    try:
        application = session.get(Application, application_id)
        if application is None:
            return
        application.status = status
        session.commit()
    finally:
        session.close()


def append_log(result: str, output: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = SUCCESS_LOG if result == "applied" else ATTENTION_LOG
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as file:
        file.write(f"\n[{stamp}] RESULT={result} LIVE={LIVE}\n")
        file.write(output.strip() or "No output captured.")
        file.write("\n")


def load_queue() -> list[tuple[Application, Vacancy]]:
    session = SessionLocal()
    try:
        query = (
            select(Application, Vacancy)
            .join(Vacancy, Vacancy.id == Application.vacancy_id)
            .where(
                Application.status == "approved",
                Vacancy.source == "ozon",
            )
            .order_by(Application.created_at.asc())
        )

        if TARGET_APPLICATION_ID:
            try:
                target_id = int(TARGET_APPLICATION_ID)
            except ValueError:
                print(
                    "[ERROR] OZON_APPLY_APPLICATION_ID "
                    "должен быть целым числом."
                )
                return []
            query = query.where(
                Application.id == target_id
            ).limit(1)
        else:
            query = query.limit(MAX_PER_RUN)

        rows = session.execute(query).all()
        result: list[tuple[Application, Vacancy]] = []
        for application, vacancy in rows:
            session.expunge(application)
            session.expunge(vacancy)
            result.append((application, vacancy))
        return result
    finally:
        session.close()


def page_text(page: Page) -> str:
    try:
        return " ".join(
            (page.locator("body").inner_text(timeout=4000) or "").split()
        ).lower()
    except Exception:
        return ""


def antibot_detected(page: Page) -> bool:
    title = ""
    try:
        title = page.title().lower()
    except Exception:
        pass

    text = page_text(page)
    probe = f"{title}\n{text}"
    return any(marker in probe for marker in ANTIBOT_MARKERS)


def process_application(
    page: Page,
    application: Application,
    vacancy: Vacancy,
) -> str:
    print("\n" + "=" * 80)
    print(f"{vacancy.title} | {vacancy.company or 'Ozon'}")
    print(vacancy.url)
    print(f"Application ID: {application.id}")
    print(f"LIVE: {LIVE}")

    if not approved_for_dispatch(application):
        print(
            f"[SAFE] Application status={application.status!r}; "
            "действие разрешено только после явного approve."
        )
        return "manual_required"

    if LIVE:
        set_status(application.id, "applying")

    try:
        page.goto(
            vacancy.url,
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        page.wait_for_timeout(1800)
    except Exception as exc:
        print(
            "[MANUAL] Не удалось открыть first-party Ozon: "
            f"{type(exc).__name__}: {exc}"
        )
        set_status(application.id, "manual_required")
        return "manual_required"

    if antibot_detected(page):
        print(
            "[MANUAL] Ozon first-party блокирует production egress "
            "антиботом. Автоматический submit не выполняю. "
            "Открой first-party ссылку вручную."
        )
        set_status(application.id, "manual_required")
        return "manual_required"

    # We intentionally do not guess Ozon's submit DOM. The production network
    # currently cannot reach the real form, so there is no verified selector
    # contract for a safe automatic submission.
    print(
        "[MANUAL] First-party Ozon открылся без известного антибота, "
        "но форма отклика ещё не верифицирована на production. "
        "Автоматический submit не выполняю."
    )
    set_status(application.id, "manual_required")
    return "manual_required"


def main() -> None:
    queue = load_queue()

    print("\n" + "=" * 80)
    print("OZON APPLY WORKER")
    print("=" * 80)
    print(f"В очереди approved/ozon: {len(queue)}")
    print(f"Headless: {HEADLESS}")
    print(f"LIVE: {LIVE}")

    if not queue:
        print("Отправлять нечего.")
        return

    stats: dict[str, int] = {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            ignore_https_errors=True,
            locale="ru-RU",
            user_agent=USER_AGENT,
            viewport={"width": 1440, "height": 1000},
        )
        try:
            page = context.new_page()

            for index, (application, vacancy) in enumerate(queue, start=1):
                output = StringIO()
                try:
                    with redirect_stdout(output):
                        print(f"[{index}/{len(queue)}]")
                        result = process_application(
                            page,
                            application,
                            vacancy,
                        )
                except Exception as exc:
                    result = "manual_required"
                    set_status(application.id, "manual_required")
                    with redirect_stdout(output):
                        print(
                            "[MANUAL] Необработанная ошибка: "
                            f"{type(exc).__name__}: {exc}"
                        )

                append_log(result, output.getvalue())
                print(output.getvalue(), end="")
                stats[result] = stats.get(result, 0) + 1
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass

    print("\n" + "=" * 80)
    print("OZON APPLY WORKER DONE")
    for key, value in stats.items():
        print(f"{key}: {value}")
    print("=" * 80)


if __name__ == "__main__":
    main()
