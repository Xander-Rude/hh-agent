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
SUCCESS_WAIT_SECONDS = int(os.getenv("VK_APPLY_SUCCESS_WAIT_SECONDS", "10"))

APPLICANT_NAME = os.getenv("VK_APPLY_NAME", "").strip()
APPLICANT_FIRST_NAME = os.getenv("VK_APPLY_FIRST_NAME", "").strip()
APPLICANT_LAST_NAME = os.getenv("VK_APPLY_LAST_NAME", "").strip()
APPLICANT_EMAIL = os.getenv("VK_APPLY_EMAIL", "").strip()
APPLICANT_PHONE = os.getenv("VK_APPLY_PHONE", "").strip()
APPLICANT_SOCIAL_LINKS = os.getenv(
    "VK_APPLY_SOCIAL_LINKS",
    "https://max.ru/u/f9LHodD0cOJdbgSSISBLFqJzADwtUSf_wg_bLeV7_xeomDfQI0ikpLlYIEI; https://t.me/xander_rude",
).strip()

MANUAL_MARKERS = (
    "тестовое задание", "пройти тест", "выполнить тест",
    "ответьте на вопросы", "дополнительные вопросы",
)
SUCCESS_MARKERS = (
    "отклик отправлен", "отклик успешно отправлен", "ваш отклик отправлен",
    "спасибо за отклик", "спасибо за ваш отклик", "резюме отправлено",
    "резюме получено", "заявка отправлена", "ваша заявка отправлена",
    "заявка принята", "мы получили ваш отклик", "мы получили ваше резюме",
)
FAILURE_MARKERS = (
    "не удалось отправить", "ошибка при отправке", "произошла ошибка",
    "попробуйте еще раз", "попробуйте ещё раз",
)
CAPTCHA_TEXT_RE = re.compile(r"captcha|капча|я\s+не\s+робот", re.IGNORECASE)
CAPTCHA_FRAME_RE = re.compile(r"captcha|recaptcha|smartcaptcha", re.IGNORECASE)
APPLY_RE = re.compile(r"отклик|отправить\s+резюме|оставить\s+резюме", re.IGNORECASE)
SUBMIT_RE = re.compile(r"отправить|откликнуться|отправить\s+резюме", re.IGNORECASE)
FORM_FIELD_SELECTOR = (
    'input[name="first_name"], input[name="last_name"], input[name="email"], '
    'input[name="phone"], textarea[name="description"], input[name="social_links"], '
    'input[name="resume"], input[name="agree"]'
)

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
            .where(Application.status == "approved", Vacancy.source == "vk")
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
        result = []
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
    return next((marker for marker in MANUAL_MARKERS if marker in text), None)

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

def _has_visible(locator: Locator) -> bool:
    return _first_visible(locator) is not None

def captcha_is_visible(page: Page) -> bool:
    for selector in (
        'iframe[src*="captcha" i]', 'iframe[title*="captcha" i]',
        '[id*="captcha" i]', '[class*="captcha" i]',
    ):
        try:
            if _has_visible(page.locator(selector)):
                return True
        except Exception:
            continue
    try:
        if _has_visible(page.get_by_text(CAPTCHA_TEXT_RE)):
            return True
    except Exception:
        pass
    for frame in page.frames:
        try:
            if CAPTCHA_FRAME_RE.search(frame.url or ""):
                if frame.frame_element().is_visible():
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
    print(f"[WAIT] Пройди CAPTCHA в открытом окне браузера. Жду до {CAPTCHA_WAIT_SECONDS} сек.")
    deadline = time.monotonic() + CAPTCHA_WAIT_SECONDS
    while time.monotonic() < deadline:
        if not captcha_is_visible(page):
            print("[OK] CAPTCHA исчезла. Проверяю фактический результат отклика...")
            page.wait_for_timeout(1000)
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
            locator = page.get_by_label(pattern) if kind == "label" else page.get_by_placeholder(pattern)
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
    agree = page.locator('input[type="checkbox"][name="agree"]').first
    try:
        if agree.count() == 0:
            return False
    except Exception:
        return False
    try:
        if agree.is_checked():
            return True
    except Exception:
        pass
    for force in (False, True):
        try:
            agree.check(force=force, timeout=2000)
            if agree.is_checked():
                return True
        except Exception:
            pass
    try:
        agree_id = agree.get_attribute("id")
        if agree_id:
            label = page.locator(f'label[for="{agree_id}"]').first
            if label.count() and label.is_visible():
                label.click(timeout=2000)
                if agree.is_checked():
                    return True
    except Exception:
        pass
    try:
        label = agree.locator("xpath=ancestor::label[1]")
        if label.count() and label.first.is_visible():
            label.first.click(timeout=2000)
            if agree.is_checked():
                return True
    except Exception:
        pass
    try:
        agree.evaluate(
            """el => {
                el.checked = true;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }"""
        )
        return agree.is_checked()
    except Exception:
        return False

def fill_known_fields(page: Page, about_me: str, resume_path: Path) -> dict[str, bool]:
    first_name, last_name = applicant_name_parts()
    first_name_ok = _fill_named(page, "first_name", first_name)
    last_name_ok = _fill_named(page, "last_name", last_name)
    email_ok = _fill_named(page, "email", APPLICANT_EMAIL)
    phone_ok = _fill_named(page, "phone", APPLICANT_PHONE)

    if not (first_name_ok and last_name_ok):
        fallback_name = _fill_by_candidates(
            page,
            [("label", re.compile(r"имя|фио|name", re.I)),
             ("placeholder", re.compile(r"имя|фио|name", re.I))],
            APPLICANT_NAME,
        )
        name_ok = fallback_name or (first_name_ok and last_name_ok)
    else:
        name_ok = True

    if not email_ok:
        email_ok = _fill_by_candidates(
            page,
            [("label", re.compile(r"e-?mail|почт", re.I)),
             ("placeholder", re.compile(r"e-?mail|почт", re.I))],
            APPLICANT_EMAIL,
        )
    if not phone_ok:
        phone_ok = _fill_by_candidates(
            page,
            [("label", re.compile(r"телефон|phone", re.I)),
             ("placeholder", re.compile(r"телефон|phone", re.I))],
            APPLICANT_PHONE,
        )

    result = {
        "name": name_ok, "email": email_ok, "phone": phone_ok,
        "about_me": False, "social_links": False, "resume": False, "agree": False,
    }

    description = _first_visible(page.locator('textarea[name="description"]'))
    if description is not None:
        try:
            description.fill(about_me, timeout=3000)
            result["about_me"] = True
        except Exception:
            pass

    if not result["about_me"]:
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
                label = " ".join(filter(None, [
                    item.get_attribute("aria-label"),
                    item.get_attribute("placeholder"),
                    item.get_attribute("name"),
                ])).lower()
                if "расскажи" in label or "о себе" in label or "сопровод" in label or count == 1:
                    item.fill(about_me, timeout=3000)
                    result["about_me"] = True
                    break
            except Exception:
                continue

    result["social_links"] = _fill_named(page, "social_links", APPLICANT_SOCIAL_LINKS)

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
            try:
                files.nth(index).set_input_files(str(resume_path), timeout=5000)
                result["resume"] = True
                break
            except Exception:
                continue

    result["agree"] = _check_vk_agreement(page)
    required_checks = page.locator(
        'input[type="checkbox"][required], input[type="checkbox"][aria-required="true"]'
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
    candidates = [page.locator('button[type="submit"]'), page.get_by_role("button", name=SUBMIT_RE)]
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

def _form_is_visible(page: Page) -> bool:
    try:
        return _has_visible(page.locator(FORM_FIELD_SELECTOR))
    except Exception:
        return False

def confirm_success(page: Page) -> tuple[bool, str]:
    deadline = time.monotonic() + SUCCESS_WAIT_SECONDS
    last_text = ""
    while time.monotonic() < deadline:
        if page.is_closed():
            return False, "страница браузера закрыта"
        text = body_text(page)
        last_text = text
        for marker in FAILURE_MARKERS:
            if marker in text:
                return False, f"обнаружен текст ошибки: {marker}"
        for marker in SUCCESS_MARKERS:
            if marker in text:
                return True, f"текстовый marker: {marker}"
        if not captcha_is_visible(page):
            if not _form_is_visible(page) and find_submit(page) is None:
                return True, "форма отклика и submit исчезли после отправки"
        page.wait_for_timeout(500)

    visible_form = _form_is_visible(page)
    visible_submit = find_submit(page) is not None
    print(f"[PROBE] После submit: url={page.url!r}, form_visible={visible_form}, submit_visible={visible_submit}")
    if last_text:
        compact = " ".join(last_text.split())
        print(f"[PROBE] Текст страницы после submit: {compact[:500]!r}")
    return False, "явного подтверждения успеха не найдено"

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

    about_me = (application.cover_letter or evaluation.cover_letter or "").strip()
    if not about_me:
        print("[MANUAL] Нет текста для поля «Расскажи о себе».")
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
            name for name, value in (
                ("VK_APPLY_FIRST_NAME/VK_APPLY_NAME", first_name),
                ("VK_APPLY_LAST_NAME/VK_APPLY_NAME", last_name),
                ("VK_APPLY_EMAIL", APPLICANT_EMAIL),
                ("VK_APPLY_PHONE", APPLICANT_PHONE),
                ("VK_APPLY_SOCIAL_LINKS", APPLICANT_SOCIAL_LINKS),
            ) if not value
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

    filled = fill_known_fields(page, about_me, resume_path)
    print(f"[FORM] filled={filled}")

    submit = find_submit(page)
    if submit is None:
        print("[MANUAL] Не найден готовый submit control.")
        set_status(application.id, "manual_required")
        return "manual_required"

    if not LIVE:
        print("[SAFE] VK_APPLY_LIVE=false — форма подготовлена, финальный submit НЕ нажат.")
        return "dry_run_ready"

    required_fields = ("name", "email", "phone", "about_me", "social_links", "resume", "agree")
    missing_fields = [key for key in required_fields if not filled.get(key)]
    if missing_fields:
        print("[MANUAL] Перед live-submit не заполнены обязательные поля: " + ", ".join(missing_fields))
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

    success, reason = confirm_success(page)
    if success:
        print(f"[SUCCESS] Отклик VK подтверждён: {reason}.")
        set_status(application.id, "applied", applied=True)
        return "applied"

    print(
        f"[MANUAL] Submit был нажат, но подтверждение успеха не найдено ({reason}). "
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
    print(f"Ожидание подтверждения: {SUCCESS_WAIT_SECONDS} сек.")

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
