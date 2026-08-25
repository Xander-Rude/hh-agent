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


def _radio_modes(page: Page):
    form = _profile_form(page)
    if form is None:
        return None, None

    radios = form.locator('input[type="radio"]')
    try:
        if radios.count() < 2:
            return None, None
        return radios.nth(0), radios.nth(1)
    except Exception:
        return None, None


def _ensure_file_mode(page: Page) -> None:
    file_radio, _ = _radio_modes(page)
    if file_radio is not None:
        try:
            if not file_radio.is_checked():
                file_radio.check()
                page.wait_for_timeout(600)
            print(f"[RESUME] Режим «Файл»: {file_radio.is_checked()}")
            return
        except Exception:
            pass

    form = _profile_form(page)
    if form is None:
        return

    try:
        file_label = form.get_by_text("Файл", exact=True)
        if file_label.count() and file_label.first.is_visible():
            file_label.first.click()
            page.wait_for_timeout(600)
            print("[RESUME] Нажат переключатель «Файл»")
    except Exception:
        pass


def _reset_file_mode(page: Page) -> bool:
    """Перерисовывает блок резюме: Ссылка -> Файл.

    У Яндекса при уже сохранённом резюме upload input не присутствует в DOM.
    Переключение режима заставляет React отрисовать контрол загрузки заново.
    """
    file_radio, link_radio = _radio_modes(page)

    if file_radio is not None and link_radio is not None:
        try:
            print("[RESUME] Переключаю «Ссылка» → «Файл», чтобы открыть загрузчик...")
            link_radio.check()
            page.wait_for_timeout(700)

            # После React rerender старые locator могут устареть — получаем заново.
            file_radio, _ = _radio_modes(page)
            if file_radio is None:
                return False

            file_radio.check()
            page.wait_for_timeout(900)
            print(f"[RESUME] Режим «Файл» после перерисовки: {file_radio.is_checked()}")
            return file_radio.is_checked()
        except Exception as exc:
            print(f"[WARN] Не удалось переключить radio: {type(exc).__name__}: {exc}")

    form = _profile_form(page)
    if form is None:
        return False

    try:
        link_label = form.get_by_text("Ссылка", exact=True)
        file_label = form.get_by_text("Файл", exact=True)
        if not link_label.count() or not file_label.count():
            return False

        print("[RESUME] Переключаю labels «Ссылка» → «Файл»...")
        link_label.first.click()
        page.wait_for_timeout(700)

        form = _profile_form(page)
        if form is None:
            return False
        file_label = form.get_by_text("Файл", exact=True)
        file_label.first.click()
        page.wait_for_timeout(900)
        return True
    except Exception as exc:
        print(f"[WARN] Не удалось переключить labels: {type(exc).__name__}: {exc}")
        return False


def _try_direct_file_input(page: Page, resume_path: Path) -> bool:
    form = _profile_form(page)
    if form is None:
        return False

    inputs = form.locator('input[type="file"]')
    try:
        count = inputs.count()
    except Exception:
        count = 0

    print(f"[RESUME] input[type=file] после перерисовки: {count}")
    if count == 0:
        return False

    for index in range(count):
        field = inputs.nth(index)
        try:
            print(
                f"[RESUME] file[{index}] visible={field.is_visible()} "
                f"name={field.get_attribute('name')} "
                f"accept={field.get_attribute('accept')}"
            )
            field.set_input_files(str(resume_path))
            page.wait_for_timeout(1500)
            print(f"[OK] PDF передан напрямую в file input: {resume_path.name}")
            return True
        except Exception as exc:
            print(f"[WARN] file[{index}] не принял файл: {type(exc).__name__}: {exc}")

    return False


def _candidate_resume_controls(page: Page):
    form = _profile_form(page)
    if form is None:
        return []

    candidates = []
    selectors = (
        ("label", "label"),
        ('[role="button"]', "role=button"),
        ('[tabindex="0"]', "tabindex=0"),
        ("button", "button"),
        ("div", "div"),
    )

    for selector, label in selectors:
        locator = form.locator(selector)
        try:
            count = locator.count()
        except Exception:
            continue

        for index in range(min(count, 30)):
            item = locator.nth(index)
            try:
                if not item.is_visible():
                    continue
                text = " ".join((item.inner_text(timeout=250) or "").split())
            except Exception:
                text = ""

            lowered = text.lower()
            if any(skip in text for skip in ("Сохранить данные", "Отменить", "Скачать")):
                continue

            if (
                "загруз" in lowered
                or "выбер" in lowered
                or "резюме" in lowered
                or "файл" in lowered
                or selector != "div"
            ):
                candidates.append((f"{label}[{index}] text={text[:100]!r}", item))

    return candidates


def _print_resume_area_html(page: Page) -> None:
    form = _profile_form(page)
    print("\n[RESUME] HTML формы после перерисовки:")
    if form is None:
        print("  <форма не найдена>")
        return

    try:
        html = form.evaluate("el => el.outerHTML")
        compact = " ".join(str(html).split())
        print(f"  {compact[:5000]}")
    except Exception as exc:
        print(f"  inspect failed: {type(exc).__name__}: {exc}")


def _try_filechooser_candidates(page: Page, resume_path: Path) -> bool:
    candidates = _candidate_resume_controls(page)
    print(f"[RESUME] Кандидатов на file chooser после перерисовки: {len(candidates)}")

    for label, control in candidates:
        try:
            if not control.is_visible():
                continue
            control.scroll_into_view_if_needed()
            print(f"[TRY] {label}")

            try:
                with page.expect_file_chooser(timeout=1500) as chooser_info:
                    control.click(timeout=1200)
                chooser = chooser_info.value
            except PlaywrightTimeoutError:
                continue
            except Exception as exc:
                print(f"  [SKIP] {type(exc).__name__}: {exc}")
                continue

            chooser.set_files(str(resume_path))
            page.wait_for_timeout(1500)
            print(f"[OK] File chooser принял: {resume_path.name}")
            return True
        except Exception as exc:
            print(f"  [SKIP] Ошибка кандидата {label}: {type(exc).__name__}: {exc}")

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
        print(f"[VERIFY] Текст блока резюме: {' '.join(text.split())[-900:]}")
    return visible


def choose_resume_via_filechooser(page: Page, resume_path: Path) -> bool:
    _ensure_file_mode(page)
    current_name = _current_resume_filename(page)
    print(f"[RESUME] Текущий файл: {current_name or '<не определён>'}")
    print(f"[RESUME] Всегда загружаю локальную версию заново: {resume_path.name}")

    if not _reset_file_mode(page):
        print("[WARN] Не удалось принудительно перерисовать блок резюме")

    # В первую очередь используем настоящий input, даже если он скрытый.
    uploaded = _try_direct_file_input(page, resume_path)

    # Если React создаёт input только по клику — ловим filechooser.
    if not uploaded:
        uploaded = _try_filechooser_candidates(page, resume_path)

    if not uploaded:
        _print_resume_area_html(page)
        print("[ERROR] После переключения «Ссылка» → «Файл» загрузчик не найден")
        return False

    # Имя может отображаться сокращённо, поэтому факт передачи файла считается
    # достаточным для продолжения, но диагностику имени сохраняем.
    _verify_selected_filename(page, resume_path)
    return True


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
        page.wait_for_timeout(3000)
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
            "[WARN] Полное имя файла не найдено в карточке после сохранения. "
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
