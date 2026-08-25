from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

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


def _profile_form(page: Page):
    forms = page.locator("form")
    try:
        count = forms.count()
    except Exception:
        return None

    for index in range(count):
        form = forms.nth(index)
        try:
            text = form.inner_text(timeout=1000)
        except Exception:
            continue

        if "Резюме" in text and "Сохранить данные" in text:
            return form

    return None


def _current_resume_filename(page: Page) -> str | None:
    form = _profile_form(page)
    if form is None:
        return None

    try:
        text = form.inner_text(timeout=2000)
    except Exception:
        return None

    match = re.search(
        r"([^\n\r]+\.(?:pdf|docx?|rtf|txt))",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    return match.group(1).strip()


def _ensure_file_mode(page: Page) -> None:
    form = _profile_form(page)
    if form is None:
        return

    radios = form.locator('input[type="radio"]')
    try:
        count = radios.count()
    except Exception:
        count = 0

    if count:
        try:
            if not radios.nth(0).is_checked():
                radios.nth(0).check()
                page.wait_for_timeout(500)
            print(f"[RESUME] Режим «Файл»: {radios.nth(0).is_checked()}")
            return
        except Exception:
            pass

    try:
        file_label = form.get_by_text("Файл", exact=True)
        if file_label.count() and file_label.first.is_visible():
            file_label.first.click()
            page.wait_for_timeout(500)
            print("[RESUME] Нажат переключатель «Файл»")
    except Exception:
        pass


def _candidate_resume_controls(page: Page, current_name: str | None):
    form = _profile_form(page)
    if form is None:
        return []

    candidates = []

    if current_name:
        by_name = form.get_by_text(current_name, exact=True)
        try:
            if by_name.count():
                candidates.append(("имя текущего файла", by_name.first))

                clickable_ancestor = by_name.first.locator(
                    "xpath=ancestor::*[self::button or self::label or @role='button' or @tabindex][1]"
                )
                if clickable_ancestor.count():
                    candidates.append(
                        ("кликабельный контейнер текущего файла", clickable_ancestor.first)
                    )

                parent = by_name.first.locator("xpath=..")
                if parent.count():
                    candidates.append(("контейнер текущего файла", parent.first))
        except Exception:
            pass

    for selector, label in (
        ("label", "label внутри формы"),
        ('[role="button"]', "role=button внутри формы"),
        ('[tabindex="0"]', "tabindex=0 внутри формы"),
    ):
        locator = form.locator(selector)
        try:
            count = locator.count()
        except Exception:
            continue

        for index in range(min(count, 12)):
            item = locator.nth(index)
            try:
                text = " ".join((item.inner_text(timeout=300) or "").split())
            except Exception:
                text = ""

            if (
                "Сохранить данные" in text
                or "Отменить" in text
                or "Скачать" in text
            ):
                continue

            candidates.append((f"{label}[{index}] text={text[:80]!r}", item))

    return candidates


def _print_resume_control_html(page: Page, current_name: str | None) -> None:
    print("\n[RESUME] Диагностика текущего контрола:")
    if not current_name:
        print("  текущее имя файла в форме не найдено")
        return

    form = _profile_form(page)
    if form is None:
        print("  форма профиля не найдена")
        return

    locator = form.get_by_text(current_name, exact=True)
    try:
        if not locator.count():
            print("  элемент с именем файла не найден")
            return

        element = locator.first
        print(f"  current={current_name}")
        for level in range(4):
            target = element if level == 0 else element.locator("xpath=" + "/.." * level)
            try:
                html = target.evaluate("el => el.outerHTML")
                html = " ".join(str(html).split())
                print(f"  level={level}: {html[:700]}")
            except Exception:
                continue
    except Exception as exc:
        print(f"  inspect failed: {type(exc).__name__}: {exc}")


def choose_resume_via_filechooser(page: Page, resume_path: Path) -> bool:
    _ensure_file_mode(page)
    current_name = _current_resume_filename(page)
    print(f"[RESUME] Текущий файл: {current_name or '<не определён>'}")
    print(f"[RESUME] Загружаю локальную версию заново: {resume_path.name}")

    candidates = _candidate_resume_controls(page, current_name)
    print(f"[RESUME] Кандидатов на открытие file chooser: {len(candidates)}")

    for label, control in candidates:
        try:
            if not control.is_visible():
                continue

            control.scroll_into_view_if_needed()
            print(f"[TRY] {label}")

            try:
                with page.expect_file_chooser(timeout=1800) as chooser_info:
                    control.click(timeout=1500)
                chooser = chooser_info.value
            except PlaywrightTimeoutError:
                continue
            except Exception as exc:
                print(f"  [SKIP] {type(exc).__name__}: {exc}")
                continue

            chooser.set_files(str(resume_path))
            page.wait_for_timeout(1500)
            print(f"[OK] File chooser принял: {resume_path.name}")

            form = _profile_form(page)
            try:
                form_text = form.inner_text(timeout=2000) if form is not None else ""
            except Exception:
                form_text = ""

            if resume_path.name.lower() in form_text.lower():
                print("[OK] Новое имя файла появилось в форме")
                return True

            print(
                "[INFO] File chooser получил PDF, но полное имя пока не видно в тексте формы."
            )
            return True
        except Exception as exc:
            print(f"  [SKIP] Ошибка кандидата {label}: {type(exc).__name__}: {exc}")
            continue

    _print_resume_control_html(page, current_name)
    print("[ERROR] Не удалось вызвать file chooser через элементы блока «Резюме»")
    return False


def save_profile(page: Page, resume_path: Path) -> bool:
    form = _profile_form(page)
    if form is None:
        print("[ERROR] Перед сохранением не найдена форма профиля")
        return False

    buttons = [
        form.get_by_role("button", name="Сохранить данные", exact=True),
        form.locator('button[type="submit"]').filter(has_text="Сохранить данные"),
        form.locator('button[type="submit"]'),
    ]

    save_button = None
    for locator in buttons:
        try:
            if locator.count() and locator.first.is_visible():
                save_button = locator.first
                break
        except Exception:
            continue

    if save_button is None:
        print("[ERROR] Не найдена кнопка «Сохранить данные»")
        return False

    try:
        save_button.scroll_into_view_if_needed()
        print("[STEP] Сохраняю обновлённое резюме в профиле Яндекса...")
        save_button.click()
        page.wait_for_timeout(2500)
    except Exception as exc:
        print(f"[ERROR] Ошибка сохранения профиля: {type(exc).__name__}: {exc}")
        return False

    edit_visible = False
    try:
        edit = page.get_by_role("button", name="Редактировать", exact=True)
        edit_visible = edit.count() > 0 and edit.first.is_visible()
    except Exception:
        pass

    try:
        body_text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        body_text = ""

    filename_visible = resume_path.name.lower() in body_text.lower()

    print(f"[VERIFY] Редактор закрылся: {edit_visible}")
    print(f"[VERIFY] Имя сохранённого файла видно в карточке: {filename_visible}")

    if not edit_visible:
        print("[ERROR] После сохранения редактор не вернулся в режим просмотра")
        return False

    if not filename_visible:
        print(
            "[WARN] Полное имя файла не найдено в тексте карточки после сохранения. "
            "Возможно, Яндекс сокращает имя; проверьте карточку глазами."
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
    print("YANDEX PROFILE SAVE DRY-RUN — ОТПРАВКА ОТКЛИКА ОТКЛЮЧЕНА")
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

            resume_ok = choose_resume_via_filechooser(page, resume_path)
            profile_saved = False

            if resume_ok:
                profile_saved = save_profile(page, resume_path)

            print("\n" + "=" * 80)
            print("PROFILE SAVE DRY-RUN RESULT")
            print(f"resume_selected={resume_ok}")
            print(f"profile_saved={profile_saved}")
            print("application_submitted=False")
            print(
                "[SAFE] Резюме в профиле сохраняется, но «Отправить отклик» НЕ нажимается. "
                "Презентация пока НЕ загружается."
            )
            print("=" * 80)

            input("\nПроверьте карточку профиля в браузере и нажмите Enter для выхода...")
            return 0 if resume_ok and profile_saved else 8

        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
