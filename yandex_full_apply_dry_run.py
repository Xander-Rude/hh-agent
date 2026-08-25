from __future__ import annotations

from playwright.sync_api import sync_playwright

from app.application_assets import validate_application_assets
from yandex_apply_dry_run import (
    click_apply,
    ensure_resume_selection,
    fill_cover_letter,
    find_application_frame,
    pick_vacancy,
    set_required_consent,
)
from yandex_browser import PROFILE_DIR, get_page, is_yandex_authenticated
from yandex_profile_edit_dry_run import click_edit, save_profile
from yandex_profile_replace_v2 import (
    remove_current_resume,
    upload_new_resume,
    wait_save_ready,
)


HEADLESS = False


def submit_control_state(page) -> tuple[bool, bool]:
    frame = find_application_frame(page)
    if frame is None:
        print("[SUBMIT] iframe forms.yandex.ru не найден")
        return False, False

    candidates = [
        frame.get_by_role("button", name="Отправить отклик", exact=False),
        frame.locator('button[type="submit"]'),
    ]

    for locator in candidates:
        try:
            count = locator.count()
        except Exception:
            continue

        for index in range(count):
            item = locator.nth(index)
            try:
                if not item.is_visible():
                    continue
                disabled = item.is_disabled()
                print(
                    f"[SUBMIT] Кнопка отправки видима: True; enabled={not disabled}"
                )
                return True, not disabled
            except Exception:
                continue

    print("[SUBMIT] Видимая кнопка отправки не найдена")
    return False, False


def main() -> int:
    vacancy, evaluation = pick_vacancy()
    resume_key, resume_title = ensure_resume_selection(vacancy, evaluation)
    resume_path, presentation_path = validate_application_assets(
        resume_key,
        resume_title,
    )

    print("=" * 80)
    print("YANDEX FULL APPLY DRY-RUN — ФИНАЛЬНАЯ ОТПРАВКА ОТКЛЮЧЕНА")
    print("=" * 80)
    print(f"Vacancy ID: {vacancy.id}")
    print(f"Вакансия: {vacancy.title}")
    print(f"Decision: {evaluation.decision}")
    print(f"Score: {evaluation.score}")
    print(f"Resume key: {resume_key}")
    print(f"Resume: {resume_path}")
    print(f"Presentation: {presentation_path}")
    print(f"URL: {vacancy.url}")

    if evaluation.decision == "reject":
        print(
            "[WARN] REJECT-вакансия используется только для проверки формы. "
            "Боевой auto-apply её отправлять не будет."
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
            page.wait_for_timeout(1400)

            if not is_yandex_authenticated(page, context):
                print("[ERROR] Сессия Яндекса не авторизована")
                return 4

            if not click_apply(page):
                print("[ERROR] Не найден «Откликнуться»")
                return 5
            print("[OK] Блок отклика открыт")

            if not click_edit(page):
                print("[ERROR] Не найден «Редактировать»")
                return 6
            print("[OK] Редактор профиля открыт")

            if not remove_current_resume(page):
                print("[ERROR] Не удалось убрать текущее резюме")
                return 7

            if not upload_new_resume(page, resume_path):
                print("[ERROR] Новое резюме не загружено")
                return 8

            if not wait_save_ready(page):
                print("[ERROR] Профиль не готов к сохранению после загрузки")
                return 9

            if not save_profile(page, resume_path):
                print("[ERROR] Новое резюме не сохранено в профиле")
                return 10
            print("[OK] Новое резюме сохранено в профиле")

            page.wait_for_timeout(1200)

            cover_ok = fill_cover_letter(page, evaluation.cover_letter or "")
            consent_ok = set_required_consent(page)
            submit_visible, submit_enabled = submit_control_state(page)

            print("\n" + "=" * 80)
            print("FULL APPLY DRY-RUN RESULT")
            print("resume_replaced=True")
            print("profile_saved=True")
            print(f"cover_letter_filled={cover_ok}")
            print(f"required_consent={consent_ok}")
            print(f"submit_visible={submit_visible}")
            print(f"submit_enabled={submit_enabled}")
            print("presentation_attached=False")
            print("application_submitted=False")
            print(
                "[SAFE] Кнопка «Отправить отклик» только проверяется и НЕ нажимается."
            )
            print("=" * 80)

            input("\nПроверьте готовую форму в браузере и нажмите Enter для выхода...")

            return 0 if cover_ok and consent_ok else 11
        finally:
            try:
                context.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
