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
                    page.wait_for_timeout(1800)
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
                file_radio.check()
                page.wait_for_timeout(500)
            checked = file_radio.is_checked()
            print(f"[RESUME] Режим «Файл»: {checked}")
            return checked
    except Exception:
        pass

    try:
        label = form.get_by_text("Файл", exact=True)
        if label.count() and label.first.is_visible():
            label.first.click()
            page.wait_for_timeout(500)
            print("[RESUME] Нажат режим «Файл»")
            return True
    except Exception:
        pass

    return False


def _resume_section(page: Page):
    form = _profile_form(page)
    if form is None:
        return None

    # Находим самый компактный контейнер, содержащий заголовок «Резюме» и
    # переключатели «Файл / Ссылка», но не всю форму целиком.
    heading = form.get_by_text("Резюме", exact=True)
    try:
        if not heading.count():
            return form
    except Exception:
        return form

    node = heading.first
    best = None

    for level in range(1, 8):
        try:
            candidate = node.locator("xpath=" + "/.." * level)
            text = " ".join((candidate.inner_text(timeout=500) or "").split())
        except Exception:
            continue

        if "Файл" in text and "Ссылка" in text:
            best = candidate
            if "Сохранить данные" not in text:
                return candidate

    return best or form


def _direct_file_input(page: Page, resume_path: Path) -> bool:
    section = _resume_section(page)
    if section is None:
        return False

    fields = section.locator('input[type="file"]')
    try:
        count = fields.count()
    except Exception:
        count = 0

    print(f"[RESUME] input[type=file] в блоке резюме: {count}")

    for index in range(count):
        try:
            fields.nth(index).set_input_files(str(resume_path))
            page.wait_for_timeout(1200)
            print(f"[OK] PDF передан в file input: {resume_path.name}")
            return True
        except Exception as exc:
            print(f"[WARN] file input[{index}] не принял файл: {type(exc).__name__}: {exc}")

    return False


def _upload_zone_candidates(page: Page):
    section = _resume_section(page)
    if section is None:
        return []

    raw = section.locator("button, label, [role='button'], [tabindex], div, span")
    candidates = []

    try:
        count = raw.count()
    except Exception:
        return []

    for index in range(min(count, 120)):
        item = raw.nth(index)
        try:
            if not item.is_visible():
                continue

            meta = item.evaluate(
                """el => ({
                    tag: el.tagName,
                    text: (el.innerText || el.textContent || '').trim(),
                    cursor: getComputedStyle(el).cursor,
                    role: el.getAttribute('role') || '',
                    aria: el.getAttribute('aria-label') || '',
                    cls: typeof el.className === 'string' ? el.className : ''
                })"""
            )
        except Exception:
            continue

        text = " ".join(str(meta.get("text") or "").split())
        lowered = text.lower()
        aria = str(meta.get("aria") or "").lower()
        cls = str(meta.get("cls") or "").lower()
        tag = str(meta.get("tag") or "")
        cursor = str(meta.get("cursor") or "")
        role = str(meta.get("role") or "")

        # Не кликаем управляющие кнопки формы и переключатель ссылки.
        if any(skip in text for skip in ("Сохранить данные", "Отменить", "Скачать")):
            continue
        if text == "Ссылка":
            continue

        score = 0
        reason = []

        if any(word in lowered for word in ("загруз", "выбрать", "прикреп", "добавить")):
            score += 100
            reason.append("upload-text")
        if any(word in aria for word in ("загруз", "выбрать", "прикреп", "resume", "file")):
            score += 90
            reason.append("aria")
        if any(word in cls for word in ("upload", "uploader", "file", "attach", "resume")):
            score += 60
            reason.append("class")
        if cursor == "pointer":
            score += 30
            reason.append("cursor:pointer")
        if tag in ("BUTTON", "LABEL") or role == "button":
            score += 20
            reason.append("clickable")
        if not text:
            score += 5
            reason.append("icon/empty")

        if score > 0:
            candidates.append(
                (
                    score,
                    f"{tag} text={text[:80]!r} aria={aria[:60]!r} "
                    f"reason={','.join(reason)}",
                    item,
                )
            )

    candidates.sort(key=lambda row: row[0], reverse=True)
    return candidates


def _try_upload_zone(page: Page, resume_path: Path) -> bool:
    candidates = _upload_zone_candidates(page)
    print(f"[RESUME] Кандидатов в зоне загрузки: {len(candidates)}")

    for score, label, control in candidates[:30]:
        try:
            control.scroll_into_view_if_needed()
            print(f"[TRY] score={score} {label}")

            with page.expect_file_chooser(timeout=1800) as chooser_info:
                control.click(timeout=1500)

            chooser = chooser_info.value
            chooser.set_files(str(resume_path))
            page.wait_for_timeout(1400)
            print(f"[OK] Зона загрузки приняла PDF: {resume_path.name}")
            return True

        except PlaywrightTimeoutError:
            continue
        except Exception as exc:
            print(f"  [SKIP] {type(exc).__name__}: {exc}")

    return False


def _print_resume_section_html(page: Page) -> None:
    section = _resume_section(page)
    print("\n[RESUME] HTML блока «Резюме»:")
    if section is None:
        print("  <блок не найден>")
        return

    try:
        html = section.evaluate("el => el.outerHTML")
        compact = " ".join(str(html).split())
        print(f"  {compact[:7000]}")
    except Exception as exc:
        print(f"  inspect failed: {type(exc).__name__}: {exc}")


def _verify_selected_filename(page: Page, resume_path: Path) -> bool:
    section = _resume_section(page)
    if section is None:
        return False

    try:
        text = section.inner_text(timeout=2000)
    except Exception:
        text = ""

    visible = resume_path.name.lower() in text.lower()
    print(f"[VERIFY] Новое имя файла видно до сохранения: {visible}")
    if not visible:
        print(f"[VERIFY] Текст блока: {' '.join(text.split())[-1000:]}")
    return visible


def choose_resume_via_filechooser(page: Page, resume_path: Path) -> bool:
    _ensure_file_mode(page)
    current_name = _current_resume_filename(page)
    print(f"[RESUME] Текущий файл: {current_name or '<не определён>'}")
    print(f"[RESUME] Всегда загружаю локальную версию заново: {resume_path.name}")

    # Сначала пробуем скрытый input, если он уже существует.
    uploaded = _direct_file_input(page, resume_path)

    # Основной сценарий Яндекса: клик по визуальной зоне загрузки в блоке «Резюме».
    if not uploaded:
        uploaded = _try_upload_zone(page, resume_path)

    if not uploaded:
        _print_resume_section_html(page)
        print("[ERROR] Не удалось открыть file chooser через зону загрузки резюме")
        return False

    _verify_selected_filename(page, resume_path)
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
