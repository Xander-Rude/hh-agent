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

from app.application_assets import validate_career_project_resume_asset
from app.db import Application, SessionLocal, Vacancy
from app.external_apply_policy import approved_for_dispatch


load_dotenv()

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
SUCCESS_LOG = LOG_DIR / "tbank_apply_worker.log"
ATTENTION_LOG = LOG_DIR / "tbank_apply_worker_attention.log"

HEADLESS = os.getenv("TBANK_APPLY_HEADLESS", "true").lower() == "true"
LIVE = os.getenv("TBANK_APPLY_LIVE", "false").lower() == "true"
MAX_PER_RUN = int(os.getenv("TBANK_APPLY_MAX_PER_RUN", "5"))
DELAY_SECONDS = float(os.getenv("TBANK_APPLY_DELAY_SECONDS", "3"))
TARGET_APPLICATION_ID = os.getenv("TBANK_APPLY_APPLICATION_ID", "").strip()
SUCCESS_WAIT_SECONDS = int(os.getenv("TBANK_APPLY_SUCCESS_WAIT_SECONDS", "12"))

_FIRST_NAME = (
    os.getenv("TBANK_APPLY_FIRST_NAME", "").strip()
    or os.getenv("VK_APPLY_FIRST_NAME", "").strip()
)
_LAST_NAME = (
    os.getenv("TBANK_APPLY_LAST_NAME", "").strip()
    or os.getenv("VK_APPLY_LAST_NAME", "").strip()
)
_FALLBACK_NAME = (
    os.getenv("TBANK_APPLY_NAME", "").strip()
    or os.getenv("VK_APPLY_NAME", "").strip()
)
APPLICANT_NAME = (
    f"{_LAST_NAME} {_FIRST_NAME}".strip()
    if _FIRST_NAME and _LAST_NAME
    else _FALLBACK_NAME
)
APPLICANT_CITY = os.getenv("TBANK_APPLY_CITY", "Москва").strip()
APPLICANT_EMAIL = (
    os.getenv("TBANK_APPLY_EMAIL", "").strip()
    or os.getenv("VK_APPLY_EMAIL", "").strip()
)
APPLICANT_PHONE = (
    os.getenv("TBANK_APPLY_PHONE", "").strip()
    or os.getenv("VK_APPLY_PHONE", "").strip()
)
APPLICANT_SOCIAL_LINK = os.getenv("TBANK_APPLY_SOCIAL_LINK", "").strip()

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

SUCCESS_MARKERS = (
    "спасибо за отклик",
    "спасибо за ваш отклик",
    "мы получили ваш отклик",
    "мы получили ваше резюме",
    "отклик отправлен",
    "резюме отправлено",
    "заявка отправлена",
    "заявка принята",
)
FAILURE_MARKERS = (
    "не удалось отправить",
    "ошибка при отправке",
    "произошла ошибка",
    "попробуйте еще раз",
    "попробуйте ещё раз",
)
INACTIVE_MARKERS = (
    "набор по вакансии закрыт",
    "вакансия закрыта",
    "вакансия уже закрыта",
    "вакансия не актуальна",
    "позиция закрыта",
)
CAPTCHA_RE = re.compile(
    r"captcha|капча|я\s+не\s+робот|подтвердите,\s+что\s+вы\s+не\s+робот",
    re.IGNORECASE,
)


def set_status(
    application_id: int,
    status: str,
    *,
    applied: bool = False,
) -> None:
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
        file.write(
            f"\n[{stamp}] RESULT={result} LIVE={LIVE}\n"
        )
        file.write(output.strip() or "No output captured.")
        file.write("\n")


def load_queue() -> list[tuple[Application, Vacancy]]:
    session = SessionLocal()
    try:
        query = (
            select(Application, Vacancy)
            .join(Vacancy, Vacancy.id == Application.vacancy_id)
            .where(
                Application.status == "approved",
                Vacancy.source == "tbank",
            )
            .order_by(Application.created_at.asc())
        )

        if TARGET_APPLICATION_ID:
            try:
                target_id = int(TARGET_APPLICATION_ID)
            except ValueError:
                print(
                    "[ERROR] TBANK_APPLY_APPLICATION_ID должен быть целым числом."
                )
                return []

            query = query.where(
                Application.id == target_id
            ).limit(1)
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


def accept_cookie_banner(page: Page) -> None:
    try:
        button = page.get_by_role(
            "button",
            name=re.compile(r"^хорошо$", re.IGNORECASE),
        )
        item = _first_visible(button)
        if item is not None:
            item.click(timeout=2500)
            page.wait_for_timeout(250)
    except Exception:
        return


def captcha_is_visible(page: Page) -> bool:
    text = body_text(page)
    if CAPTCHA_RE.search(text):
        return True

    for selector in (
        'iframe[src*="captcha" i]',
        'iframe[title*="captcha" i]',
        '[id*="captcha" i]',
        '[class*="captcha" i]',
    ):
        try:
            item = _first_visible(page.locator(selector))
            if item is not None:
                return True
        except Exception:
            continue

    return False


def vacancy_is_inactive(page: Page) -> bool:
    text = body_text(page)
    return any(marker in text for marker in INACTIVE_MARKERS)


def _fill_named(page: Page, name: str, value: str) -> bool:
    if not value:
        return False

    item = _first_visible(page.locator(f'input[name="{name}"]'))
    if item is None:
        return False

    try:
        item.fill(value, timeout=4000)
        item.press("Tab", timeout=1000)
        return True
    except Exception:
        return False


def fill_city(page: Page, city_name: str) -> bool:
    if not city_name:
        return False

    city = _first_visible(page.locator('input[name="city"]'))
    if city is None:
        return False

    try:
        city.click(timeout=3000)
        city.fill("", timeout=3000)
        # T-Bank autocomplete is more reliable with real key events than with
        # one-shot fill(): suggestions can otherwise fail to render.
        city.type(
            city_name,
            delay=100,
            timeout=5000,
        )
        page.wait_for_timeout(1400)
    except Exception:
        return False

    try:
        options = page.get_by_role("option")
        for index in range(options.count()):
            option = options.nth(index)
            try:
                if not option.is_visible():
                    continue
                text = " ".join(
                    (option.inner_text(timeout=500) or "").split()
                )
                if city_name.lower() in text.lower():
                    option.click(timeout=2500)
                    page.wait_for_timeout(350)
                    return bool(city.input_value().strip())
            except Exception:
                continue
    except Exception:
        pass

    try:
        city.press("ArrowDown")
        page.wait_for_timeout(150)
        city.press("Enter")
        page.wait_for_timeout(350)
        return bool(city.input_value().strip())
    except Exception:
        return False


def _resume_upload_visible_in_form(
    page: Page,
    resume_path: Path,
) -> bool:
    """T-Bank clears the native file input after ingesting the attachment.

    The reliable confirmation is the rendered attachment row in the vacancy
    form, which contains the uploaded filename and file type.
    """

    try:
        form = page.locator("form").filter(
            has=page.locator('input[name="email"]')
        ).first
        text = " ".join(
            (form.inner_text(timeout=3000) or "").split()
        ).lower()
    except Exception:
        return False

    stem = " ".join(
        resume_path.stem.lower().split()
    )
    return bool(stem and stem in text and ".pdf" in text)


def upload_resume(page: Page, resume_path: Path) -> bool:
    # The native input may be visually hidden. Also, after T-Bank's React
    # uploader consumes the file it clears the native input state, so success
    # must be confirmed from the rendered attachment row instead.
    inputs = page.locator('input[type="file"]')
    try:
        count = inputs.count()
    except Exception:
        return False

    try:
        payload = {
            "name": resume_path.name,
            "mimeType": "application/pdf",
            "buffer": resume_path.read_bytes(),
        }
    except OSError:
        return False

    for index in range(count):
        file_input = inputs.nth(index)
        try:
            file_input.set_input_files(
                payload,
                timeout=5000,
            )
            page.wait_for_timeout(1200)
            if _resume_upload_visible_in_form(
                page,
                resume_path,
            ):
                return True
        except Exception:
            continue

    return False


def fill_optional_social_link(page: Page) -> bool:
    if not APPLICANT_SOCIAL_LINK:
        return True

    return _fill_named(
        page,
        "social_link0",
        APPLICANT_SOCIAL_LINK,
    )


def fill_application_form(
    page: Page,
    resume_path: Path,
) -> dict[str, bool]:
    return {
        "name": _fill_named(
            page,
            "name",
            APPLICANT_NAME,
        ),
        "city": fill_city(
            page,
            APPLICANT_CITY,
        ),
        "email": _fill_named(
            page,
            "email",
            APPLICANT_EMAIL,
        ),
        "phone": _fill_named(
            page,
            "phone",
            APPLICANT_PHONE,
        ),
        "resume": upload_resume(
            page,
            resume_path,
        ),
        "social_link": fill_optional_social_link(
            page,
        ),
    }


def find_submit(page: Page) -> Locator | None:
    candidates = [
        page.locator(
            'button[type="submit"][name="submit"]'
        ),
        page.get_by_role(
            "button",
            name=re.compile(
                r"^отправить$",
                re.IGNORECASE,
            ),
        ),
    ]

    for locator in candidates:
        item = _first_visible(locator)
        if item is None:
            continue

        try:
            if not item.is_disabled():
                return item
        except Exception:
            continue

    return None


def form_is_visible(page: Page) -> bool:
    try:
        form = _first_visible(
            page.locator("form").filter(
                has=page.locator('input[name="email"]')
            )
        )
        return form is not None
    except Exception:
        return (
            _first_visible(
                page.locator('input[name="email"]')
            )
            is not None
        )


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
            if not form_is_visible(page) and find_submit(page) is None:
                return True, "форма и submit исчезли после отправки"

        page.wait_for_timeout(500)

    visible_form = form_is_visible(page)
    visible_submit = find_submit(page) is not None

    print(
        "[PROBE] После submit: "
        f"url={page.url!r}, "
        f"form_visible={visible_form}, "
        f"submit_visible={visible_submit}"
    )

    if last_text:
        compact = " ".join(last_text.split())
        print(
            "[PROBE] Текст страницы после submit: "
            f"{compact[-700:]!r}"
        )

    return False, "явного подтверждения успеха не найдено"


def process_application(
    page: Page,
    application: Application,
    vacancy: Vacancy,
) -> str:
    print("\n" + "=" * 80)
    print(f"{vacancy.title} | {vacancy.company or '-'}")
    print(vacancy.url)
    print(f"Application ID: {application.id}")
    print(f"LIVE: {LIVE}")

    if not approved_for_dispatch(application):
        print(
            f"[SAFE] Application status={application.status!r}; "
            "отклик разрешён только после явного approve."
        )
        return "manual_required"

    missing_config = [
        name
        for name, value in (
            ("name", APPLICANT_NAME),
            ("city", APPLICANT_CITY),
            ("email", APPLICANT_EMAIL),
            ("phone", APPLICANT_PHONE),
        )
        if not value
    ]
    if missing_config:
        print(
            "[MANUAL] Не заданы данные кандидата: "
            + ", ".join(missing_config)
        )
        set_status(
            application.id,
            "manual_required",
        )
        return "manual_required"

    try:
        resume_path = validate_career_project_resume_asset()
    except Exception as exc:
        print(
            "[MANUAL] Не удалось подготовить резюме: "
            f"{type(exc).__name__}: {exc}"
        )
        set_status(
            application.id,
            "manual_required",
        )
        return "manual_required"

    print(
        f"[RESUME] fixed project resume: {resume_path}"
    )

    if LIVE:
        set_status(
            application.id,
            "applying",
        )

    try:
        page.goto(
            vacancy.url,
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        page.wait_for_timeout(1400)
    except Exception as exc:
        print(
            "[ERROR] Не удалось открыть вакансию: "
            f"{type(exc).__name__}: {exc}"
        )
        set_status(
            application.id,
            "apply_error",
        )
        return "apply_error"

    accept_cookie_banner(page)

    if vacancy_is_inactive(page):
        print(
            "[MANUAL] Вакансия на сайте уже закрыта или неактивна."
        )
        set_status(
            application.id,
            "manual_required",
        )
        return "manual_required"

    if captcha_is_visible(page):
        print(
            "[MANUAL] CAPTCHA обнаружена до заполнения формы."
        )
        set_status(
            application.id,
            "manual_required",
        )
        return "manual_required"

    if not form_is_visible(page):
        print(
            "[MANUAL] Стандартная форма отклика Т-Банка не найдена."
        )
        set_status(
            application.id,
            "manual_required",
        )
        return "manual_required"

    filled = fill_application_form(
        page,
        resume_path,
    )
    print(f"[FORM] filled={filled}")

    required_fields = (
        "name",
        "city",
        "email",
        "phone",
        "resume",
    )
    missing_fields = [
        key
        for key in required_fields
        if not filled.get(key)
    ]

    if missing_fields:
        print(
            "[MANUAL] Перед submit не заполнены обязательные поля: "
            + ", ".join(missing_fields)
        )
        set_status(
            application.id,
            "manual_required",
        )
        return "manual_required"

    if captcha_is_visible(page):
        print(
            "[MANUAL] CAPTCHA появилась после заполнения формы."
        )
        set_status(
            application.id,
            "manual_required",
        )
        return "manual_required"

    submit = find_submit(page)
    if submit is None:
        print(
            "[MANUAL] Не найден готовый submit control."
        )
        set_status(
            application.id,
            "manual_required",
        )
        return "manual_required"

    if not LIVE:
        print(
            "[SAFE] TBANK_APPLY_LIVE=false — "
            "форма заполнена, финальный submit НЕ нажат."
        )
        return "dry_run_ready"

    print("[STEP] Отправляю отклик Т-Банка...")
    try:
        submit.click(timeout=5000)
        page.wait_for_timeout(1000)
    except Exception as exc:
        print(
            "[ERROR] Ошибка финального submit: "
            f"{type(exc).__name__}: {exc}"
        )
        set_status(
            application.id,
            "apply_error",
        )
        return "apply_error"

    if captcha_is_visible(page):
        print(
            "[MANUAL] CAPTCHA появилась после submit. "
            "Повторно автоматически НЕ отправлять."
        )
        set_status(
            application.id,
            "manual_required",
        )
        return "manual_required"

    success, reason = confirm_success(page)
    if success:
        print(
            f"[SUCCESS] Отклик Т-Банка подтверждён: {reason}."
        )
        set_status(
            application.id,
            "applied",
            applied=True,
        )
        return "applied"

    print(
        "[MANUAL] Submit был нажат, но подтверждение успеха "
        f"не найдено ({reason}). "
        "Повторно автоматически НЕ отправлять."
    )
    set_status(
        application.id,
        "manual_required",
    )
    return "manual_required"


def main() -> None:
    queue = load_queue()

    print("\n" + "=" * 80)
    print("TBANK APPLY WORKER")
    print("=" * 80)
    print(
        f"В очереди approved/tbank: {len(queue)}"
    )
    print(
        f"Максимум за проход: {MAX_PER_RUN}"
    )
    print(
        f"Headless: {HEADLESS}"
    )
    print(
        f"LIVE: {LIVE}"
    )

    if not queue:
        print("Отправлять нечего.")
        return

    stats: dict[str, int] = {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=HEADLESS,
        )
        context = browser.new_context(
            ignore_https_errors=True,
            locale="ru-RU",
            user_agent=USER_AGENT,
            viewport={
                "width": 1440,
                "height": 1000,
            },
        )
        try:
            page = context.new_page()

            for index, (
                application,
                vacancy,
            ) in enumerate(
                queue,
                start=1,
            ):
                output = StringIO()
                try:
                    with redirect_stdout(output):
                        print(
                            f"[{index}/{len(queue)}]"
                        )
                        result = process_application(
                            page,
                            application,
                            vacancy,
                        )
                except Exception as exc:
                    result = "apply_error"
                    set_status(
                        application.id,
                        "apply_error",
                    )
                    with redirect_stdout(output):
                        print(
                            "[ERROR] Необработанная ошибка: "
                            f"{type(exc).__name__}: {exc}"
                        )

                append_log(
                    result,
                    output.getvalue(),
                )
                print(
                    output.getvalue(),
                    end="",
                )
                stats[result] = (
                    stats.get(result, 0)
                    + 1
                )

                if index < len(queue):
                    time.sleep(DELAY_SECONDS)
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass

    print("\n" + "=" * 80)
    print("TBANK APPLY WORKER DONE")
    for key, value in stats.items():
        print(f"{key}: {value}")
    print("=" * 80)


if __name__ == "__main__":
    main()
