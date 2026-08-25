from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright
from sqlalchemy import select

from app.db import Application, SessionLocal, Vacancy
from yandex_apply_dry_run import find_application_frame
from yandex_browser import PROFILE_DIR, get_page, is_yandex_authenticated


APPLIED_MARKERS = (
    "отклик отправлен",
    "спасибо за отклик",
    "спасибо за ваш отклик",
    "мы получили ваш отклик",
    "ваш отклик отправлен",
)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python probe_yandex_application_state.py <application_id>")
        return 2

    application_id = int(sys.argv[1])

    session = SessionLocal()
    try:
        row = session.execute(
            select(Application, Vacancy)
            .join(Vacancy, Vacancy.id == Application.vacancy_id)
            .where(Application.id == application_id)
            .limit(1)
        ).first()

        if row is None:
            print(f"[ERROR] Application ID={application_id} не найдена")
            return 3

        application, vacancy = row
        vacancy_url = vacancy.url
        vacancy_title = vacancy.title
        source = vacancy.source
        status = application.status
    finally:
        session.close()

    print("=" * 80)
    print("YANDEX APPLICATION STATE PROBE — НИЧЕГО НЕ ОТПРАВЛЯЕТ")
    print("=" * 80)
    print(f"Application ID: {application_id}")
    print(f"DB status: {status!r}")
    print(f"Vacancy: {vacancy_title}")
    print(f"Source: {source!r}")
    print(f"URL: {vacancy_url}")

    if (source or "").strip().lower() != "yandex":
        print("[ERROR] Это не Yandex-вакансия")
        return 4

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1440, "height": 1000},
        )
        try:
            page = get_page(context)
            page.goto(vacancy_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1800)

            if not is_yandex_authenticated(page, context):
                print("[ERROR] Сессия Яндекса не авторизована")
                return 5

            chunks: list[str] = []
            try:
                chunks.append(page.locator("body").inner_text(timeout=3000))
            except Exception:
                pass

            frame = find_application_frame(page)
            if frame is not None:
                try:
                    chunks.append(frame.locator("body").inner_text(timeout=3000))
                except Exception:
                    pass

            text = "\n".join(chunks).lower()
            applied_marker = next((m for m in APPLIED_MARKERS if m in text), None)

            apply_count = 0
            try:
                apply_count = page.get_by_role("button", name="Откликнуться", exact=False).count()
            except Exception:
                pass

            print("-" * 80)
            print(f"applied_marker: {applied_marker!r}")
            print(f"apply_button_count: {apply_count}")
            print(f"forms_yandex_iframe_present: {frame is not None}")

            if applied_marker:
                print("RESULT=already_applied")
                print("[SAFE] Повторно отправлять нельзя.")
                return 0

            if apply_count > 0:
                print("RESULT=not_applied_apply_available")
                print("[SAFE] На странице есть обычный «Откликнуться»; submit не нажимался этим probe.")
                return 0

            if frame is not None:
                print("RESULT=form_open_or_ambiguous")
                print("[SAFE] Форма присутствует, но однозначного статуса нет. Ничего не нажималось.")
                return 0

            print("RESULT=ambiguous")
            print("[SAFE] Однозначно определить состояние не удалось. Ничего не нажималось.")
            return 0
        finally:
            try:
                context.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
