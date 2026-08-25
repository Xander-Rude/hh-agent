from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from app.application_assets import validate_application_assets
from yandex_apply_dry_run import click_apply, ensure_resume_selection, pick_vacancy
from yandex_browser import PROFILE_DIR, get_page, is_yandex_authenticated
from yandex_profile_edit_dry_run import click_edit, _profile_form, save_profile


HEADLESS = False
FORMAT_HINT_RE = re.compile(r"PDF.*DOC.*DOCX.*TXT.*RTF.*10\s*МБ", re.IGNORECASE)


def remove_current_resume(page: Page) -> bool:
    form = _profile_form(page)
    if form is None:
        print("[ERROR] Форма профиля не найдена")
        return False

    button = form.get_by_role("button", name="Удалить файл резюме", exact=True)
    try:
        count = button.count()
    except Exception as exc:
        print(f"[ERROR] Не удалось проверить кнопку удаления: {type(exc).__name__}: {exc}")
        return False

    print(f"[RESUME] Кнопок «Удалить файл резюме»: {count}")
    if count != 1:
        button = form.locator('button[aria-label="Удалить файл резюме"]')
        try:
            count = button.count()
        except Exception:
            count = 0
        print(f"[RESUME] CSS fallback кнопок удаления: {count}")

    if count != 1:
        print("[ERROR] Ожидалась ровно одна кнопка удаления резюме")
        return False

    try:
        target = button.first
        print(f"[RESUME] aria-label={target.get_attribute('aria-label')!r}")
        target.click(timeout=3000)
        page.wait_for_timeout(900)
        print("[OK] Старый файл убран из несохранённой формы")
        return True
    except Exception as exc:
        print(f"[ERROR] Не удалось удалить старый файл из формы: {type(exc).__name__}: {exc}")
        return False


def dump_empty_resume_state(page: Page) -> None:
    form = _profile_form(page)
    print("\n[EMPTY STATE]")
    if form is None:
        print("[ERROR] Форма профиля после удаления не найдена")
        return

    try:
        inputs = form.locator('input[type="file"]')
        print(f"input[type=file]: {inputs.count()}")
        for i in range(inputs.count()):
            field = inputs.nth(i)
            print(
                f"  [{i}] name={field.get_attribute('name')!r} "
                f"accept={field.get_attribute('accept')!r} "
                f"class={field.get_attribute('class')!r}"
            )
    except Exception as exc:
        print(f"input inspect failed: {type(exc).__name__}: {exc}")

    try:
        text = " ".join(form.inner_text(timeout=2000).split())
        print(f"TEXT: {text[-1200:]}")
    except Exception:
        pass

    try:
        html = " ".join(str(form.evaluate("el => el.outerHTML")).split())
        marker = html.lower().find("pdf")
        if marker >= 0:
            start = max(0, marker - 1400)
            end = min(len(html), marker + 3800)
            print(f"HTML: {html[start:end]}")
        else:
            print(f"HTML: {html[:5000]}")
    except Exception as exc:
        print(f"HTML inspect failed: {type(exc).__name__}: {exc}")


def _input_file_name(field) -> str | None:
    try:
        return field.evaluate("el => el.files && el.files.length ? el.files[0].name : null")
    except Exception:
        return None


def upload_new_resume(page: Page, resume_path: Path):
    form = _profile_form(page)
    if form is None:
        return None

    inputs = form.locator('input[type="file"]')
    try:
        count = inputs.count()
    except Exception:
        count = 0

    print(f"[RESUME] input[type=file] после удаления: {count}")
    if count:
        for i in range(count):
            field = inputs.nth(i)
            try:
                field.set_input_files(str(resume_path))
                page.wait_for_timeout(1200)
                actual_name = _input_file_name(field)
                print(f"[OK] Новый PDF установлен через file input: {resume_path.name}")
                print(f"[VERIFY] input.files[0].name={actual_name!r}")
                if actual_name == resume_path.name:
                    return field
                print("[WARN] Имя файла внутри input не совпало")
            except Exception as exc:
                print(f"[WARN] file[{i}] не принял PDF: {type(exc).__name__}: {exc}")

    hints = form.get_by_text(FORMAT_HINT_RE)
    try:
        hint_count = hints.count()
    except Exception:
        hint_count = 0

    print(f"[RESUME] Подсказок форматов: {hint_count}")
    for i in range(hint_count):
        hint = hints.nth(i)
        for level in range(0, 4):
            control = hint if level == 0 else hint.locator("xpath=" + "/.." * level)
            try:
                if not control.is_visible():
                    continue
                tag = control.evaluate("el => el.tagName")
                cls = control.get_attribute("class") or ""
                print(f"[TRY] format-zone level={level} tag={tag} class={cls[:140]!r}")
                with page.expect_file_chooser(timeout=2000) as chooser_info:
                    control.click(timeout=1600)
                chooser_info.value.set_files(str(resume_path))
                page.wait_for_timeout(1200)
                print(f"[OK] Новый PDF установлен через file chooser: {resume_path.name}")

                refreshed_form = _profile_form(page)
                if refreshed_form is None:
                    return True
                refreshed_inputs = refreshed_form.locator('input[type="file"]')
                try:
                    for j in range(refreshed_inputs.count()):
                        actual_name = _input_file_name(refreshed_inputs.nth(j))
                        if actual_name:
                            print(f"[VERIFY] input.files[0].name={actual_name!r}")
                            if actual_name == resume_path.name:
                                return refreshed_inputs.nth(j)
                except Exception:
                    pass

                return True
            except PlaywrightTimeoutError:
                continue
            except Exception as exc:
                print(f"  [SKIP] {type(exc).__name__}: {exc}")

    return None


def verify_new_resume(page: Page, resume_path: Path, uploaded_field) -> bool:
    if uploaded_field is not None and uploaded_field is not True:
        actual_name = _input_file_name(uploaded_field)
        ok = actual_name == resume_path.name
        print(f"[VERIFY] Файл подтверждён через input.files: {ok}")
        if ok:
            return True

    form = _profile_form(page)
    if form is not None:
        inputs = form.locator('input[type="file"]')
        try:
            for i in range(inputs.count()):
                actual_name = _input_file_name(inputs.nth(i))
                if actual_name:
                    print(f"[VERIFY] file[{i}].files[0].name={actual_name!r}")
                    if actual_name == resume_path.name:
                        return True
        except Exception:
            pass

        try:
            text = form.inner_text(timeout=2000)
        except Exception:
            text = ""
        text_ok = resume_path.name.lower() in text.lower()
        print(f"[VERIFY] Новое имя видно в редакторе: {text_ok}")
        if text_ok:
            return True

    print("[ERROR] Новый PDF не подтверждён ни через input.files, ни через DOM")
    return False


def main() -> int:
    vacancy, evaluation = pick_vacancy()
    resume_key, resume_title = ensure_resume_selection(vacancy, evaluation)
    resume_path, _ = validate_application_assets(resume_key, resume_title)

    print("=" * 80)
    print("YANDEX PROFILE REPLACE V2 — ОТКЛИК НЕ ОТПРАВЛЯЕТСЯ")
    print("=" * 80)
    print(f"Vacancy: {vacancy.title}")
    print(f"Resume: {resume_path}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=HEADLESS,
            viewport={"width": 1440, "height": 1000},
        )
        try:
            page = get_page(context)
            page.goto(vacancy.url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1400)

            if not is_yandex_authenticated(page, context):
                print("[ERROR] Сессия Яндекса не авторизована")
                return 4
            if not click_apply(page):
                print("[ERROR] Не найден «Откликнуться»")
                return 5
            if not click_edit(page):
                print("[ERROR] Не найден «Редактировать»")
                return 6

            print("[OK] Редактор открыт")

            if not remove_current_resume(page):
                input("\nНажмите Enter для выхода...")
                return 7

            dump_empty_resume_state(page)

            uploaded_field = upload_new_resume(page, resume_path)
            if uploaded_field is None:
                print("[ERROR] Новый PDF не загружен. Профиль НЕ сохраняется.")
                input("\nНажмите Enter для выхода...")
                return 8

            if not verify_new_resume(page, resume_path, uploaded_field):
                print("[ERROR] Новый PDF не подтверждён. Профиль НЕ сохраняется.")
                input("\nНажмите Enter для выхода...")
                return 9

            saved = save_profile(page, resume_path)
            print("\n" + "=" * 80)
            print("resume_replaced=True")
            print(f"profile_saved={saved}")
            print("application_submitted=False")
            print("=" * 80)
            input("\nПроверьте карточку и нажмите Enter для выхода...")
            return 0 if saved else 10
        finally:
            try:
                context.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
