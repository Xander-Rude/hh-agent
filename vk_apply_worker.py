from __future__ import annotations

import os
import re
import time
from contextlib import redirect_stdout
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Locator, Page, sync_playwright
from sqlalchemy import select

from app.application_assets import validate_resume_asset
from app.db import Application, Evaluation, SessionLocal, Vacancy


load_dotenv()

ROOT = Path(__file__).resolve().parent
PROFILE_DIR = ROOT / "vk-browser-profile"
LOG_DIR = ROOT / "logs"
SUCCESS_LOG = LOG_DIR / "vk_apply_worker.log"
ATTENTION_LOG = LOG_DIR / "vk_apply_worker_attention.log"

HEADLESS = os.getenv("VK_APPLY_HEADLESS", "false").lower() == "true"
LIVE = os.getenv("VK_APPLY_LIVE", "false").lower() == "true"
MAX_PER_RUN = int(os.getenv("VK_APPLY_MAX_PER_RUN", "5"))
DELAY_SECONDS = float(os.getenv("VK_APPLY_DELAY_SECONDS", "3"))
TARGET_APPLICATION_ID = os.getenv("VK_APPLY_APPLICATION_ID", "").strip()
CAPTCHA_WAIT_SECONDS = int(os.getenv("VK_APPLY_CAPTCHA_WAIT_SECONDS", "300"))

APPLICANT_NAME = os.getenv("VK_APPLY_NAME", "").strip()
APPLICANT_FIRST_NAME = os.getenv("VK_APPLY_FIRST_NAME", "").strip()
APPLICANT_LAST_NAME = os.getenv("VK_APPLY_LAST_NAME", "").strip()
APPLICANT_EMAIL = os.getenv("VK_APPLY_EMAIL", "").strip()
APPLICANT_PHONE = os.getenv("VK_APPLY_PHONE", "").strip()

MANUAL_MARKERS = (
    "тестовое задание",
    "пройти тест",
    "выполнить тест",
    "ответьте на вопросы",
    "дополнительные вопросы",
)

CAPTCHA_TEXT_RE = re.compile(r"captcha|капча|я\s+не\s+робот", re.IGNORECASE)
CAPTCHA_FRAME_RE = re.compile(r"captcha|recaptcha|smartcaptcha", re.IGNORECASE)

SUCCESS_MARKERS = (
    "отклик отправлен",
    "спасибо за отклик",
    "спасибо за ваш отклик",
    "резюме отправлено",
    "заявка отправлена",
)

APPLY_RE = re.compile(r"отклик|отправить\s+резюме|оставить\s+резюме", re.IGNORECASE)
SUBMIT_RE = re.compile(r"отправить|откликнуться|отправить\s+резюме", re.IGNORECASE)


def set_status(application_id: int, status: str, *, applied: bool = False) -> None:
    if not LIVE:
        return
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
        file.write(f"\n[{stamp}] RESULT={result} LIVE={LIVE}\n")
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
        query = (
            select(Application, Vacancy)
            .join(Vacancy, Vacancy.id == Application.vacancy_id)
            .where(
                Application.status == "approved",
                Vacancy.source == "vk",
            )
            .order_by(Application.created_at.asc())
        )

        if TARGET_APPLICATION_ID:
            try:
                target_id = int(TARGET_APPLICATION_ID)
            except ValueError:
                print("[ERROR] VK_APPLY_APPLICATION_ID должен быть целым числом.")
                return []
            query = query.where(Application.id == target_id).limit(1)
        else:
            query = query.limit(MAX_PER_RUN)

        rows = session.execute(query).all()
        result: list[tuple[Application, Vacancy]] = []
        for application, vacancy in rows:
            session.expunge(application)
            session.expunge(vacancy)
            result.append((application, vacancy))
        return result
    finally:
        session.close()


def body_text(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=3000).lower()
    except Exception:
        return ""


def detect_manual_marker(page: Page) -> str | None:
    text = body_text(page)
    for marker in MANUAL_MARKERS:
        if marker in text:
            return marker
    return None


def _first_visible(locator: Locator) -> Locator | None:
    try:
        count = locator.count()
    except Exception:
        return None
    for index in range(count):
        item = locator.nth(index)
        try:
            if item.is_visible():
                return item
        except Exception:
            continue
    return None


def captcha_is_visible(page: Page) -> bool:
    """Проверяет только реально видимую captcha, а не служебный текст в DOM."""
    selectors = (
        'iframe[src*="captcha" i]',
        'iframe[title*="captcha" i]',
        '[id*="captcha" i]',
        '[class*="captcha" i]',
    )
    for selector in selectors:
        try:
            if _first_visible(page.locator(selector)) is not None:
                return True
        except Exception:
            continue

    try:
        if _first_visible(page.get_by_text(CAPTCHA_TEXT_RE)) is not None:
            return True
    except Exception:
        pass

    for frame in page.frames:
        try:
            if CAPTCHA_FRAME_RE.search(frame.url or ""):
                return True
        except Exception:
            continue

    return False


def wait_for_captcha_resolution(page: Page) -> bool:
    if not captcha_is_visible(page):
        return True

    if HEADLESS:
        print("[MANUAL] CAPTCHA появилась в headless-режиме; пройти её вручную невозможно.")
        return False

    print("[MANUAL] CAPTCHA появилась после submit.")
    print(
        f"[WAIT] Пройди CAPTCHA в открытом окне браузера. "
        f"Жду до {CAPTCHA_WAIT_SECONDS} сек."
    )

    deadline = time.monotonic() + CAPTCHA_WAIT_SECONDS
    while time.monotonic() < deadline:
        if not captcha_is_visible(page):
            print("[OK] CAPTCHA пройдена/исчезла. Проверяю результат отклика...")
            page.wait_for_timeout(1500)
            return True
        page.wait_for_timeout(1000)

    print("[MANUAL] Время ожидания CAPTCHA истекло.")
    return False


def click_apply(page: Page) -> bool:
    candidates = [
        page.get_by_role("button", name=APPLY_RE),
        page.get_by_role("link", name=APPLY_RE),
        page.locator("button").filter(has_text=APPLY_RE),
        page.locator("a").filter(has_text=APPLY_RE),
    ]
    for locator in candidates:
        item = _first_visible(locator)
        if item is None:
            continue
        try:
            item.click(timeout=5000)
            page.wait_for_timeout(900)
            return True
        except Exception:
            continue
    return False


def _fill_by_candidates(page: Page, selectors: list[tuple[str, re.Pattern]], value: str) -> bool:
    if not value:
        return False

    for kind, pattern in selectors:
        try:
            if kind == "label":
                locator = page.get_by_label(pattern)
            else:
                locator = page.get_by_placeholder(pattern)
        except Exception:
            continue

        item = _first_visible(locator)
        if item is None:
            continue
        try:
            item.fill(value, timeout=3000)
            return True
        except Exception:
            continue
    return False


def _fill_named(page: Page, name: str, value: str) -> bool:
    if not value:
        return False
    item = _first_visible(page.locator(f'[name="{name}"]'))
    if item is None:
        return False
    try:
        item.fill(value, timeout=3000)
        return True
    except Exception:
        return False


def applicant_name_parts() -> tuple[str, str]:
    if APPLICANT_FIRST_NAME or APPLICANT_LAST_NAME:
        return APPLICANT_FIRST_NAME, APPLICANT_LAST_NAME

    parts = APPLICANT_NAME.split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    if parts:
        return parts[0], ""
    return "", ""


def _check_vk_agreement(page: Page) -> bool:
    agree = _first_visible(page.locator('input[type="checkbox"][name="agree"]'))
    if agree is None:
        return False
    try:
        if not agree.is_checked():
            agree.check(timeout=3000)
        return agree.is_checked()
    except Exception:
        return False


def fill_known_fields(page: Page, cover_letter: str, resume_path: Path) -> dict[str, bool]:
    first_name, last_name = applicant_name_parts()

    first_name_ok = _fill_named(page, "first_name", first_name)
    last_name_ok = _fill_named(page, "last_name", last_name)
    email_ok = _fill_named(page, "email", APPLICANT_EMAIL)
    phone_ok = _fill_named(page, "phone", APPLICANT_PHONE)

    # Fallback для вариаций формы без стабильных name-атрибутов.
    if not (first_name_ok and last_name_ok):
        fallback_name = _fill_by_candidates(
            page,
            [
                ("label", re.compile(r"имя|фио|name", re.I)),
                ("placeholder", re.compile(r"имя|фио|name", re.I)),
            ],
            APPLICANT_NAME,
        )
        name_ok = fallback_name or (first_name_ok and last_name_ok)
    else:
        name_ok = True

    if not email_ok:
        email_ok = _fill_by_candidates(
            page,
            [
                ("label", re.compile(r"e-?mail|почт", re.I)),
                ("placeholder", re.compile(r"e-?mail|почт", re.I)),
            ],
            APPLICANT_EMAIL,
        )

    if not phone_ok:
        phone_ok = _fill_by_candidates(
            page,
            [
                ("label", re.compile(r"телефон|phone", re.I)),
                ("placeholder", re.compile(r"телефон|phone", re.I)),
            ],
            APPLICANT_PHONE,
        )

    result = {
        "name": name_ok,
        "email": email_ok,
        "phone": phone_ok,
        "cover_letter": False,
        "resume": False,
        "agree": False,
    }

    description = _first_visible(page.locator('textarea[name="description"]'))
    if description is not None:
        try:
            description.fill(cover_letter, timeout=3000)
            result["cover_letter"] = True
        except Exception:
            pass

    if not result["cover_letter"]:
        textareas = page.locator("textarea")
        try:
            count = textareas.count()
        except Exception:
            count = 0
        for index in range(count):
            item = textareas.nth(index)
            try:
                if not item.is_visible():
                    continue
                label = " ".join(
                    filter(
                        None,
                        [
                            item.get_attribute("aria-label"),
                            item.get_attribute("placeholder"),
                            item.get_attribute("name"),
                        ],
                    )
                ).lower()
                if "сопровод" in label or "комментар" in label or count == 1:
                    item.fill(cover_letter, timeout=3000)
                    result["cover_letter"] = True
                    break
            except Exception:
                continue

    resume = page.locator('input[type="file"][name="resume"]')
    try:
        if resume.count():
            resume.first.set_input_files(str(resume_path), timeout=5000)
            result["resume"] = True
    except Exception:
        pass

    if not result["resume"]:
        files = page.locator('input[type="file"]')
        try:
            file_count = files.count()
        except Exception:
            file_count = 0
        for index in range(file_count):
            item = files.nth(index)
            try:
                item.set_input_files(str(resume_path), timeout=5000)
                result["resume"] = True
                break
            except Exception:
                continue

    result["agree"] = _check_vk_agreement(page)

    required_checks = page.locator(
        'input[type="checkbox"][required], '
        'input[type="checkbox"][aria-required="true"]'
    )
    try:
        check_count = required_checks.count()
    except Exception:
        check_count = 0
    for index in range(check_count):
        item = required_checks.nth(index)
        try:
            if not item.is_checked():
                item.check(timeout=3000)
        except Exception:
            continue

    return result


def dump_form_controls(page: Page) -> None:
    print("[PROBE] Видимые form controls:")
    controls = page.locator("input, textarea, select, button")
    try:
        count = min(controls.count(), 80)
    except Exception:
        count = 0
    for index in range(count):
        item = controls.nth(index)
        try:
            if not item.is_visible():
                continue
            tag = item.evaluate("el => el.tagName.toLowerCase()")
            attrs = {
                "type": item.get_attribute("type"),
                "name": item.get_attribute("name"),
                "placeholder": item.get_attribute("placeholder"),
                "aria-label": item.get_attribute("aria-label"),
                "required": item.get_attribute("required"),
            }
            text = " ".join((item.inner_text(timeout=500) or "").split())[:100] if tag == "button" else ""
            print(f"  {tag} {attrs} text={text!r}")
        except Exception:
            continue


def find_submit(page: Page) -> Locator | None:
    candidates = [
        page.locator('button[type="submit"]'),
        page.get_by_role("button", name=SUBMIT_RE),
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
    return any(marker in text for marker in SUCCESS_MARKERS)


def process_application(page: Page, application: Application, vacancy: Vacancy) -> str:
    print("\n" + "=" * 80)
    print(f"{vacancy.title} | {vacancy.company or '-'}")
    print(vacancy.url)
    print(f"Application ID: {application.id}")
    print(f"LIVE: {LIVE}")

    evaluation = latest_evaluation(vacancy.id)
    if evaluation is None:
        print("[MANUAL] Нет Evaluation.")
        set_status(application.id, "manual_required")
        return "manual_required"

    if (evaluation.decision or "").strip().lower() != "apply":
        print(f"[SAFE] latest decision={evaluation.decision!r}; разрешён только apply.")
        set_status(application.id, "manual_required")
        return "manual_required"

    cover_letter = (application.cover_letter or evaluation.cover_letter or "").strip()
    if not cover_letter:
        print("[MANUAL] Нет сопроводительного письма.")
        set_status(application.id, "manual_required")
        return "manual_required"

    resume_key = (application.selected_resume_key or evaluation.selected_resume_key or "").strip()
    resume_title = (application.selected_resume_title or evaluation.selected_resume_title or "").strip()
    try:
        resume_path = validate_resume_asset(resume_key, resume_title)
    except Exception as exc:
        print(f"[MANUAL] Не удалось подготовить резюме: {type(exc).__name__}: {exc}")
        set_status(application.id, "manual_required")
        return "manual_required"

    print(f"[RESUME] {resume_path}")
    if LIVE:
        first_name, last_name = applicant_name_parts()
        missing = [
            name
            for name, value in (
                ("VK_APPLY_FIRST_NAME/VK_APPLY_NAME", first_name),
                ("VK_APPLY_LAST_NAME/VK_APPLY_NAME", last_name),
                ("VK_APPLY_EMAIL", APPLICANT_EMAIL),
                ("VK_APPLY_PHONE", APPLICANT_PHONE),
            )
            if not value
        ]
        if missing:
            print(f"[MANUAL] Для live-отклика не заданы: {', '.join(missing)}")
            set_status(application.id, "manual_required")
            return "manual_required"
        set_status(application.id, "applying")

    try:
        page.goto(vacancy.url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1200)
    except Exception as exc:
        print(f"[ERROR] Не удалось открыть вакансию: {type(exc).__name__}: {exc}")
        set_status(application.id, "apply_error")
        return "apply_error"

    marker = detect_manual_marker(page)
    if marker:
        print(f"[MANUAL] До формы обнаружено: {marker}")
        set_status(application.id, "manual_required")
        return "manual_required"

    if not click_apply(page):
        print("[MANUAL] Не найден контрол открытия формы отклика.")
        dump_form_controls(page)
        set_status(application.id, "manual_required")
        return "manual_required"

    print("[OK] Форма отклика открыта")
    dump_form_controls(page)

    marker = detect_manual_marker(page)
    if marker:
        print(f"[MANUAL] После открытия формы обнаружено: {marker}")
        set_status(application.id, "manual_required")
        return "manual_required"

    filled = fill_known_fields(page, cover_letter, resume_path)
    print(f"[FORM] filled={filled}")

    submit = find_submit(page)
    if submit is None:
        print("[MANUAL] Не найден готовый submit control.")
        set_status(application.id, "manual_required")
        return "manual_required"

    if not LIVE:
        print("[SAFE] VK_APPLY_LIVE=false — форма подготовлена, финальный submit НЕ нажат.")
        return "dry_run_ready"

    required_fields = ("name", "email", "phone", "cover_letter", "resume", "agree")
    missing_fields = [key for key in required_fields if not filled.get(key)]
    if missing_fields:
        print(
            "[MANUAL] Перед live-submit не заполнены обязательные поля: "
            + ", ".join(missing_fields)
        )
        set_status(application.id, "manual_required")
        return "manual_required"

    marker = detect_manual_marker(page)
    if marker:
        print(f"[MANUAL] Перед отправкой обнаружено: {marker}")
        set_status(application.id, "manual_required")
        return "manual_required"

    print("[STEP] Отправляю отклик VK...")
    try:
        submit.click(timeout=5000)
        page.wait_for_timeout(1000)
    except Exception as exc:
        print(f"[ERROR] Ошибка финального submit: {type(exc).__name__}: {exc}")
        set_status(application.id, "apply_error")
        return "apply_error"

    if captcha_is_visible(page):
        set_status(application.id, "waiting_captcha")
        if not wait_for_captcha_resolution(page):
            set_status(application.id, "manual_required")
            return "manual_required"
        set_status(application.id, "applying")
    else:
        page.wait_for_timeout(1500)

    if confirm_success(page):
        print("[SUCCESS] Отклик VK подтверждён.")
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
    print("VK APPLY WORKER")
    print("=" * 80)
    print(f"В очереди approved/vk: {len(queue)}")
    print(f"Максимум за проход: {MAX_PER_RUN}")
    print(f"Headless: {HEADLESS}")
    print(f"LIVE: {LIVE}")
    print(f"Ожидание CAPTCHA: {CAPTCHA_WAIT_SECONDS} сек.")

    if not queue:
        print("Отправлять нечего.")
        return

    stats: dict[str, int] = {}

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=HEADLESS,
            viewport={"width": 1440, "height": 1000},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
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
    print("VK APPLY WORKER DONE")
    for key, value in stats.items():
        print(f"{key}: {value}")
    print("=" * 80)


if __name__ == "__main__":
    main()
