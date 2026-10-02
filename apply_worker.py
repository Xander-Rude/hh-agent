import os
import re
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
from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.orm import make_transient

from application_notifications import (
    notify_captcha_pause,
    notify_cover_letter_attention,
    notify_manual_required,
)
from hh_accounts import account_for_worker, account_label, account_resume_id
from hh_browser import hh_browser_context_options
from app.application_events import (
    record_application_event,
    record_outcome_event,
    update_career_status,
)

from app.db import (
    Application,
    Evaluation,
    SessionLocal,
    Vacancy,
)
from app.cover_letter_runtime import (
    build_legacy_vacancy_cover_letter,
    parse_strengths,
)
from app.canonical_cover_letter import get_or_generate_cover_letter
from app.clean_live_guard import clean_eligibility
from app.decision_snapshot import (
    ensure_decision_snapshot,
    get_decision_snapshot,
    refresh_pending_decision_snapshot_cover_letter,
)
from app.hh_apply_control import (
    captcha_reason_from_page,
    captcha_pause,
    is_captcha_paused,
    pause_for_captcha,
    record_old_apply_attempt,
    wait_for_old_slot,
)


load_dotenv()


ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"

ACTIVE_ACCOUNT = account_for_worker()
PROFILE_DIR = ACTIVE_ACCOUNT.profile_dir

SUCCESS_LOG = LOG_DIR / f"apply_worker_{ACTIVE_ACCOUNT.key}.log"
ATTENTION_LOG = LOG_DIR / f"apply_worker_attention_{ACTIVE_ACCOUNT.key}.log"

HEADLESS = (
    os.getenv(
        "HH_APPLY_HEADLESS",
        "false",
    ).lower()
    == "true"
)

MAX_PER_RUN = int(
    os.getenv(
        (
            "HH_OLD_APPLY_MAX_PER_RUN"
            if ACTIVE_ACCOUNT.key == "old"
            else "HH_APPLY_MAX_PER_RUN"
        ),
        "1" if ACTIVE_ACCOUNT.key == "old" else "10",
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
    'textarea[data-qa="vacancy-response-popup-form-letter-input"]',
    'textarea[data-qa*="vacancy-response-letter"]',
    'textarea[data-qa*="cover-letter"]',
    'textarea[name*="letter"]',
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
    "Сопроводительное письмо",
    "+ Сопроводительное письмо",
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
]


ALREADY_APPLIED_MARKERS = [
    "вы откликнулись",
    "вы уже откликнулись",
    "отклик отправлен",
    "резюме отправлено",
    "резюме доставлено",
]


SUCCESS_MARKERS = [
    "отклик отправлен",
    "вы откликнулись",
    "резюме отправлено",
    "резюме доставлено",
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
    emit_outcome: bool = True,
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
        application_account = (
            getattr(application, "account_key", None)
            or ACTIVE_ACCOUNT.key
        )
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
                    "account_key": application_account,
                    "cover_letter": (
                        (application.cover_letter or "").strip()
                        or None
                    ),
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

    if previous_status != status:
        record_application_event(
            application_id,
            f"technical_{status}",
            source="apply_worker",
            details={
                "previous_status": previous_status,
                "status": status,
                "application_sent": applied,
                "account_key": application_account,
            },
        )

        if emit_outcome and status == "applying":
            record_outcome_event(
                application_id,
                "apply_started",
                source="apply_worker",
                confidence="system_confirmed",
                details={
                    "previous_status": previous_status,
                    "account_key": application_account,
                },
            )
        elif emit_outcome and status == "manual_required":
            record_outcome_event(
                application_id,
                "manual_required",
                source="apply_worker",
                confidence="system_confirmed",
                details={
                    "previous_status": previous_status,
                    "application_sent": applied,
                    "reason": manual_reason,
                    "account_key": application_account,
                },
            )

    if applied:
        update_career_status(
            application_id,
            "submitted",
            source="apply_worker",
            details={
                "technical_status": status,
                "account_key": application_account,
            },
            confidence="system_confirmed",
            emit_event=emit_outcome,
        )

    if notification is not None:
        notify_manual_required(
            **notification
        )


def set_cover_letter_status(
    application_id: int,
    status: str,
    *,
    error: str | None = None,
    notify: bool = False,
) -> None:
    """Track cover-letter delivery independently from the resume response."""
    session = SessionLocal()
    notification = None
    previous_status = None
    application_account = ACTIVE_ACCOUNT.key

    try:
        application = session.get(Application, application_id)
        if application is None:
            return

        previous_status = (
            getattr(application, "cover_letter_status", None)
            or "unknown"
        )
        application_account = (
            getattr(application, "account_key", None)
            or ACTIVE_ACCOUNT.key
        )
        application.cover_letter_status = status
        application.cover_letter_last_error = error
        application.cover_letter_checked_at = datetime.utcnow()

        if (
            notify
            and status == "needs_manual"
            and previous_status != "needs_manual"
        ):
            vacancy = session.get(Vacancy, application.vacancy_id)
            if vacancy is not None:
                notification = {
                    "vacancy_title": vacancy.title,
                    "company": vacancy.company,
                    "vacancy_url": vacancy.url,
                    "application_id": application.id,
                    "account_key": application_account,
                    "reason": error or "HH не подтвердил доставку письма.",
                }

        session.commit()
    finally:
        session.close()

    if previous_status != status or error:
        record_application_event(
            application_id,
            f"cover_letter_{status}",
            source="apply_worker",
            details={
                "previous_status": previous_status,
                "status": status,
                "error": error,
                "account_key": application_account,
            },
        )

    if notification is not None:
        notify_cover_letter_attention(**notification)


def enforce_application_cover_letter_policy(
    application: Application,
) -> str:
    """Bind the exact pre-send letter to this vacancy before HH interaction."""
    current = (application.cover_letter or "").strip()

    vacancy_id = getattr(application, "vacancy_id", None)
    if vacancy_id is None:
        # Some isolated unit tests use lightweight application doubles without
        # a DB identity. Production Application rows always have vacancy_id.
        return current

    session = SessionLocal()
    try:
        stored = session.get(Application, application.id)
        vacancy = session.get(Vacancy, vacancy_id)

        application_account = (
            getattr(stored or application, "account_key", None)
            or ACTIVE_ACCOUNT.key
            or "old"
        )

        snapshot = get_decision_snapshot(
            session,
            application.id,
        )
        if snapshot is not None:
            if stored is not None and vacancy is not None:
                try:
                    snapshot = refresh_pending_decision_snapshot_cover_letter(
                        session,
                        application=stored,
                        vacancy=vacancy,
                    ) or snapshot
                except Exception as exc:
                    if application_account != "old":
                        raise
                    stored.cover_letter = None
                    application.cover_letter = None
                    session.commit()
                    print(
                        "[COVER POLICY] Refusing OLD legacy snapshot fallback: "
                        f"application={application.id} "
                        f"error={type(exc).__name__}: {exc}",
                        flush=True,
                    )
                    return ""

            approved = (
                snapshot.cover_letter_final
                or ""
            ).strip()
            if approved != current:
                if stored is not None:
                    stored.cover_letter = approved or None
                application.cover_letter = approved or None
                session.commit()
                print(
                    "[COVER POLICY] Replaced stale pre-send letter with "
                    "vacancy-bound snapshot: "
                    f"application={application.id}",
                    flush=True,
                )
            return approved

        if (
            application_account == "old"
            and stored is not None
            and vacancy is not None
        ):
            try:
                canonical, _ = get_or_generate_cover_letter(
                    session,
                    vacancy=vacancy,
                    account_key="old",
                    assessment=None,
                )
                snapshot = ensure_decision_snapshot(
                    session,
                    application=stored,
                    vacancy=vacancy,
                    approved_cover_letter_override=canonical,
                )
                approved = (snapshot.cover_letter_final or "").strip()
            except Exception as exc:
                stored.cover_letter = None
                application.cover_letter = None
                session.commit()
                print(
                    "[COVER POLICY] Refusing OLD legacy cover-letter fallback: "
                    f"application={application.id} error={type(exc).__name__}: {exc}",
                    flush=True,
                )
                return ""

            stored.cover_letter = approved or None
            application.cover_letter = approved or None
            session.commit()
            print(
                "[COVER POLICY] Recovered canonical OLD cover letter before apply: "
                f"application={application.id}",
                flush=True,
            )
            return approved

        evaluation = session.scalars(
            select(Evaluation)
            .where(Evaluation.vacancy_id == vacancy_id)
            .where(~Evaluation.model.startswith("hard-filter/"))
            .order_by(Evaluation.created_at.desc(), Evaluation.id.desc())
            .limit(1)
        ).first()

        if vacancy is None or (evaluation is None and not current):
            return current

        strengths = (
            parse_strengths(evaluation.strengths)
            if evaluation is not None
            else []
        )
        source_text = (
            evaluation.cover_letter
            if evaluation is not None
            else current
        )
        safe = build_legacy_vacancy_cover_letter(
            vacancy_title=vacancy.title,
            vacancy_company=vacancy.company,
            vacancy_description=vacancy.description or "",
            stored_text=source_text,
            strengths=strengths,
        )

        if safe != current:
            if stored is not None:
                stored.cover_letter = safe
                session.commit()

            application.cover_letter = safe
            print(
                "[COVER POLICY] Persisted cover letter was rebound "
                f"to vacancy before apply: application={application.id}",
                flush=True,
            )

        return safe
    finally:
        session.close()


def detect_manual_required(
    page: Page,
) -> str | None:
    # HH response modals are not always represented in body.inner_text().
    # Detect employer screening structurally before relying on page text so
    # task answer fields can never be mistaken for a cover-letter textarea.
    screening_selectors = [
        'input[name="testRequired"][value="true"]',
        'textarea[name^="task_"]',
        'input[name^="task_"]',
        'select[name^="task_"]',
    ]
    for selector in screening_selectors:
        try:
            if page.locator(selector).count() > 0:
                return "вопросы работодателя"
        except Exception:
            continue

    text = page_text(page)

    for marker in MANUAL_MARKERS:
        if marker in text:
            return marker

    return None


def pause_application_for_captcha(
    application: Application,
    reason: str,
    *,
    submit_may_have_happened: bool = False,
    application_already_sent: bool = False,
) -> str:
    pause_for_captcha(
        ACTIVE_ACCOUNT.key,
        reason,
        application_id=application.id,
    )
    notify_captcha_pause(
        account_key=ACTIVE_ACCOUNT.key,
        reason=reason,
        application_id=application.id,
    )

    if application_already_sent:
        set_status(
            application.id,
            "applied",
            applied=True,
        )
        if (application.cover_letter or "").strip():
            set_cover_letter_status(
                application.id,
                "needs_manual",
                error=(
                    "HH показал CAPTCHA во время проверки/прикрепления "
                    "сопроводительного. Повторный submit не выполнялся."
                ),
                notify=False,
            )
    elif submit_may_have_happened:
        set_status(
            application.id,
            "manual_required",
            manual_reason=(
                "HH показал CAPTCHA после действия, которое могло отправить "
                "отклик. Автоматический retry запрещён; проверь отклик вручную."
            ),
        )
    else:
        # No submit has happened yet. Keep the row retryable after the operator
        # clears the account-wide captcha pause.
        set_status(
            application.id,
            "approved",
            emit_outcome=False,
        )

    print(
        "[CAPTCHA PAUSE] "
        f"account={ACTIVE_ACCOUNT.key} application={application.id} "
        f"reason={reason}"
    )
    return "captcha_paused"


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


def wait_for_post_apply_transition(
    page: Page,
    *,
    attempts: int = 10,
    delay_ms: int = 400,
) -> bool:
    """Wait briefly for HH to reveal that the first response click already sent.

    HH can render the post-apply state asynchronously. A single immediate
    already_applied() check is not enough: if the UI is still transitioning,
    the worker may mistake the page for a regular pre-submit form and fail on
    the missing cover-letter textarea.

    Return True only when there is concrete post-apply evidence. Return False
    immediately when a normal cover-letter form or employer questionnaire is
    visible, so the regular pre-submit flow is not delayed unnecessarily.
    """
    for _ in range(max(1, attempts)):
        if already_applied(page):
            return True

        if find_post_apply_cover_letter_trigger(page) is not None:
            return True

        if find_visible(page, COVER_LETTER_SELECTORS) is not None:
            return False

        if detect_manual_required(page):
            return False

        page.wait_for_timeout(delay_ms)

    return False


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


def _multipart_boundary(content_type: str) -> bytes | None:
    match = re.search(
        r'boundary=(?:"([^"]+)"|([^;]+))',
        content_type or "",
        re.IGNORECASE,
    )
    if not match:
        return None
    value = (match.group(1) or match.group(2) or "").strip()
    return value.encode("utf-8") if value else None


def _multipart_field_value(
    body: bytes,
    boundary: bytes,
    name: str,
) -> bytes | None:
    marker = (
        b'Content-Disposition: form-data; name="'
        + name.encode("utf-8")
        + b'"'
    )
    start = body.find(marker)
    if start < 0:
        return None
    header_end = body.find(b"\r\n\r\n", start)
    if header_end < 0:
        return None
    value_start = header_end + 4
    value_end = body.find(b"\r\n--" + boundary, value_start)
    if value_end < 0:
        return None
    return body[value_start:value_end]


def _multipart_upsert_text_field(
    body: bytes,
    content_type: str,
    name: str,
    value: str,
) -> bytes:
    boundary = _multipart_boundary(content_type)
    if not boundary:
        raise ValueError("multipart boundary not found")

    encoded = value.encode("utf-8")
    marker = (
        b'Content-Disposition: form-data; name="'
        + name.encode("utf-8")
        + b'"'
    )
    start = body.find(marker)

    if start >= 0:
        header_end = body.find(b"\r\n\r\n", start)
        if header_end < 0:
            raise ValueError("multipart field header is malformed")
        value_start = header_end + 4
        value_end = body.find(b"\r\n--" + boundary, value_start)
        if value_end < 0:
            raise ValueError("multipart field terminator not found")
        return body[:value_start] + encoded + body[value_end:]

    closing = b"--" + boundary + b"--"
    closing_at = body.rfind(closing)
    if closing_at < 0:
        raise ValueError("multipart closing boundary not found")

    prefix = body[:closing_at]
    if not prefix.endswith(b"\r\n"):
        prefix += b"\r\n"

    part = (
        b"--"
        + boundary
        + b"\r\n"
        + b'Content-Disposition: form-data; name="'
        + name.encode("utf-8")
        + b'"\r\n\r\n'
        + encoded
        + b"\r\n"
    )
    return prefix + part + body[closing_at:]


def _normalize_cover_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _multipart_contains_exact_cover_letter(
    body: bytes,
    content_type: str,
    cover_letter: str,
) -> bool:
    boundary = _multipart_boundary(content_type)
    if not boundary:
        return False

    expected = _normalize_cover_text(cover_letter)
    for name in ("letter", "text"):
        raw = _multipart_field_value(body, boundary, name)
        if raw is None:
            continue
        try:
            actual = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if _normalize_cover_text(actual) == expected:
            return True

    # Keep a conservative fallback for HH variants whose textarea field name
    # changes while the endpoint and multipart format stay the same.
    try:
        decoded = body.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return expected in _normalize_cover_text(decoded)


def _guard_hh_response_post(
    page: Page,
    cover_letter: str,
    action,
    *,
    mutate_letter: bool = True,
) -> tuple[object, dict]:
    """Guard HH popup response POSTs without corrupting native form submits.

    Instant apply can omit the letter entirely, so that first-click path still
    receives the prepared letter by mutating the multipart payload. Once HH has
    opened its normal two-step response form, the browser already serializes the
    filled textarea correctly. For that final submit we only verify the exact
    letter and let the native request body pass through unchanged.
    """
    state = {
        "post_seen": False,
        "letter_verified": False,
        "letter_injected": False,
        "blocked": False,
        "error": None,
    }
    pattern = "**/applicant/vacancy_response/popup*"

    def handler(route):
        request = route.request
        if request.method.upper() != "POST":
            route.continue_()
            return

        state["post_seen"] = True
        try:
            content_type = request.headers.get("content-type", "")
            body = request.post_data_buffer or b""

            if not mutate_letter:
                if not _multipart_contains_exact_cover_letter(
                    body,
                    content_type,
                    cover_letter,
                ):
                    raise ValueError(
                        "native HH response body does not contain prepared letter"
                    )
                state["letter_verified"] = True
                route.continue_()
                return

            guarded = _multipart_upsert_text_field(
                body,
                content_type,
                "letter",
                cover_letter,
            )
            boundary = _multipart_boundary(content_type)
            actual = (
                _multipart_field_value(guarded, boundary, "letter")
                if boundary
                else None
            )
            if (
                actual is None
                or _normalize_cover_text(actual.decode("utf-8"))
                != _normalize_cover_text(cover_letter)
            ):
                raise ValueError("guarded HH response body has wrong letter")
            state["letter_verified"] = True
            state["letter_injected"] = guarded != body
            route.continue_(post_data=guarded)
        except Exception as exc:
            state["blocked"] = True
            state["error"] = f"{type(exc).__name__}: {exc}"
            route.abort("blockedbyclient")

    page.route(pattern, handler)
    try:
        result = action()
        page.wait_for_timeout(250)
        return result, state
    finally:
        try:
            page.unroute(pattern, handler)
        except Exception:
            pass


def click_initial_apply_with_letter(
    page: Page,
    cover_letter: str,
) -> tuple[bool, dict]:
    return _guard_hh_response_post(
        page,
        cover_letter,
        lambda: click_initial_apply(page),
    )


def click_final_submit_with_letter(
    page: Page,
    submit_button,
    cover_letter: str,
) -> tuple[dict, dict]:
    def action():
        submit_button.click()
        return {"clicked": True}

    return _guard_hh_response_post(
        page,
        cover_letter,
        action,
        mutate_letter=False,
    )


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
        message = (
            "HH подтвердил отклик, но сопроводительное письмо не подтверждено: "
            + reason
        )
        print("[LETTER ATTENTION] " + message)
        set_status(application.id, "applied", applied=True)
        set_cover_letter_status(
            application.id,
            "needs_manual",
            error=reason,
            notify=True,
        )
        return "applied"

    try:
        captcha_reason = captcha_reason_from_page(page)
        if captcha_reason:
            return pause_application_for_captcha(
                application,
                captcha_reason,
                application_already_sent=True,
            )

        cover_letter = (application.cover_letter or "").strip()
        if not cover_letter:
            set_status(application.id, "applied", applied=True)
            set_cover_letter_status(application.id, "not_required")
            return "applied"

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
        try:
            submit.click(timeout=5000)
        except PlaywrightTimeoutError:
            print("[WARN] Timeout прикрепления; проверяю результат без повторной отправки.")

        for _ in range(12):
            if letter_delivery_confirmed(page, cover_letter, before_text):
                print("[SUCCESS] Отклик и отдельное сопроводительное подтверждены HH.")
                set_cover_letter_status(application.id, "confirmed")
                set_status(application.id, "applied", applied=True)
                return "applied"
            page.wait_for_timeout(500)

        return incomplete("HH не показал подтверждение прикрепления.")
    except Exception as exc:
        return incomplete(f"ошибка прикрепления ({type(exc).__name__}).")



def finalize_existing_application(
    page: Page,
    application: Application,
    *,
    preexisting: bool = True,
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

    if preexisting:
        record_outcome_event(
            application.id,
            "already_applied",
            source="hh",
            confidence="platform_observed",
            details={
                "detected_before_submit": True,
                "account_key": getattr(
                    application,
                    "account_key",
                    ACTIVE_ACCOUNT.key,
                ),
            },
            raw_ref=(
                f"hh-vacancy:{getattr(application, 'vacancy_id', '')}"
                if getattr(application, "vacancy_id", None) is not None
                else None
            ),
        )

    if not preexisting:
        if cover_letter:
            # This path follows a form submit that already contained the
            # prepared letter and HH confirmed the response on reload.
            set_cover_letter_status(application.id, "confirmed")
        else:
            set_cover_letter_status(application.id, "not_required")

    set_status(
        application.id,
        "applied",
        applied=True,
        emit_outcome=not preexisting,
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

    captcha_reason = captcha_reason_from_page(page)
    if captcha_reason:
        return pause_application_for_captcha(
            application,
            captcha_reason,
            submit_may_have_happened=True,
        )

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
        preexisting=False,
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

    application_account = getattr(application, "account_key", None) or "old"
    if application_account != ACTIVE_ACCOUNT.key:
        print(
            "[BLOCK] Application account mismatch: "
            f"application={application.id} "
            f"card={application_account} "
            f"worker={ACTIVE_ACCOUNT.key}"
        )
        return "account_mismatch"

    expected_resume_id = account_resume_id(ACTIVE_ACCOUNT)
    application_resume_id = (
        getattr(application, "selected_resume_id", None)
        or ""
    ).strip()
    if (
        expected_resume_id
        and application_resume_id
        and application_resume_id != expected_resume_id
    ):
        print(
            "[BLOCK] Application resume mismatch: "
            f"application={application.id} "
            f"card_resume={application_resume_id} "
            f"worker_resume={expected_resume_id} "
            f"account={ACTIVE_ACCOUNT.key}"
        )
        return "resume_mismatch"

    if application_account == "clean":
        session = SessionLocal()
        try:
            eligibility = clean_eligibility(
                session,
                vacancy.id,
            )
        finally:
            session.close()

        if not eligibility.eligible:
            print(
                "[BLOCK] CLEAN apply-time guard: "
                f"application={application.id} "
                f"vacancy={vacancy.id} "
                f"reason={eligibility.reason}"
            )
            set_status(
                application.id,
                "clean_guard_blocked",
                emit_outcome=False,
            )
            return "clean_guard_blocked"

    set_status(
        application.id,
        "applying",
    )

    approved_cover_letter = enforce_application_cover_letter_policy(
        application
    )
    if application_account == "old" and not approved_cover_letter:
        reason = (
            "Не удалось подготовить canonical сопроводительное письмо для OLD; "
            "legacy fallback заблокирован."
        )
        print(
            "[BLOCK] " + reason + f" application={application.id}",
            flush=True,
        )
        set_status(
            application.id,
            "manual_required",
            manual_reason=reason,
        )
        return "manual_required"

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

    captcha_reason = captcha_reason_from_page(page)
    if captcha_reason:
        return pause_application_for_captcha(
            application,
            captcha_reason,
        )

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

    cover_letter = (
        application.cover_letter
        or ""
    ).strip()

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
            clicked, response_guard = click_initial_apply_with_letter(
                page,
                cover_letter,
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

        captcha_reason = captcha_reason_from_page(page)
        if captcha_reason:
            return pause_application_for_captcha(
                application,
                captcha_reason,
                submit_may_have_happened=True,
            )

        # HH may switch to the post-apply UI a little after the click.
        # Wait for concrete sent-state evidence before treating the page as a
        # regular pre-submit form; otherwise delayed instant-apply responses
        # fall through to fill_cover_letter() and become false manual_required.
        if wait_for_post_apply_transition(page):
            if (
                response_guard.get("post_seen")
                and response_guard.get("letter_verified")
                and not response_guard.get("blocked")
            ):
                print(
                    "[SUCCESS] HH instant apply отправлен одним запросом "
                    "с подготовленным сопроводительным письмом."
                )
                set_cover_letter_status(
                    application.id,
                    "confirmed",
                )
                set_status(
                    application.id,
                    "applied",
                    applied=True,
                )
                return "applied"

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

    # Последняя страховка: CAPTCHA pauses the whole HH account, while
    # employer questionnaires remain application-local manual work.
    captcha_reason = captcha_reason_from_page(page)
    if captcha_reason:
        return pause_application_for_captcha(
            application,
            captcha_reason,
        )

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
        _, response_guard = click_final_submit_with_letter(
            page,
            submit_button,
            cover_letter,
        )

        print(
            "[DEBUG] HH final submit guard: "
            f"post_seen={response_guard.get('post_seen')} "
            f"letter_verified={response_guard.get('letter_verified')} "
            f"payload_mutated={response_guard.get('letter_injected')}"
        )

        if response_guard.get("blocked"):
            raise RuntimeError(
                "HH response payload guard blocked submit: "
                + str(response_guard.get("error") or "unknown error")
            )

        page.wait_for_timeout(
            2200
        )

        captcha_reason = captcha_reason_from_page(page)
        if captcha_reason:
            return pause_application_for_captcha(
                application,
                captcha_reason,
                submit_may_have_happened=True,
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

            set_cover_letter_status(
                application.id,
                "confirmed",
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


def _materialize_transient(instance):
    """Load all scalar columns, then remove any dependency on the DB session."""
    state = sa_inspect(instance)
    for attribute in state.mapper.column_attrs:
        getattr(instance, attribute.key)
    make_transient(instance)
    return instance


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
            .where(
                Application.account_key
                == ACTIVE_ACCOUNT.key
            )
            .order_by(
                Application.created_at.asc()
            )
            .limit(
                MAX_PER_RUN
            )
        ).all()

        # Worker keeps these rows after this session closes. Fully materialize
        # scalar columns and make the instances transient so SQLAlchemy can never
        # attempt a lazy refresh against a closed session mid-apply.
        result = []

        for application, vacancy in rows:
            _materialize_transient(application)
            _materialize_transient(vacancy)

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
    if ACTIVE_ACCOUNT.key == "old" and is_captcha_paused("old"):
        pause = captcha_pause("old") or {}
        print(
            "[CAPTCHA PAUSE] OLD apply worker stopped before browser launch: "
            f"{pause.get('reason') or 'captcha'}"
        )
        return

    queue = load_queue()

    print()
    print("=" * 80)

    print(
        f"HH APPLY WORKER {account_label(ACTIVE_ACCOUNT.key)}"
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
    print(
        f"HH profile: {PROFILE_DIR}"
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
        "clean_guard_blocked": 0,
    }

    with sync_playwright() as p:
        context = (
            p.chromium
            .launch_persistent_context(
                user_data_dir=str(
                    PROFILE_DIR
                ),
                **hh_browser_context_options(headless=HEADLESS),
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
            if ACTIVE_ACCOUNT.key == "old":
                allowed, rate_reason = wait_for_old_slot(
                    max_wait_seconds=0,
                )
                if not allowed:
                    print(
                        "[OLD RATE] Worker stops this run: "
                        f"{rate_reason}"
                    )
                    break
                rate_state = record_old_apply_attempt()
                print(
                    "[OLD RATE] Slot acquired: "
                    f"application_id={application.id} "
                    f"next_allowed_at={rate_state.get('next_allowed_at')}"
                )

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

            if (
                ACTIVE_ACCOUNT.key == "old"
                and result not in {
                    "applied",
                    "manual_required",
                    "captcha_paused",
                }
            ):
                original_result = result
                set_status(
                    application.id,
                    "manual_required",
                    manual_reason=(
                        "Автоматический OLD-отклик не завершён "
                        f"(result={original_result}). Автоматически повторять "
                        "его не буду; заверши отклик вручную."
                    ),
                )
                result = "manual_required"

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

            if result == "captcha_paused":
                print(
                    "[CAPTCHA PAUSE] Stop remaining queue until operator resumes."
                )
                break

            if ACTIVE_ACCOUNT.key != "old":
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
        f"Заблокировано CLEAN guard: "
        f"{stats['clean_guard_blocked']}"
    )

    print(
        f"Ошибок: "
        f"{stats['apply_error']}"
    )


if __name__ == "__main__":
    main()
