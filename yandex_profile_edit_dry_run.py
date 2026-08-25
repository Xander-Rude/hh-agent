from __future__ import annotations

from playwright.sync_api import Page, sync_playwright

from yandex_apply_dry_run import (
    click_apply,
    pick_vacancy,
    print_form_inventory,
)
from yandex_browser import PROFILE_DIR, get_page, is_yandex_authenticated


HEADLESS = False


def click_edit(page: Page) -> bool:
    candidates = [
        page.get_by_role("button", name="Редактировать", exact=True),
        page.get_by_role("button", name="Редактировать", exact=False),
        page.get_by_role("link", name="Редактировать", exact=True),
        page.get_by_role("link", name="Редактировать", exact=False),
        page.locator("button").filter(has_text="Редактировать"),
    ]

    for locator in candidates:
        try:
            count = locator.count()
        except Exception:
            continue

        for index in range(count):
            item = locator.nth(index)
            try:
                if item.is_visible():
                    item.scroll_into_view_if_needed()
                    item.click()
                    page.wait_for_timeout(2000)
                    return True
            except Exception:
                continue

    return False


def print_file_inputs(page: Page) -> None:
    print("\n[FILES] file inputs после «Редактировать»:")
    total = 0

    for frame_index, frame in enumerate(page.frames):
        scope_name = "PAGE" if frame == page.main_frame else f"FRAME[{frame_index}]"
        locator = frame.locator('input[type="file"]')

        try:
            count = locator.count()
        except Exception:
            count = 0

        if count == 0:
            continue

        total += count
        print(f"  {scope_name}: {count}")

        for index in range(count):
            field = locator.nth(index)
            try:
                print(
                    f"    [{index}] visible={field.is_visible()} "
                    f"name={field.get_attribute('name')} "
                    f"accept={field.get_attribute('accept')} "
                    f"multiple={field.get_attribute('multiple')}"
                )
            except Exception as exc:
                print(f"    [{index}] inspect failed: {type(exc).__name__}: {exc}")

    if total == 0:
        print("  <не найдено>")


def main() -> int:
    vacancy, evaluation = pick_vacancy()

    print("=" * 80)
    print("YANDEX PROFILE EDIT DRY-RUN — СОХРАНЕНИЕ И ОТПРАВКА ОТКЛЮЧЕНЫ")
    print("=" * 80)
    print(f"Vacancy ID: {vacancy.id}")
    print(f"Вакансия: {vacancy.title}")
    print(f"Decision: {evaluation.decision}")
    print(f"URL: {vacancy.url}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=HEADLESS,
            viewport={"width": 1440, "height": 1000},
        )

        try:
            page = get_page(context)
            page.goto(vacancy.url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)

            if not is_yandex_authenticated(page, context):
                print("[ERROR] Сессия Яндекса не авторизована")
                return 4

            if not click_apply(page):
                print("[ERROR] Не нашёл кнопку «Откликнуться»")
                return 5

            print("[OK] Блок отклика открыт")

            if not click_edit(page):
                print("[ERROR] Не нашёл видимую кнопку «Редактировать»")
                print_form_inventory(page)
                input("\nОсмотрите браузер и нажмите Enter для выхода...")
                return 6

            print(f"[OK] «Редактировать» нажато. URL: {page.url}")
            print_form_inventory(page)
            print_file_inputs(page)

            print("\n[SAFE] Ничего не загружаю, не сохраняю и не отправляю.")
            input("\nОсмотрите открытый редактор и нажмите Enter для выхода...")
            return 0

        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
