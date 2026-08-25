from __future__ import annotations

import os
import time
from contextlib import redirect_stdout
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright
from sqlalchemy import select

from app.application_assets import validate_resume_asset
from app.db import Application, Evaluation, SessionLocal, Vacancy
from yandex_apply_dry_run import (
    click_apply,
    fill_cover_letter,
    find_application_frame,
    set_required_consent,
)
from yandex_browser import PROFILE_DIR, get_page, is_yandex_authenticated
from yandex_full_apply_dry_run import submit_control_state
from yandex_profile_edit_dry_run import click_edit, save_profile
from yandex_profile_replace_v2 import (
    remove_current_resume,
    upload_new_resume,
    wait_save_ready,
)


load_dotenv()

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
SUCCESS_LOG = LOG_DIR / "yandex_apply_worker.log"
ATTENTION_LOG = LOG_DIR / "yandex_apply_worker_attention.log"

HEADLESS = os.getenv("YANDEX_APPLY_HEADLESS", "false").lower() == "true"
MAX_PER_RUN = int(os.getenv("YANDEX_APPLY_MAX_PER_RUN", "5"))
DELAY_SECONDS = float(os.getenv("YANDEX_APPLY_DELAY_SECONDS", "3"))

MANUAL_MARKERS = (
    "captcha",
    "капча",
    "я не робот",
    "подтвердите, что вы не робот",
    "тестовое задание",
    "пройти тест",
    "выполнить тест",
    "ответьте на вопросы",
    "вопросы работодателя",
    "анкета",
)

SUCCESS_MARKERS = (
    "отклик отправлен",
    "спасибо за отклик",
    "спасибо за ваш отклик",
    "мы получили ваш отклик",
    "ваш отклик отправлен",
)


def set_status(application_id: int, status: str, *, applied: bool = False) -> None:
    session = SessionLocal()
    try:
        application = session.get(Application, application_id)
        if application is None:
            return
        application.status = status
        if applied:
            application.applied_at = datetime.now(UTC).replace(tzinfo=None)
        session.commit()
    finally:
        session.close()


def append_log(result: str, output: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = SUCCESS_LOG if result == "applied" else ATTENTION_LOG
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as file:
        file.write(f"\n[{stamp}] RESULT={result}\n")
        file.write(output.strip() or "No output captured.")
        file.write("\n")


def latest_evaluation(vacancy_id: int) -> Evaluation | None:
    session = SessionLocal()
    try:
        evaluation = session.scalars(
            select(Evaluation)
            .where(Evaluation.vacancy_id == vacancy_id)
            .order_by(Evaluation.created_at.desc(), Evaluation.id.desc())
            .limit(1)
        ).first()
        if evaluation is not None:
            session.expunge(evaluation)
        return evaluation
    finally:
        session.close()


def load_queue() -> list[tuple[Application, Vacancy]]:
    session = SessionLocal()
    try:
        rows = session.execute(
            select(Application, Vacancy)
            .join(Vacancy, Vacancy.id == Application.vacancy_id)
            .where(
                Application.status == "approved",
                Vacancy.source == "yandex",
            )
            .order_by(Application.created_at.asc())
            .limit(MAX_PER_RUN)
        ).all()

        result: list[tuple[Application, Vacancy]] = []
        for application, vacancy in rows:
            session.expunge(application)
            session.expunge(vacancy)
            result.append((application, vacancy))
        return result
    finally:
        session.close()


def body_text(page: Page) -> str:
    chunks: list[str] = []
    try:
        chunks.append(page.locator("body").inner_text(timeout=2500))
    except Exception:
        pass

    frame = find_application_frame(page)
    if frame is not None:
        try:
            chunks.append(frame.locator("body").inner_text(timeout=2500))
        except Exception:
            pass

    return "\n".join(chunks).lower()


def detect_manual_marker(page: Page) -> str | None:
    text = body_text(page)
    for marker in MANUAL_MARKERS:
        if marker in text:
            return marker
    return None


def find_submit_button(page: Page):
    frame = find_application_frame(page)
    if frame is None:
        return None

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
                if item.is_visible() and not item.is_disabled():
                    return item
            except Exception:
                continue
    return None


def confirm_success(page: Page) -> bool:
    text = body_text(page)
    if any(marker in text for marker in SUCCESS_MARKERS):
        return True

    frame = find_application_frame(page)
    if frame is None:
        # После успешного submit iframe может исчезнуть целиком.
        return True

    submit_visible, _ = submit_control_state(page)
    return not submit_visible


def process_application(
    page: Page,
    application: Application,
    vacancy: Vacancy,
) -> str:
    print("\n" + "=" * 80)
    print(f"{vacancy.title} | {vacancy.company or '-'}")
    print(vacancy.url)
    print(f"Application ID: {application.id}")

    evaluation = latest_evaluation(vacancy.id)
    if evaluation is None:
        print("[MANUAL] У вакансии нет Evaluation. Отклик НЕ отправляю.")
        set_status(application.id, "manual_required")
        return "manual_required"

    if (evaluation.decision or "").strip().lower() == "reject":
        print("[MANUAL] Последняя Evaluation=reject. Боевой отклик запрещён.")
        set_status(application.id, "manual_required")
        return "manual_required"

    cover_letter = (application.cover_letter or evaluation.cover_letter or "").strip()
    if not cover_letter:
        print("[MANUAL] Нет сопроводительного письма. Отклик НЕ отправляю.")
        set_status(application.id, "manual_required")
        return "manual_required"

    resume_key = (
        application.selected_resume_key
        or evaluation.selected_resume_key
        or ""
    ).strip()
    resume_title = (
        application.selected_resume_title
        or evaluation.selected_resume_title
        or ""
    ).strip()

    if not resume_key:
        print("[MANUAL] Не выбрано резюме. Отклик НЕ отправляю.")
        set_status(application.id, "manual_required")
        return "manual_required"

    try:
        resume_path = validate_resume_asset(
            resume_key,
            resume_title,
        )
    except Exception as exc:
        print(f"[MANUAL] Не удалось подготовить резюме: {type(exc).__name__}: {exc}")
        set_status(application.id, "manual_required")
        return "manual_required"

    print(f"[RESUME] {resume_path}")

    set_status(application.id, "applying")

    try:
        page.goto(vacancy.url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1400)
    except Exception as exc:
        print(f"[ERROR] Не удалось открыть вакансию: {type(exc).__name__}: {exc}")
        set_status(application.id, "apply_error")
        return "apply_error"

    marker = detect_manual_marker(page)
    if marker:
        print(f"[MANUAL] До открытия формы обнаружено: {marker}")
        set_status(application.id, "manual_required")
        return "manual_required"

    if not click_apply(page):
        print("[MANUAL] Не найден стандартный «Откликнуться».")
        set_status(application.id, "manual_required")
        return "manual_required"
    print("[OK] Блок отклика открыт")

    marker = detect_manual_marker(page)
    if marker:
        print(f"[MANUAL] После открытия формы обнаружено: {marker}")
        set_status(application.id, "manual_required")
        return "manual_required"

    if not click_edit(page):
        print("[MANUAL] Не найден стандартный редактор профиля.")
        set_status(application.id, "manual_required")
        return "manual_required"

    if not remove_current_resume(page):
        print("[MANUAL] Не удалось убрать текущее резюме.")
        set_status(application.id, "manual_required")
        return "manual_required"

    if not upload_new_resume(page, resume_path):
        print("[MANUAL] Не удалось загрузить выбранный PDF.")
        set_status(application.id, "manual_required")
        return "manual_required"

    if not wait_save_ready(page):
        print("[MANUAL] Профиль не готов к сохранению после загрузки PDF.")
        set_status(application.id, "manual_required")
        return "manual_required"

    if not save_profile(page, resume_path):
        print("[ERROR] Не удалось сохранить профиль с новым резюме.")
        set_status(application.id, "apply_error")
        return "apply_error"
    print("[OK] Новое резюме сохранено")

    page.wait_for_timeout(900)

    if not fill_cover_letter(page, cover_letter):
        print("[MANUAL] Не удалось заполнить сопроводительное письмо.")
        set_status(application.id, "manual_required")
        return "manual_required"

    if not set_required_consent(page):
        print("[MANUAL] Не удалось установить обязательное согласие.")
        set_status(application.id, "manual_required")
        return "manual_required"

    marker = detect_manual_marker(page)
    if marker:
        print(f"[MANUAL] Перед отправкой обнаружено: {marker}")
        set_status(application.id, "manual_required")
        return "manual_required"

    submit_visible, submit_enabled = submit_control_state(page)
    if not submit_visible or not submit_enabled:
        print("[MANUAL] Финальная кнопка отправки не готова.")
        set_status(application.id, "manual_required")
        return "manual_required"

    submit = find_submit_button(page)
    if submit is None:
        print("[MANUAL] Не удалось получить точный submit control.")
        set_status(application.id, "manual_required")
        return "manual_required"

    print("[STEP] Отправляю отклик Яндекса...")
    try:
        submit.click(timeout=4000)
        page.wait_for_timeout(2500)
    except Exception as exc:
        print(f"[ERROR] Ошибка финального submit: {type(exc).__name__}: {exc}")
        set_status(application.id, "apply_error")
        return "apply_error"

    if confirm_success(page):
        print("[SUCCESS] Отклик Яндекса подтверждён.")
        set_status(application.id, "applied", applied=True)
        return "applied"

    print(
        "[MANUAL] Submit был нажат, но подтверждение успеха не найдено. "
        "Повторно автоматически НЕ отправлять."
    )
    set_status(application.id, "manual_required")
    return "manual_required"


def main() -> None:
    queue = load_queue()

    print("\n" + "=" * 80)
    print("YANDEX APPLY WORKER")
    print("=" * 80)
    print(f"В очереди approved/yandex: {len(queue)}")
    print(f"Максимум за проход: {MAX_PER_RUN}")
    print(f"Headless: {HEADLESS}")

    if not queue:
        print("Отправлять нечего.")
        return

    stats = {"applied": 0, "manual_required": 0, "apply_error": 0}

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=HEADLESS,
            viewport={"width": 1440, "height": 1000},
        )
        try:
            page = get_page(context)
            if not is_yandex_authenticated(page, context):
                print("[ERROR] Сессия Яндекса не авторизована. Worker остановлен.")
                return

            for index, (application, vacancy) in enumerate(queue, start=1):
                output = StringIO()
                try:
                    with redirect_stdout(output):
                        print(f"[{index}/{len(queue)}]")
                        result = process_application(page, application, vacancy)
                except Exception as exc:
                    result = "apply_error"
                    set_status(application.id, "apply_error")
                    with redirect_stdout(output):
                        print(f"[ERROR] Необработанная ошибка: {type(exc).__name__}: {exc}")

                append_log(result, output.getvalue())
                print(output.getvalue(), end="")
                stats[result] = stats.get(result, 0) + 1

                if index < len(queue):
                    time.sleep(DELAY_SECONDS)
        finally:
            try:
                context.close()
            except Exception:
                pass

    print("\n" + "=" * 80)
    print("YANDEX APPLY WORKER DONE")
    for key, value in stats.items():
        print(f"{key}: {value}")
    print("=" * 80)


if __name__ == "__main__":
    main()
