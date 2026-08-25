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
                    item.click(timeout=3000)
                    page.wait_for_timeout(1600)
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


def _ensure_file_mode(page: Page) -> bool:
    form = _profile_form(page)
    if form is None:
        return False

    radios = form.locator('input[type="radio"]')
    try:
        if radios.count() >= 1:
            file_radio = radios.nth(0)
            if not file_radio.is_checked():
                file_radio.check(timeout=2500)
                page.wait_for_timeout(400)
            checked = file_radio.is_checked()
            print(f"[RESUME] Режим «Файл»: {checked}")
            return checked
    except Exception:
        pass

    try:
        label = form.get_by_text("Файл", exact=True)
        if label.count() and label.first.is_visible():
            label.first.click(timeout=2500)
            page.wait_for_timeout(400)
            print("[RESUME] Нажат режим «Файл»")
            return True
    except Exception:
        pass

    return False


def _remove_current_resume(page: Page) -> bool:
    """Удаляет текущий файл только из редактируемой формы.

    Без последующего «Сохранить данные» изменение не должно сохраняться.
    Кнопка подтверждена реальным DOM Яндекса: aria-label="удалить файл резюме".
    """
    form = _profile_form(page)
    if form is None:
        return False

    selectors = [
        'button[aria-label="удалить файл резюме"]',
        'button[aria-label*="удалить файл резюме" i]',
        'button[aria-label*="удалить" i]',
    ]

    for selector in selectors:
        button = form.locator(selector)
        try:
            count = button.count()
        except Exception:
            continue

        for index in range(count):
            item = button.nth(index)
            try:
                if not item.is_visible():
                    continue
                aria = item.get_attribute("aria-label") or ""
                if "резюме" not in aria.lower():
                    continue
                print(f"[RESUME] Нажимаю кнопку удаления текущего файла: {aria!r}")
                item.click(timeout=3000)
                page.wait_for_timeout(1000)
                return True
            except Exception as exc:
                print(f"[WARN] Не удалось нажать удаление файла: {type(exc).__name__}: {exc}")

    print("[WARN] Кнопка «удалить файл резюме» не найдена")
    return False


def _try_direct_file_input(page: Page, resume_path: Path) -> bool:
    form = _profile_form(page)
    if form is None:
        return False

    fields = form.locator('input[type="file"]')
    try:
        count = fields.count()
    except Exception:
        count = 0

    print(f"[RESUME] input[type=file] после удаления старого файла: {count}")

    for index in range(count):
        field = fields.nth(index)
        try:
            accept = field.get_attribute("accept") or ""
            print(f"[RESUME] file[{index}] accept={accept!r}")
            field.set_input_files(str(resume_path))
            page.wait_for_timeout(1200)
            print(f"[OK] Новый PDF передан напрямую: {resume_path.name}")
            return True
        except Exception as exc:
            print(f"[WARN] file[{index}] не принял файл: {type(exc).__name__}: {exc}")

    return False


def _try_visible_upload_zone(page: Page, resume_path: Path) -> bool:
    """Кликает только по подтверждённой визуальной зоне загрузки.

    На форме она содержит текст вида «PDF, DOC, DOCX, TXT, RTF до 10 МБ».
    Никакие ссылки на текущий файл и кнопки «Скачать» здесь не используются.
    """
    form = _profile_form(page)
    if form is None:
        return False

    text_re = re.compile(r"PDF\s*,?\s*DOC.*DOCX.*TXT.*RTF.*10\s*МБ", re.IGNORECASE)
    text_nodes = form.get_by_text(text_re)

    try:
        count = text_nodes.count()
    except Exception:
        count = 0

    print(f"[RESUME] Зон с подсказкой форматов: {count}")

    candidates = []
    for index in range(count):
        node = text_nodes.nth(index)
        candidates.append(("текст подсказки форматов", node))
        for level in range(1, 4):
            candidates.append(
                (
                    f"родитель подсказки level={level}",
                    node.locator("xpath=" + "/.." * level),
                )
            )

    seen = set()
    for label, control in candidates:
        try:
            if not control.is_visible():
                continue
            key = control.evaluate("el => el.tagName + '|' + el.className + '|' + (el.textContent || '')")
            if key in seen:
                continue
            seen.add(key)

            print(f"[TRY] upload-zone: {label}")
            try:
                with page.expect_file_chooser(timeout=2200) as chooser_info:
                    control.click(timeout=1800)
                chooser = chooser_info.value
            except PlaywrightTimeoutError:
                continue

            chooser.set_files(str(resume_path))
            page.wait_for_timeout(1200)
            print(f"[OK] File chooser принял: {resume_path.name}")
            return True
        except Exception as exc:
            print(f"  [SKIP] {label}: {type(exc).__name__}: {exc}")

    return False


def _verify_selected_filename(page: Page, resume_path: Path) -> bool:
    form = _profile_form(page)
    if form is None:
        return False

    try:
        text = form.inner_text(timeout=2000)
    except Exception:
        text = ""

    visible = resume_path.name.lower() in text.lower()
    print(f"[VERIFY] Новое имя файла видно до сохранения: {visible}")
    if not visible:
        compact = " ".join(text.split())
        print(f"[VERIFY] Хвост текста формы: {compact[-900:]}")
    return visible


def _print_resume_debug(page: Page) -> None:
    form = _profile_form(page)
    print("\n[RESUME] Диагностика формы после удаления старого файла:")
    if form is None:
        print("  <форма не найдена>")
        return

    try:
        html = form.evaluate("el => el.outerHTML")
        compact = " ".join(str(html).split())
        marker = compact.lower().find("pdf")
        if marker >= 0:
            start = max(0, marker - 1800)
            end = min(len(compact), marker + 3500)
            print(f"  {compact[start:end]}")
        else:
            print(f"  {compact[:5500]}")
    except Exception as exc:
        print(f"  inspect failed: {type(exc).__name__}: {exc}")


def choose_resume(page: Page, resume_path: Path) -> bool:
    _ensure_file_mode(page)
    current_name = _current_resume_filename(page)
    print(f"[RESUME] Текущий файл: {current_name or '<не определён>'}")
    print(f"[RESUME] Загружаю локальную версию заново: {resume_path.name}")

    # Если файл уже есть, сначала убираем его из несохранённой формы.
    # Это открывает штатную пустую зону загрузки Яндекса.
    if current_name:
        if not _remove_current_resume(page):
            print("[ERROR] Не смог убрать текущий файл, замену не выполняю")
            return False

    uploaded = _try_direct_file_input(page, resume_path)
    if not uploaded:
        uploaded = _try_visible_upload_zone(page, resume_path)

    if not uploaded:
        _print_resume_debug(page)
        print("[ERROR] Новый файл не удалось передать в загрузчик")
        return False

    verified = _verify_selected_filename(page, resume_path)
    if not verified:
        print("[WARN] Файл передан загрузчику, но его имя не подтвердилось в тексте формы")

    return True


def save_profile(page: Page, resume_path: Path) -> bool:
    form = _profile_form(page)
    if form is None:
        print("[ERROR] Перед сохранением не найдена форма профиля")
        return False

    candidates = [
        form.get_by_role("button", name="Сохранить данные", exact=True),
        form.locator('button[type="submit"]').filter(has_text="Сохранить данные"),
        form.locator('button[type="submit"]'),
    ]

    save_button = None
    for locator in candidates:
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
        print("[STEP] Сохраняю обновлённое резюме в профиле Яндекса...")
        save_button.click(timeout=3500)
        page.wait_for_timeout(3000)
    except Exception as exc:
        print(f"[ERROR] Ошибка сохранения профиля: {type(exc).__name__}: {exc}")
        return False

    try:
        edit = page.get_by_role("button", name="Редактировать", exact=True)
        edit_visible = edit.count() > 0 and edit.first.is_visible()
    except Exception:
        edit_visible = False

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

            resume_ok = choose_resume(page, resume_path)
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
            try:
                context.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
