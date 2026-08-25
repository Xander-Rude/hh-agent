from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import Page, sync_playwright
from sqlalchemy import select

from app.application_assets import validate_application_assets
from app.db import Evaluation, SessionLocal, Vacancy
from yandex_browser import PROFILE_DIR, get_page, is_yandex_authenticated


HEADLESS = False


def pick_vacancy() -> tuple[Vacancy, Evaluation]:
    session = SessionLocal()
    try:
        requested_id = os.getenv("YANDEX_DRY_RUN_VACANCY_ID", "").strip()

        stmt = (
            select(Vacancy, Evaluation)
            .join(Evaluation, Evaluation.vacancy_id == Vacancy.id)
            .where(
                Vacancy.source == "yandex",
                Evaluation.decision != "reject",
                Evaluation.selected_resume_key.is_not(None),
            )
            .order_by(Evaluation.score.desc(), Evaluation.created_at.desc())
        )

        if requested_id:
            stmt = stmt.where(Vacancy.id == int(requested_id))

        row = session.execute(stmt).first()
        if row is None:
            raise RuntimeError(
                "Не найдено подходящей оценённой Yandex-вакансии с выбранным резюме"
            )

        vacancy, evaluation = row
        session.expunge(vacancy)
        session.expunge(evaluation)
        return vacancy, evaluation
    finally:
        session.close()


def visible_button(page: Page, names: list[str]):
    for name in names:
        for role in ("button", "link"):
            locator = page.get_by_role(role, name=name, exact=False)
            try:
                for index in range(locator.count()):
                    item = locator.nth(index)
                    if item.is_visible():
                        return item
            except Exception:
                continue
    return None


def click_apply(page: Page) -> bool:
    button = visible_button(
        page,
        ["Откликнуться", "Войти и откликнуться", "Отправить резюме"],
    )
    if button is None:
        return False

    button.click()
    page.wait_for_timeout(1800)
    return True


def print_form_inventory(page: Page) -> None:
    print("\n[FORM] Видимые поля:")

    for selector in ("input", "textarea", "select"):
        locator = page.locator(selector)
        try:
            count = locator.count()
        except Exception:
            continue

        for index in range(count):
            item = locator.nth(index)
            try:
                if not item.is_visible() and item.get_attribute("type") != "file":
                    continue
                print(
                    f"  {selector}[{index}] "
                    f"type={item.get_attribute('type')} "
                    f"name={item.get_attribute('name')} "
                    f"placeholder={item.get_attribute('placeholder')} "
                    f"accept={item.get_attribute('accept')} "
                    f"multiple={item.get_attribute('multiple')}"
                )
            except Exception:
                continue


def fill_cover_letter(page: Page, text: str) -> bool:
    text = (text or "").strip()
    if not text:
        print("[WARN] Сопроводительное письмо пустое")
        return False

    selectors = [
        'textarea[name*="cover"]',
        'textarea[name*="letter"]',
        'textarea',
    ]

    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = locator.count()
        except Exception:
            continue

        for index in range(count):
            field = locator.nth(index)
            try:
                if not field.is_visible():
                    continue
                field.fill(text)
                actual = field.input_value().strip()
                if actual == text:
                    print("[OK] Сопроводительное заполнено")
                    return True
            except Exception:
                continue

    print("[WARN] Не нашёл подходящее поле сопроводительного")
    return False


def upload_assets(
    page: Page,
    resume_path: Path,
    presentation_path: Path,
) -> tuple[bool, bool]:
    inputs = page.locator('input[type="file"]')
    try:
        count = inputs.count()
    except Exception:
        count = 0

    print(f"[FILES] file inputs: {count}")

    if count == 0:
        print("[WARN] В форме пока нет input[type=file]")
        return False, False

    if count >= 2:
        try:
            inputs.nth(0).set_input_files(str(resume_path))
            print(f"[OK] Резюме прикреплено: {resume_path.name}")
            resume_ok = True
        except Exception as exc:
            print(f"[WARN] Резюме не прикреплено: {type(exc).__name__}: {exc}")
            resume_ok = False

        try:
            inputs.nth(1).set_input_files(str(presentation_path))
            print(f"[OK] Презентация прикреплена: {presentation_path.name}")
            presentation_ok = True
        except Exception as exc:
            print(f"[WARN] Презентация не прикреплена: {type(exc).__name__}: {exc}")
            presentation_ok = False

        return resume_ok, presentation_ok

    field = inputs.first
    multiple = field.get_attribute("multiple") is not None

    if multiple:
        try:
            field.set_input_files([str(resume_path), str(presentation_path)])
            print(f"[OK] Оба файла прикреплены через один multiple-input")
            return True, True
        except Exception as exc:
            print(f"[WARN] Не удалось прикрепить оба файла: {type(exc).__name__}: {exc}")
            return False, False

    try:
        field.set_input_files(str(resume_path))
        print(f"[OK] Резюме прикреплено: {resume_path.name}")
        print("[WARN] В форме найден только один single-file input; презентацию не прикрепляю вслепую")
        return True, False
    except Exception as exc:
        print(f"[WARN] Резюме не прикреплено: {type(exc).__name__}: {exc}")
        return False, False


def main() -> int:
    vacancy, evaluation = pick_vacancy()

    resume_path, presentation_path = validate_application_assets(
        evaluation.selected_resume_key,
        evaluation.selected_resume_title,
    )

    print("=" * 80)
    print("YANDEX APPLY DRY-RUN — ФИНАЛЬНАЯ ОТПРАВКА ОТКЛЮЧЕНА")
    print("=" * 80)
    print(f"Vacancy ID: {vacancy.id}")
    print(f"Вакансия: {vacancy.title}")
    print(f"Score: {evaluation.score}")
    print(f"Resume key: {evaluation.selected_resume_key}")
    print(f"Resume title: {evaluation.selected_resume_title}")
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

            print(f"[OK] Авторизация подтверждена. URL: {page.url}")

            if not click_apply(page):
                print("[ERROR] Не нашёл кнопку отклика")
                print_form_inventory(page)
                input("\nНажмите Enter, чтобы закрыть браузер...")
                return 5

            print(f"[STEP] Форма отклика открыта. URL: {page.url}")
            print_form_inventory(page)

            cover_ok = fill_cover_letter(page, evaluation.cover_letter or "")
            resume_ok, presentation_ok = upload_assets(
                page,
                resume_path,
                presentation_path,
            )

            print("\n" + "=" * 80)
            print("DRY-RUN RESULT")
            print(f"cover_letter={cover_ok}")
            print(f"resume={resume_ok}")
            print(f"presentation={presentation_ok}")
            print("SUBMIT=DISABLED — никакая финальная кнопка не нажимается")
            print("=" * 80)

            input("\nОсмотрите заполненную форму в браузере и нажмите Enter для выхода...")
            return 0

        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
