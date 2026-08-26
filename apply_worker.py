import os
import time
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import (
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)
from sqlalchemy import select

from application_notifications import notify_manual_required

from app.db import (
    Application,
    SessionLocal,
    Vacancy,
)


load_dotenv()


ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
SUCCESS_LOG = LOG_DIR / "apply_worker.log"
ATTENTION_LOG = LOG_DIR / "apply_worker_attention.log"


PROFILE_DIR = Path("browser-profile")

HEADLESS = (
    os.getenv(
        "HH_APPLY_HEADLESS",
        "false",
    ).lower()
    == "true"
)

MAX_PER_RUN = int(
    os.getenv(
        "HH_APPLY_MAX_PER_RUN",
        "10",
    )
)

DELAY_SECONDS = float(
    os.getenv(
        "HH_APPLY_DELAY_SECONDS",
        "3",
    )
)


# Кнопки первого уровня "Откликнуться"
APPLY_BUTTON_SELECTORS = [
    '[data-qa="vacancy-response-link-top"]',
    '[data-qa="vacancy-response-link-bottom"]',
    'a[data-qa*="vacancy-response"]',
    'button[data-qa*="vacancy-response"]',
]


# Возможные поля сопроводительного.
COVER_LETTER_SELECTORS = [
    'textarea[data-qa*="vacancy-response-letter"]',
    'textarea[data-qa*="cover-letter"]',
    'textarea[name*="letter"]',
    'textarea',
]


# HH может сначала показывать только ссылку/кнопку
# «Добавить сопроводительное», а textarea создавать после клика.
COVER_LETTER_TRIGGER_SELECTORS = [
    'button[data-qa*="vacancy-response-letter"]',
    'a[data-qa*="vacancy-response-letter"]',
    'button[data-qa*="cover-letter"]',
    'a[data-qa*="cover-letter"]',
]


# Финальные кнопки отправки.
FINAL_SUBMIT_SELECTORS = [
    'button[data-qa="vacancy-response-submit-popup"]',
    'button[data-qa*="vacancy-response-submit"]',
    'button[data-qa*="response-submit"]',
]


MANUAL_MARKERS = [
    "ответьте на вопросы",
    "ответить на вопросы",
    "вопросы работодателя",
    "анкета работодателя",
    "пройти тест",
    "тестовое задание",
    "выполнить тест",
    "captcha",
    "капча",
    "подтвердите, что вы не робот",
]


ALREADY_APPLIED_MARKERS = [
    "вы откликнулись",
    "вы уже откликнулись",
    "отклик отправлен",
    "резюме отправлено",
]


SUCCESS_MARKERS = [
    "отклик отправлен",
    "вы откликнулись",
    "резюме отправлено",
]


def page_text(
    page: Page,
) -> str:
    try:
        return (
            page.locator("body")
            .inner_text(timeout=5000)
            .lower()
        )
    except Exception:
        return ""


def contains_any(
    text: str,
    markers: list[str],
) -> bool:
    lower_text = text.lower()

    return any(
        marker.lower() in lower_text
        for marker in markers
    )


def append_application_log(
    result: str,
    output: str,
) -> None:
    """
    Keep confirmed applications separate from cases that need attention.

    applied -> logs/apply_worker.log
    manual_required/apply_error/unknown -> logs/apply_worker_attention.log
    """
    LOG_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    log_path = (
        SUCCESS_LOG
        if result == "applied"
        else ATTENTION_LOG
    )

    timestamp = (
        datetime.now()
        .astimezone()
        .isoformat(timespec="seconds")
    )

    cleaned_output = output.strip()

    if not cleaned_output:
        cleaned_output = "No application output captured."

    with log_path.open(
        "a",
        encoding="utf-8",
    ) as log:
        log.write(
            f"\n[{timestamp}] RESULT={result}\n"
        )
        log.write(cleaned_output)
        log.write("\n")


def find_visible(
    page: Page,
    selectors: list[str],
):
    for selector in selectors:
        locator = page.locator(selector)

        try:
            count = locator.count()
        except Exception:
            continue

        for index in range(count):
            item = locator.nth(index)

            try:
                if item.is_visible():
                    return item
            except Exception:
                continue

    return None


def get_button_by_text(
    page: Page,
    texts: list[str],
):
    for text in texts:
        locator = page.get_by_role(
            "button",
            name=text,
            exact=False,
        )

        try:
            count = locator.count()
        except Exception:
            continue

        for index in range(count):
            button = locator.nth(index)

            try:
                if button.is_visible():
                    return button
            except Exception:
                continue

    return None


def set_status(
    application_id: int,
    status: str,
    applied: bool = False,
    manual_reason: str | None = None,
) -> None:
    session = SessionLocal()
    notification = None

    try:
        application = session.get(
            Application,
            application_id,
        )

        if application is None:
            return

        previous_status = application.status
        application.status = status

        if applied:
            application.applied_at = (
                datetime.utcnow()
            )

        if (
            status == "manual_required"
            and previous_status != "manual_required"
        ):
            vacancy = session.get(
                Vacancy,
                application.vacancy_id,
            )

            if vacancy is not None:
                notification = {
                    "vacancy_title": vacancy.title,
                    "company": vacancy.company,
                    "vacancy_url": vacancy.url,
                    "application_id": application.id,
                    "reason": (
                        manual_reason
                        or (
                            "Автоматический отклик не отправлен "
                            "или HH не подтвердил отправку."
                        )
                    ),
                }

        session.commit()

    finally:
        session.close()

    if notification is not None:
        notify_manual_required(
            **notification
        )

def detect_manual_required(
    page: Page,
) -> str | None:
    text = page_text(page)

    for marker in MANUAL_MARKERS:
        if marker in text:
            return marker

    return None


def already_applied(
    page: Page,
) -> bool:
    return contains_any(
        page_text(page),
        ALREADY_APPLIED_MARKERS,
    )


def find_cover_letter_trigger(
    page: Page,
):
    trigger = find_visible(
        page,
        COVER_LETTER_TRIGGER_SELECTORS,
    )

    if trigger is not None:
        return trigger

    # Fallback по тексту. На разных версиях формы HH
    # элемент может быть button или link.
    texts = [
        "Добавить сопроводительное",
        "Добавить сопроводительное письмо",
        "Сопроводительное письмо",
        "Добавить письмо",
    ]

    for role in ("button", "link"):
        for text in texts:
            locator = page.get_by_role(
                role,
                name=text,
                exact=False,
            )

            try:
                count = locator.count()
            except Exception:
                continue

            for index in range(count):
                item = locator.nth(index)

                try:
                    if item.is_visible():
                        return item
                except Exception:
                    continue

    return None


def ensure_cover_letter_field(
    page: Page,
):
    # Сначала проверяем, не открыто ли поле уже.
    field = find_visible(
        page,
        COVER_LETTER_SELECTORS,
    )

    if field is not None:
        return field

    # Если textarea скрыта за «Добавить сопроводительное»,
    # раскрываем блок и ищем поле повторно.
    trigger = find_cover_letter_trigger(page)

    if trigger is None:
        return None

    try:
        trigger.click()
        page.wait_for_timeout(800)
    except Exception:
        return None

    return find_visible(
        page,
        COVER_LETTER_SELECTORS,
    )


def fill_cover_letter(
    page: Page,
    cover_letter: str,
) -> bool:
    cover_letter = cover_letter.strip()

    if not cover_letter:
        return False

    field = ensure_cover_letter_field(page)

    if field is None:
        return False

    try:
        field.fill(cover_letter)
        page.wait_for_timeout(200)

        # Не считаем fill успешным, пока не прочитали текст обратно
        # из самого поля. Это страховка от странностей динамической формы.
        actual_value = field.input_value(
            timeout=2000
        ).strip()

        return actual_value == cover_letter

    except Exception:
        return False


def choose_resume_if_needed(
    page: Page,
) -> None:
    """
    Пока выбираем максимально консервативно.

    Если HH показывает ровно один доступный вариант
    резюме и требует его выбрать, пытаемся выбрать его.

    Если форма сложнее — дальше worker, скорее всего,
    уйдёт в manual_required.
    """

    selectors = [
        '[data-qa*="resume"] input[type="radio"]',
        'input[type="radio"][name*="resume"]',
    ]

    for selector in selectors:
        radios = page.locator(
            selector
        )

        try:
            count = radios.count()
        except Exception:
            continue

        if count == 1:
            try:
                radios.first.check()
                return
            except Exception:
                pass


def click_initial_apply(
    page: Page,
) -> bool:
    button = find_visible(
        page,
        APPLY_BUTTON_SELECTORS,
    )

    if button is None:
        # Fallback по видимому тексту.
        candidates = [
            page.get_by_role(
                "button",
                name="Откликнуться",
                exact=True,
            ),
            page.get_by_role(
                "link",
                name="Откликнуться",
                exact=True,
            ),
        ]

        for candidate in candidates:
            try:
                if (
                    candidate.count() > 0
                    and candidate.first.is_visible()
                ):
                    button = candidate.first
                    break
            except Exception:
                continue

    if button is None:
        return False

    button.click()

    page.wait_for_timeout(
        1500
    )

    return True


def find_final_submit(
    page: Page,
):
    button = find_visible(
        page,
        FINAL_SUBMIT_SELECTORS,
    )

    if button is not None:
        return button

    return get_button_by_text(
        page,
        [
            "Отправить отклик",
            "Откликнуться",
            "Отправить",
        ],
    )


def process_application(
    page: Page,
    vacancy: Vacancy,
    application: Application,
) -> str:
    print()
    print("=" * 80)

    print(
        f"{vacancy.title} | "
        f"{vacancy.company or '-'}"
    )

    print(
        vacancy.url
    )

    print(
        f"Application ID: "
        f"{application.id}"
    )

    set_status(
        application.id,
        "applying",
    )

    try:
        page.goto(
            vacancy.url,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        page.wait_for_timeout(
            1800
        )

    except Exception as exc:
        print(
            f"[ERROR] Не удалось открыть вакансию: "
            f"{exc}"
        )

        set_status(
            application.id,
            "apply_error",
        )

        return "apply_error"

    # Возможно, отклик был отправлен вручную ранее.
    if already_applied(page):
        print(
            "[INFO] HH сообщает, что отклик уже есть."
        )

        set_status(
            application.id,
            "applied",
            applied=True,
        )

        return "applied"

    manual_reason = detect_manual_required(
        page
    )

    if manual_reason:
        print(
            f"[MANUAL] Обнаружено условие: "
            f"{manual_reason}"
        )

        set_status(
            application.id,
            "manual_required",
        )

        return "manual_required"

    print(
        "[STEP] Нажимаю первоначальное "
        "«Откликнуться»..."
    )

    try:
        clicked = click_initial_apply(
            page
        )

    except Exception as exc:
        print(
            f"[ERROR] Ошибка при открытии "
            f"формы отклика: {exc}"
        )

        set_status(
            application.id,
            "apply_error",
        )

        return "apply_error"

    if not clicked:
        print(
            "[MANUAL] Не нашёл стандартную "
            "кнопку «Откликнуться»."
        )

        set_status(
            application.id,
            "manual_required",
        )

        return "manual_required"

    # В редком случае первый click уже мог
    # завершить стандартный отклик.
    if already_applied(page):
        print(
            "[SUCCESS] HH уже показывает "
            "успешный отклик."
        )

        set_status(
            application.id,
            "applied",
            applied=True,
        )

        return "applied"

    manual_reason = detect_manual_required(
        page
    )

    if manual_reason:
        print(
            f"[MANUAL] После открытия формы "
            f"обнаружено: {manual_reason}"
        )

        set_status(
            application.id,
            "manual_required",
        )

        return "manual_required"

    choose_resume_if_needed(
        page
    )

    cover_letter = (
        application.cover_letter
        or ""
    ).strip()

    # Жёсткая страховка: этот worker не отправляет отклики
    # без сопроводительного письма ни при каких обстоятельствах.
    if not cover_letter:
        print(
            "[MANUAL] В application отсутствует "
            "сопроводительное письмо. "
            "Отклик НЕ отправляю."
        )

        set_status(
            application.id,
            "manual_required",
        )

        return "manual_required"

    filled = fill_cover_letter(
        page,
        cover_letter,
    )

    if not filled:
        print(
            "[MANUAL] Не удалось открыть, найти "
            "или заполнить поле сопроводительного. "
            "Отклик НЕ отправляю."
        )

        set_status(
            application.id,
            "manual_required",
        )

        return "manual_required"

    print(
        "[STEP] Сопроводительное вставлено "
        "и проверено."
    )

    manual_reason = detect_manual_required(
        page
    )

    if manual_reason:
        print(
            f"[MANUAL] Перед отправкой "
            f"обнаружено: {manual_reason}"
        )

        set_status(
            application.id,
            "manual_required",
        )

        return "manual_required"

    submit_button = find_final_submit(
        page
    )

    if submit_button is None:
        print(
            "[MANUAL] Не найдена стандартная "
            "кнопка финальной отправки."
        )

        set_status(
            application.id,
            "manual_required",
        )

        return "manual_required"

    # Последняя страховка:
    # если на странице появились анкета / тест / CAPTCHA,
    # ничего не отправляем.
    manual_reason = detect_manual_required(
        page
    )

    if manual_reason:
        print(
            f"[MANUAL] Отправка отменена: "
            f"{manual_reason}"
        )

        set_status(
            application.id,
            "manual_required",
        )

        return "manual_required"

    print(
        "[STEP] Отправляю отклик..."
    )

    try:
        submit_button.click()

        page.wait_for_timeout(
            2200
        )

    except PlaywrightTimeoutError:
        print(
            "[ERROR] Timeout при отправке."
        )

        set_status(
            application.id,
            "apply_error",
        )

        return "apply_error"

    except Exception as exc:
        print(
            f"[ERROR] Не удалось отправить: "
            f"{exc}"
        )

        set_status(
            application.id,
            "apply_error",
        )

        return "apply_error"

    text = page_text(
        page
    )

    if contains_any(
        text,
        SUCCESS_MARKERS,
    ):
        print(
            "[SUCCESS] Отклик отправлен."
        )

        set_status(
            application.id,
            "applied",
            applied=True,
        )

        return "applied"

    # Если после клика интерфейс HH изменился
    # и мы не можем подтвердить результат,
    # не считаем отклик успешным наугад.
    print(
        "[MANUAL] Кнопка была нажата, "
        "но подтверждение успешного отклика "
        "не найдено."
    )

    set_status(
        application.id,
        "manual_required",
    )

    return "manual_required"


def load_queue():
    session = SessionLocal()

    try:
        rows = session.execute(
            select(
                Application,
                Vacancy,
            )
            .join(
                Vacancy,
                Vacancy.id
                == Application.vacancy_id,
            )
            .where(
                Application.status
                == "approved"
            )
            .order_by(
                Application.created_at.asc()
            )
            .limit(
                MAX_PER_RUN
            )
        ).all()

        # Отвязываем ORM-объекты от session,
        # чтобы спокойно использовать после close.
        result = []

        for application, vacancy in rows:
            session.expunge(
                application
            )
            session.expunge(
                vacancy
            )

            result.append(
                (
                    application,
                    vacancy,
                )
            )

        return result

    finally:
        session.close()


def main() -> None:
    queue = load_queue()

    print()
    print("=" * 80)

    print(
        "HH APPLY WORKER"
    )

    print("=" * 80)

    print(
        f"В очереди approved: "
        f"{len(queue)}"
    )

    print(
        f"Максимум за проход: "
        f"{MAX_PER_RUN}"
    )

    print(
        f"Headless: {HEADLESS}"
    )

    if not queue:
        print(
            "Отправлять нечего."
        )
        return

    stats = {
        "applied": 0,
        "manual_required": 0,
        "apply_error": 0,
    }

    with sync_playwright() as p:
        context = (
            p.chromium
            .launch_persistent_context(
                user_data_dir=str(
                    PROFILE_DIR
                ),
                headless=HEADLESS,
                viewport={
                    "width": 1440,
                    "height": 1000,
                },
            )
        )

        page = context.pages[0]

        for index, (
            application,
            vacancy,
        ) in enumerate(
            queue,
            start=1,
        ):
            application_output = StringIO()

            try:
                with redirect_stdout(
                    application_output
                ):
                    print()
                    print(
                        f"[{index}/{len(queue)}]"
                    )

                    result = process_application(
                        page=page,
                        vacancy=vacancy,
                        application=application,
                    )

            except Exception as exc:
                result = "apply_error"

                try:
                    set_status(
                        application.id,
                        "apply_error",
                    )
                except Exception:
                    pass

                with redirect_stdout(
                    application_output
                ):
                    print(
                        "[ERROR] Необработанная ошибка "
                        f"при отклике: {type(exc).__name__}: "
                        f"{exc}"
                    )

            append_application_log(
                result,
                application_output.getvalue(),
            )

            print(
                f"[{index}/{len(queue)}] "
                f"application_id={application.id} "
                f"result={result}"
            )

            if result in stats:
                stats[result] += 1

            time.sleep(
                DELAY_SECONDS
            )

        context.close()

    print()
    print("=" * 80)
    print("ГОТОВО")
    print("=" * 80)

    print(
        f"Отправлено: "
        f"{stats['applied']}"
    )

    print(
        f"Нужно вручную: "
        f"{stats['manual_required']}"
    )

    print(
        f"Ошибок: "
        f"{stats['apply_error']}"
    )


if __name__ == "__main__":
    main()
