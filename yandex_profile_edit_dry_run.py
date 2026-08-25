from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from app.application_assets import validate_application_assets
from yandex_apply_dry_run import (
    click_apply,
    ensure_resume_selection,
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


def print_file_inputs(page: Page) -> int:
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

    return total


def _find_resume_file_input(page: Page):
    candidates = []

    for frame in page.frames:
        locator = frame.locator('input[type="file"]')
        try:
            count = locator.count()
        except Exception:
            continue

        for index in range(count):
            field = locator.nth(index)
            try:
                accept = (field.get_attribute("accept") or "").lower()
                name = (field.get_attribute("name") or "").lower()

                score = 0
                if "pdf" in accept:
                    score += 3
                if any(ext in accept for ext in ("doc", "txt", "rtf")):
                    score += 2
                if "resume" in name or "cv" in name:
                    score += 5

                candidates.append((score, field))
            except Exception:
                continue

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def upload_resume_without_saving(page: Page, resume_path: Path) -> bool:
    field = _find_resume_file_input(page)
    if field is None:
        print("[ERROR] Не найден input[type=file] для резюме")
        return False

    try:
        field.set_input_files(str(resume_path))
        page.wait_for_timeout(1200)
        print(f"[OK] Выбран файл резюме: {resume_path.name}")
    except Exception as exc:
        print(f"[ERROR] Не удалось выбрать файл резюме: {type(exc).__name__}: {exc}")
        return False

    # Проверяем, что имя файла появилось в DOM/тексте страницы. Некоторые
    # кастомные file input не показывают value напрямую, поэтому это только
    # диагностическая проверка, а не сохранение данных.
    try:
        body_text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        body_text = ""

    if resume_path.name.lower() in body_text.lower():
        print("[OK] Имя выбранного файла видно в интерфейсе")
    else:
        print(
            "[INFO] Файл передан в input, но его имя не найдено в тексте страницы; "
            "проверьте интерфейс глазами."
        )

    return True


def main() -> int:
    vacancy, evaluation = pick_vacancy()
    resume_key, resume_title = ensure_resume_selection(vacancy, evaluation)
    resume_path, presentation_path = validate_application_assets(
        resume_key,
        resume_title,
    )

    print("=" * 80)
    print("YANDEX PROFILE EDIT DRY-RUN — СОХРАНЕНИЕ И ОТПРАВКА ОТКЛЮЧЕНЫ")
    print("=" * 80)
    print(f"Vacancy ID: {vacancy.id}")
    print(f"Вакансия: {vacancy.title}")
    print(f"Decision: {evaluation.decision}")
    print(f"Resume key: {resume_key}")
    print(f"Resume title: {resume_title}")
    print(f"Resume file: {resume_path}")
    print(f"Presentation: {presentation_path}")
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
            file_count = print_file_inputs(page)

            if file_count == 0:
                print("[ERROR] После «Редактировать» не найдено поле загрузки файла")
                input("\nОсмотрите браузер и нажмите Enter для выхода...")
                return 7

            resume_ok = upload_resume_without_saving(page, resume_path)

            print("\n" + "=" * 80)
            print("PROFILE EDIT DRY-RUN RESULT")
            print(f"resume_selected={resume_ok}")
            print("profile_saved=False")
            print("application_submitted=False")
            print(
                "[SAFE] «Сохранить данные» и «Отправить отклик» НЕ нажимаются. "
                "Презентация пока НЕ загружается."
            )
            print("=" * 80)

            input("\nПроверьте выбранный файл в браузере и нажмите Enter для выхода...")
            return 0 if resume_ok else 8

        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
