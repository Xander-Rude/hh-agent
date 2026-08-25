from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import Frame, Page, sync_playwright
from sqlalchemy import select

from app.application_assets import validate_application_assets
from app.db import Evaluation, SessionLocal, Vacancy
from app.resume_matcher import match_resume
from yandex_browser import PROFILE_DIR, get_page, is_yandex_authenticated


HEADLESS = False


def pick_vacancy() -> tuple[Vacancy, Evaluation]:
    session = SessionLocal()
    try:
        requested_id = os.getenv("YANDEX_DRY_RUN_VACANCY_ID", "").strip()

        def build_stmt(include_rejects: bool):
            stmt = (
                select(Vacancy, Evaluation)
                .join(Evaluation, Evaluation.vacancy_id == Vacancy.id)
                .where(Vacancy.source == "yandex")
                .order_by(Evaluation.score.desc(), Evaluation.created_at.desc())
            )

            if not include_rejects:
                stmt = stmt.where(Evaluation.decision != "reject")

            if requested_id:
                stmt = stmt.where(Vacancy.id == int(requested_id))

            return stmt

        row = session.execute(build_stmt(include_rejects=False)).first()

        if row is None:
            row = session.execute(build_stmt(include_rejects=True)).first()
            if row is not None:
                print(
                    "[WARN] Для dry-run не найдено ни одной Yandex-вакансии "
                    "с decision != reject. Беру лучшую оценённую вакансию только "
                    "для теста формы. Боевой auto-apply такие вакансии отправлять не будет."
                )

        if row is None:
            raise RuntimeError("Не найдено ни одной оценённой Yandex-вакансии")

        vacancy, evaluation = row
        session.expunge(vacancy)
        session.expunge(evaluation)
        return vacancy, evaluation
    finally:
        session.close()


def ensure_resume_selection(
    vacancy: Vacancy,
    evaluation: Evaluation,
) -> tuple[str, str]:
    key = (evaluation.selected_resume_key or "").strip()
    title = (evaluation.selected_resume_title or "").strip()

    if key:
        return key, title

    print(
        "[INFO] В старой Evaluation нет selected_resume_key. "
        "Выбираю лучшее из четырёх текущих резюме на лету..."
    )

    decision = match_resume(
        vacancy_title=vacancy.title or "",
        vacancy_description=vacancy.description or "",
        vacancy_score=int(evaluation.score or 0),
    )

    key = (decision.selected_resume_key or "").strip()
    title = (decision.selected_resume_title or "").strip()

    if not key:
        raise RuntimeError(
            "Resume matcher не вернул selected_resume_key для Yandex-вакансии"
        )

    evaluation.selected_resume_key = key
    evaluation.selected_resume_title = title
    evaluation.selected_resume_id = decision.selected_resume_id or None
    evaluation.selected_resume_score = int(decision.match_score or 0)

    print(
        f"[OK] Резюме выбрано на лету: {title or key} "
        f"(match={evaluation.selected_resume_score}%)"
    )

    return key, title


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
    page.wait_for_timeout(2500)
    return True


def _short(text: str | None, limit: int = 180) -> str:
    value = " ".join((text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _iter_scopes(page: Page) -> list[tuple[str, Page | Frame]]:
    scopes: list[tuple[str, Page | Frame]] = [("PAGE", page)]
    for index, frame in enumerate(page.frames):
        if frame == page.main_frame:
            continue
        scopes.append((f"FRAME[{index}]", frame))
    return scopes


def print_form_inventory(page: Page) -> None:
    print("\n[DIAG] URL после клика:")
    print(f"  PAGE: {page.url}")

    print("\n[DIAG] Frames:")
    for index, frame in enumerate(page.frames):
        print(f"  FRAME[{index}]: {frame.url}")

    for scope_name, scope in _iter_scopes(page):
        print(f"\n[DIAG] {scope_name} controls:")

        selectors = (
            "input",
            "textarea",
            "select",
            '[contenteditable="true"]',
            "button",
            'a[role="button"]',
            "form",
        )

        found_any = False

        for selector in selectors:
            locator = scope.locator(selector)
            try:
                count = locator.count()
            except Exception:
                continue

            if count == 0:
                continue

            print(f"  {selector}: {count}")

            for index in range(min(count, 25)):
                item = locator.nth(index)
                try:
                    visible = item.is_visible()
                    print(
                        f"    [{index}] visible={visible} "
                        f"type={item.get_attribute('type')} "
                        f"name={item.get_attribute('name')} "
                        f"role={item.get_attribute('role')} "
                        f"placeholder={item.get_attribute('placeholder')} "
                        f"accept={item.get_attribute('accept')} "
                        f"multiple={item.get_attribute('multiple')} "
                        f"text={_short(item.inner_text(timeout=500) if visible else '')}"
                    )
                    found_any = True
                except Exception as exc:
                    print(f"    [{index}] <inspect failed: {type(exc).__name__}>")

        if not found_any:
            print("  <controls not found>")

        try:
            body_text = scope.locator("body").inner_text(timeout=2000)
        except Exception:
            body_text = ""

        if body_text:
            print(f"  BODY TEXT: {_short(body_text, 1200)}")


def fill_cover_letter(page: Page, text: str) -> bool:
    text = (text or "").strip()
    if not text:
        print("[WARN] Сопроводительное письмо пустое")
        return False

    selectors = [
        'textarea[name*="cover"]',
        'textarea[name*="letter"]',
        "textarea",
        '[contenteditable="true"]',
    ]

    for scope_name, scope in _iter_scopes(page):
        for selector in selectors:
            locator = scope.locator(selector)
            try:
                count = locator.count()
            except Exception:
                continue

            for index in range(count):
                field = locator.nth(index)
                try:
                    if not field.is_visible():
                        continue

                    if selector == '[contenteditable="true"]':
                        field.fill(text)
                        actual = (field.inner_text() or "").strip()
                    else:
                        field.fill(text)
                        actual = field.input_value().strip()

                    if actual == text:
                        print(f"[OK] Сопроводительное заполнено в {scope_name}")
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
    for scope_name, scope in _iter_scopes(page):
        inputs = scope.locator('input[type="file"]')
        try:
            count = inputs.count()
        except Exception:
            count = 0

        if count == 0:
            continue

        print(f"[FILES] {scope_name} file inputs: {count}")

        if count >= 2:
            try:
                inputs.nth(0).set_input_files(str(resume_path))
                print(f"[OK] Резюме прикреплено: {resume_path.name}")
                resume_ok = True
            except Exception as exc:
                print(
                    f"[WARN] Резюме не прикреплено: "
                    f"{type(exc).__name__}: {exc}"
                )
                resume_ok = False

            try:
                inputs.nth(1).set_input_files(str(presentation_path))
                print(f"[OK] Презентация прикреплена: {presentation_path.name}")
                presentation_ok = True
            except Exception as exc:
                print(
                    f"[WARN] Презентация не прикреплена: "
                    f"{type(exc).__name__}: {exc}"
                )
                presentation_ok = False

            return resume_ok, presentation_ok

        field = inputs.first
        multiple = field.get_attribute("multiple") is not None

        if multiple:
            try:
                field.set_input_files([str(resume_path), str(presentation_path)])
                print("[OK] Оба файла прикреплены через один multiple-input")
                return True, True
            except Exception as exc:
                print(
                    f"[WARN] Не удалось прикрепить оба файла: "
                    f"{type(exc).__name__}: {exc}"
                )
                return False, False

        try:
            field.set_input_files(str(resume_path))
            print(f"[OK] Резюме прикреплено: {resume_path.name}")
            print(
                "[WARN] Найден только один single-file input; "
                "презентацию не прикрепляю вслепую"
            )
            return True, False
        except Exception as exc:
            print(
                f"[WARN] Резюме не прикреплено: "
                f"{type(exc).__name__}: {exc}"
            )
            return False, False

    print("[FILES] file inputs: 0 во всех frames")
    print("[WARN] В форме пока нет input[type=file]")
    return False, False


def main() -> int:
    vacancy, evaluation = pick_vacancy()
    resume_key, resume_title = ensure_resume_selection(vacancy, evaluation)

    resume_path, presentation_path = validate_application_assets(
        resume_key,
        resume_title,
    )

    print("=" * 80)
    print("YANDEX APPLY DRY-RUN — ФИНАЛЬНАЯ ОТПРАВКА ОТКЛЮЧЕНА")
    print("=" * 80)
    print(f"Vacancy ID: {vacancy.id}")
    print(f"Вакансия: {vacancy.title}")
    print(f"Score: {evaluation.score}")
    print(f"Decision: {evaluation.decision}")
    print(f"Resume key: {resume_key}")
    print(f"Resume title: {resume_title}")
    print(f"Resume file: {resume_path}")
    print(f"Presentation: {presentation_path}")
    print(f"URL: {vacancy.url}")

    if evaluation.decision == "reject":
        print(
            "[WARN] Эта вакансия REJECT и используется ИСКЛЮЧИТЕЛЬНО для dry-run формы."
        )

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
