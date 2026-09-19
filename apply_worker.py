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
    Evaluation,
    SessionLocal,
    Vacancy,
)
from app.cover_letter_runtime import (
    calibrate_stored_cover_letter,
    parse_strengths,
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


# После успешного отклика HH может не показать текстовый success-marker,
# но открыть отдельное действие для прикрепления письма.
# Этот data-qa относится именно к post-apply состоянию и не совпадает
# с контролами формы до отправки.
POST_APPLY_COVER_LETTER_TRIGGER_SELECTORS = [
    'button[data-qa="responded-success-attach-cover-letter"]',
    'a[data-qa="responded-success-attach-cover-letter"]',
    '[data-qa^="responded-success-"][data-qa*="letter"]',
]


# По возможности открываем именно форму отклика с письмом ДО отправки резюме.
# На части вакансий обычный первый клик отправляет отклик мгновенно, и письмо
# приходится прикреплять уже после факта. Этот путь стараемся не использовать.
PREAPPLY_COVER_LETTER_LINK_TEXTS = [
    "Написать сопроводительное",
    "Добавить сопроводительное",
    "Добавить сопроводительное письмо",
]

PREAPPLY_WITH_LETTER_TEXTS = [
    "С сопроводительным письмом",
    "Сопроводительное письмо",
]

PREAPPLY_DROPDOWN_SELECTORS = [
    '[data-qa="vacancy-response-link-top"] + button',
    '[data-qa="vacancy-response-link-bottom"] + button',
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
                    "application_sent": applied,
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

def enforce_application_cover_letter_policy(
    application: Application,
) -> str:
    """
    Re-check persisted letters before any HH interaction.

    Applications may have been created from older Evaluation rows generated
    before the current cover-letter calibration rules existed.
    """
    current = (application.cover_letter or "").strip()
    if not current:
        return ""

    vacancy_id = getattr(application, "vacancy_id", None)
    if vacancy_id is None:
        # Some isolated unit tests use lightweight application doubles without
        # a DB identity. Production Application rows always have vacancy_id.
        return current

    session = SessionLocal()
    try:
        evaluation = session.scalars(
            select(Evaluation)
            .where(Evaluation.vacancy_id == vacancy_id)
            .where(~Evaluation.model.startswith("hard-filter/"))
            .order_by(Evaluation.created_at.desc(), Evaluation.id.desc())
            .limit(1)
        ).first()

        strengths = (
            parse_strengths(evaluation.strengths)
            if evaluation is not None
            else []
        )
        safe = calibrate_stored_cover_letter(
            current,
            strengths,
        )

        if safe != current:
            stored = session.get(
                Application,
                application.id,
            )
            if stored is not None:
                stored.cover_letter = safe
                session.commit()

            application.cover_letter = safe
            print(
                "[COVER POLICY] Persisted cover letter was recalibrated "
                f"before apply: application={application.id}",
                flush=True,
            )

        return safe
    finally:
        session.close()


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
        "Приложить сопроводительное письмо",
        "Приложить сопроводительное",
        "Добавить сопроводительное к отклику",
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


def find_post_apply_cover_letter_trigger(
    page: Page,
):
    # Keep this detector intentionally strict. It is used as structural
    # evidence that HH has already accepted the response, so a generic
    # truthy mock/proxy must never be enough to enter post-apply mode.
    for selector in POST_APPLY_COVER_LETTER_TRIGGER_SELECTORS:
        try:
            locator = page.locator(selector)
            count = locator.count()
        except Exception:
            continue

        if not isinstance(count, int):
            continue

        for index in range(count):
            item = locator.nth(index)

            try:
                if item.is_visible():
                    return item
            except Exception:
                continue

    return None


def _strict_visible_by_role(
    page: Page,
    role: str,
    texts: list[str],
):
    for text in texts:
        try:
            locator = page.get_by_role(
                role,
                name=text,
                exact=False,
            )
            count = locator.count()
        except Exception:
            continue

        if not isinstance(count, int):
            continue

        for index in range(count):
            item = locator.nth(index)
            try:
                if item.is_visible():
                    return item
            except Exception:
                continue

    return None


def try_open_preapply_cover_letter(
    page: Page,
) -> bool:
    # 1. Прямая ссылка/кнопка "Написать сопроводительное".
    for role in ("link", "button"):
        trigger = _strict_visible_by_role(
            page,
            role,
            PREAPPLY_COVER_LETTER_LINK_TEXTS,
        )
        if trigger is None:
            continue

        try:
            trigger.click()
            page.wait_for_timeout(900)
        except Exception:
            continue

        field = find_visible(
            page,
            [
                'textarea[data-qa="vacancy-response-popup-form-letter-input"]',
                'textarea[data-qa*="vacancy-response-letter"]',
            ],
        )
        if field is not None:
            print(
                "[STEP] Открыта форма отклика с сопроводительным "
                "до отправки резюме."
            )
            return True

    # 2. На части страниц HH прячет вариант "С сопроводительным письмом"
    # в стрелке рядом с основной кнопкой отклика.
    dropdown = find_visible(
        page,
        PREAPPLY_DROPDOWN_SELECTORS,
    )
    if dropdown is not None:
        try:
            dropdown.click()
            page.wait_for_timeout(500)
        except Exception:
            dropdown = None

    if dropdown is not None:
        option = None
        for role in ("menuitem", "button", "link"):
            option = _strict_visible_by_role(
                page,
                role,
                PREAPPLY_WITH_LETTER_TEXTS,
            )
            if option is not None:
                break

        if option is None:
            for text in PREAPPLY_WITH_LETTER_TEXTS:
                try:
                    locator = page.get_by_text(
                        text,
                        exact=False,
                    )
                    count = locator.count()
                except Exception:
                    continue

                if not isinstance(count, int):
                    continue

                for index in range(count):
                    item = locator.nth(index)
                    try:
                        if item.is_visible():
                            option = item
                            break
                    except Exception:
                        continue
                if option is not None:
                    break

        if option is not None:
            try:
                option.click()
                page.wait_for_timeout(900)
            except Exception:
                option = None

        if option is not None:
            field = find_visible(
                page,
                [
                    'textarea[data-qa="vacancy-response-popup-form-letter-input"]',
                    'textarea[data-qa*="vacancy-response-letter"]',
                ],
            )
            if field is not None:
                print(
                    "[STEP] Выбран отклик «С сопроводительным письмом» "
                    "до отправки резюме."
                )
                return True

    return False


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


LETTER_SUCCESS_MARKERS = [
    "сопроводительное письмо отправлено",
    "сопроводительное письмо добавлено",
    "сопроводительное письмо приложено",
]


def find_letter_submit(field):
    # Restrict the search to the letter's nearest form/container. Never fall
    # back to the vacancy's «Откликнуться» button after the resume was sent.
    scope = field.locator("xpath=ancestor::*[.//button][1]")
    for name in ("Приложить сопроводительное письмо", "Приложить сопроводительное", "Приложить",
                 "Отправить письмо", "Добавить письмо", "Сохранить", "Отправить"):
        buttons = scope.get_by_role("button", name=name, exact=True)
        for index in range(buttons.count()):
            button = buttons.nth(index)
            if button.is_visible() and button.is_enabled():
                return button
    return None


def letter_delivery_confirmed(page, cover_letter, before_text):
    # The old response-success banner is deliberately not evidence for a letter.
    text = page_text(page)
    if any(marker in text and marker not in before_text
           for marker in LETTER_SUCCESS_MARKERS):
        return True
    for item in page.get_by_text(cover_letter, exact=True).all():
        if item.is_visible() and item.evaluate(
            "el => !el.closest('textarea, input, [contenteditable]')"
        ):
            return True
    return False


def attach_post_apply_cover_letter(page, application):
    print("[STEP] HH instant apply: отклик отправлен; прикладываю письмо отдельно.")

    def incomplete(reason):
        message = "HH подтвердил отклик, но сопроводительное письмо не подтверждено: " + reason
        print("[MANUAL] " + message)
        set_status(application.id, "manual_required", applied=True,
                   manual_reason=message)
        return "manual_required"

    try:
        cover_letter = (application.cover_letter or "").strip()
        if not cover_letter:
            return incomplete("текст отсутствует.")
        field = None
        for _ in range(10):
            reason = detect_manual_required(page)
            if reason:
                return incomplete(reason)
            field = ensure_cover_letter_field(page)
            if field is not None:
                break
            page.wait_for_timeout(500)
        if field is None:
            return incomplete("не найдено поле письма.")
        field.fill(cover_letter)
        if field.input_value(timeout=2000).strip() != cover_letter:
            return incomplete("текст в поле не совпадает с подготовленным письмом.")
        submit = find_letter_submit(field)
        if submit is None:
            return incomplete("не найдена кнопка прикрепления письма.")
        reason = detect_manual_required(page)
        if reason:
            return incomplete(reason)
        before_text = page_text(page)
        # A timeout can occur after the server accepted the letter. Verify once,
        # but never click again and risk sending a duplicate.
        try:
            submit.click(timeout=5000)
        except PlaywrightTimeoutError:
            print("[WARN] Timeout прикрепления; проверяю результат без повторной отправки.")
        for _ in range(12):
            if letter_delivery_confirmed(page, cover_letter, before_text):
                print("[SUCCESS] Отклик и отдельное сопроводительное подтверждены HH.")
                set_status(application.id, "applied", applied=True)
                return "applied"
            page.wait_for_timeout(500)
        return incomplete("HH не показал подтверждение прикрепления.")
    except Exception as exc:
        return incomplete(f"ошибка прикрепления ({type(exc).__name__}).")



def finalize_existing_application(
    page: Page,
    application: Application,
) -> str:
    """Finish an application that HH already shows as sent.

    If HH still exposes the dedicated post-apply cover-letter action, the
    resume was accepted without the prepared letter and we repair only that
    missing step. We never click the vacancy's primary apply button here.
    """
    cover_letter = (
        application.cover_letter
        or ""
    ).strip()

    if cover_letter:
        try:
            trigger = find_post_apply_cover_letter_trigger(
                page
            )
        except Exception:
            trigger = None

        if trigger is not None:
            print(
                "[INFO] HH подтверждает, что отклик уже отправлен, "
                "но сопроводительное ещё можно приложить; "
                "прикладываю письмо отдельно."
            )
            return attach_post_apply_cover_letter(
                page,
                application,
            )

    set_status(
        application.id,
        "applied",
        applied=True,
    )
    return "applied"


def recover_ambiguous_application(
    page: Page,
    vacancy: Vacancy,
    application: Application,
) -> str | None:
    """Re-check HH after an ambiguous submit without submitting again."""
    print(
        "[INFO] HH не показал подтверждение после submit; "
        "перепроверяю вакансию без повторной отправки."
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
            "[WARN] Не удалось перепроверить вакансию "
            f"после неоднозначной отправки: {type(exc).__name__}: {exc}"
        )
        return None

    if not already_applied(page):
        print(
            "[INFO] После повторной загрузки HH всё ещё "
            "не подтверждает существующий отклик."
        )
        return None

    print(
        "[INFO] После повторной загрузки HH подтверждает, "
        "что отклик уже существует."
    )
    return finalize_existing_application(
        page,
        application,
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

    enforce_application_cover_letter_policy(
        application
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
        return finalize_existing_application(
            page,
            application,
        )

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

    # Validate before the first click: HH can send the resume immediately.
    if not (application.cover_letter or "").strip():
        set_status(application.id, "manual_required",
                   manual_reason="Сопроводительное письмо отсутствует; отклик не отправлялся.")
        return "manual_required"

    preapply_letter_form = False

    try:
        preapply_letter_form = try_open_preapply_cover_letter(
            page
        )
    except Exception as exc:
        print(
            "[WARN] Не удалось открыть отдельную форму "
            f"с письмом до отклика: {type(exc).__name__}"
        )

    if not preapply_letter_form:
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

        # Instant apply sends the resume first; attaching the letter is a
        # separate operation with its own submit control and confirmation.
        if already_applied(page):
            return attach_post_apply_cover_letter(page, application)

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
            "[WARN] Timeout при отправке; "
            "перепроверяю результат без повторного submit."
        )

        recovered = recover_ambiguous_application(
            page,
            vacancy,
            application,
        )
        if recovered is not None:
            return recovered

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

    # Application 1626: HH реально принял отклик, но не показал ни один
    # известный текстовый success-marker. При этом отдельная post-apply кнопка
    # для сопроводительного уже означает, что резюме отправлено и письмо теперь
    # нужно прикреплять отдельной операцией.
    for _ in range(8):
        if find_post_apply_cover_letter_trigger(page) is not None:
            print(
                "[INFO] HH подтвердил отклик через post-apply UI; "
                "прикладываю сопроводительное отдельно."
            )
            return attach_post_apply_cover_letter(
                page,
                application,
            )

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

        page.wait_for_timeout(
            400
        )

    recovered = recover_ambiguous_application(
        page,
        vacancy,
        application,
    )
    if recovered is not None:
        return recovered

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
