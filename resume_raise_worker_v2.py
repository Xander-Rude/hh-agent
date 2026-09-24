from __future__ import annotations

import os
import re

from dotenv import load_dotenv
from playwright.sync_api import (
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from hh_accounts import account_for_worker, account_label, account_resume_id
from hh_browser import RESUMES_URL, hh_is_authenticated


load_dotenv()

ACTIVE_ACCOUNT = account_for_worker()
PROFILE_DIR = ACTIVE_ACCOUNT.profile_dir
EXPECTED_RESUME_ID = account_resume_id(ACTIVE_ACCOUNT)

HEADLESS = os.getenv("HH_RESUME_RAISE_HEADLESS", "true").lower() == "true"
NAV_TIMEOUT_MS = int(os.getenv("HH_RESUME_RAISE_NAV_TIMEOUT_MS", "30000"))
NAV_RETRIES = max(1, int(os.getenv("HH_RESUME_RAISE_NAV_RETRIES", "3")))
NAV_RETRY_DELAY_MS = max(0, int(os.getenv("HH_RESUME_RAISE_NAV_RETRY_DELAY_MS", "15000")))

RAISE_RE = re.compile(r"поднять(?:\s+резюме)?\s+в\s+поиске", re.IGNORECASE)
BLOCK_MARKERS = (
    "captcha",
    "подтвердите, что вы не робот",
    "проверка, что вы не робот",
    "доступ временно ограничен",
    "подозрительная активность",
)
PAID_MODAL_MARKERS = (
    "₽",
    "руб",
    "оплат",
    "купить",
    "стоимость",
    "платн",
    "автоподня",
    "продвижение резюме",
)
TRANSIENT_NAV_ERRORS = (
    "net::ERR_NAME_NOT_RESOLVED",
    "net::ERR_INTERNET_DISCONNECTED",
    "net::ERR_NETWORK_CHANGED",
    "net::ERR_CONNECTION_RESET",
    "net::ERR_CONNECTION_TIMED_OUT",
    "net::ERR_TIMED_OUT",
    "net::ERR_PROXY_CONNECTION_FAILED",
    "net::ERR_TUNNEL_CONNECTION_FAILED",
)


def page_text(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        return ""


def detect_block(page: Page) -> str | None:
    text = page_text(page)
    url = (page.url or "").lower()
    for marker in BLOCK_MARKERS:
        if marker in text or marker in url:
            return marker
    return None


def element_label(item) -> str:
    parts: list[str] = []
    for getter in (
        lambda: item.inner_text(timeout=1000),
        lambda: item.get_attribute("aria-label"),
        lambda: item.get_attribute("title"),
    ):
        try:
            value = getter()
        except Exception:
            value = None
        if value:
            parts.append(" ".join(value.split()))
    return " ".join(parts).strip()


def is_clickable(item) -> bool:
    try:
        if not item.is_visible():
            return False
    except Exception:
        return False

    try:
        if not item.is_enabled():
            return False
    except Exception:
        pass

    try:
        if (item.get_attribute("aria-disabled") or "").lower() == "true":
            return False
    except Exception:
        pass

    return True


def visible_raise_buttons(page: Page):
    result = []

    candidates = page.locator("button, a, [role='button'], [role='link']").filter(
        has_text=RAISE_RE
    )
    try:
        count = candidates.count()
    except Exception:
        count = 0

    for index in range(count):
        item = candidates.nth(index)
        try:
            label = element_label(item)
            if RAISE_RE.search(label) and is_clickable(item):
                result.append(item)
        except Exception:
            continue

    if result:
        return result

    fallback = page.get_by_text(RAISE_RE)
    try:
        count = fallback.count()
    except Exception:
        count = 0

    for index in range(count):
        item = fallback.nth(index)
        try:
            label = element_label(item)
            if RAISE_RE.search(label) and is_clickable(item):
                result.append(item)
        except Exception:
            continue

    return result


def wait_for_resume_ui(page: Page, timeout_ms: int = 15000) -> None:
    elapsed = 0
    step_ms = 500
    while elapsed <= timeout_ms:
        if not hh_is_authenticated(page):
            page.wait_for_timeout(step_ms)
            elapsed += step_ms
            continue

        try:
            if page.locator('[data-qa="resume"]').count() > 0:
                return
        except Exception:
            pass

        if "мои резюме" in page_text(page):
            return

        page.wait_for_timeout(step_ms)
        elapsed += step_ms


def goto_resumes_with_retry(page: Page) -> bool:
    for attempt in range(1, NAV_RETRIES + 1):
        try:
            page.goto(
                RESUMES_URL,
                wait_until="domcontentloaded",
                timeout=NAV_TIMEOUT_MS,
            )
            if attempt > 1:
                print(f"[INFO] HH открылся с попытки {attempt}/{NAV_RETRIES}.")
            return True
        except PlaywrightTimeoutError:
            reason = f"таймаут загрузки {NAV_TIMEOUT_MS // 1000} сек."
        except PlaywrightError as exc:
            message = str(exc)
            if not any(marker in message for marker in TRANSIENT_NAV_ERRORS):
                raise
            reason = message.splitlines()[0] if message else type(exc).__name__

        if attempt >= NAV_RETRIES:
            print(
                f"[ERROR] HH не открылся после {NAV_RETRIES} попыток. "
                f"Последняя ошибка: {reason}"
            )
            return False

        delay_sec = NAV_RETRY_DELAY_MS / 1000
        print(
            f"[WARN] Не удалось открыть HH ({reason}). "
            f"Повтор {attempt + 1}/{NAV_RETRIES} через {delay_sec:g} сек."
        )
        page.wait_for_timeout(NAV_RETRY_DELAY_MS)

    return False


def visible_modal(page: Page):
    for selector in (
        "[role='dialog']",
        "[data-qa*='modal']:not([data-qa='modal-overlay'])",
    ):
        loc = page.locator(selector)
        try:
            count = loc.count()
        except Exception:
            continue
        for index in range(count):
            item = loc.nth(index)
            try:
                if item.is_visible():
                    return item
            except Exception:
                continue
    return None


def close_modal_safely(page: Page, modal=None) -> None:
    roots = [modal] if modal is not None else []
    roots.append(page)
    safe_names = re.compile(
        r"^(закрыть|понятно|готово|ок|отмена|не сейчас|позже|пропустить)$",
        re.IGNORECASE,
    )

    for root in roots:
        try:
            buttons = root.get_by_role("button", name=safe_names)
            for index in range(buttons.count()):
                button = buttons.nth(index)
                if button.is_visible() and button.is_enabled():
                    button.click(timeout=2000)
                    page.wait_for_timeout(500)
                    return
        except Exception:
            pass

        try:
            closeish = root.locator(
                "button[aria-label*='Закры'], button[title*='Закры'], "
                "[data-qa*='close'], [data-qa*='modal-close']"
            )
            for index in range(closeish.count()):
                button = closeish.nth(index)
                if button.is_visible():
                    button.click(timeout=2000)
                    page.wait_for_timeout(500)
                    return
        except Exception:
            pass

    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    except Exception:
        pass


def visible_blocking_overlay(page: Page):
    loc = page.locator("[class*='magritte-overlay']")
    try:
        count = loc.count()
    except Exception:
        return None

    for index in range(count):
        item = loc.nth(index)
        try:
            if item.is_visible():
                return item
        except Exception:
            continue

    return None


def dismiss_blocking_overlay(page: Page) -> bool:
    """
    HH may show a profile/update prompt above an otherwise clickable
    "Поднять в поиске" button. Playwright then reports that a
    magritte-overlay intercepts pointer events.

    Only non-committal close/dismiss actions are used here. We deliberately
    do not force-click through the overlay and do not accept paid/primary
    actions blindly.
    """
    for attempt in range(1, 4):
        overlay = visible_blocking_overlay(page)
        if overlay is None:
            return True

        try:
            text = (overlay.inner_text(timeout=2000) or "").strip()
        except Exception:
            text = ""

        normalized = " ".join(text.split())
        print(
            f"[INFO] Перед поднятием HH перекрывает кнопку оверлеем "
            f"(попытка {attempt}/3): {normalized[:400]!r}"
        )

        safe_names = re.compile(
            r"^(закрыть|понятно|готово|ок|отмена|не сейчас|позже|пропустить)$",
            re.IGNORECASE,
        )

        dismissed = False

        try:
            buttons = overlay.get_by_role("button", name=safe_names)
            for index in range(buttons.count()):
                button = buttons.nth(index)
                if is_clickable(button):
                    label = element_label(button) or "safe-dismiss"
                    print(f"[INFO] Закрываю блокирующий оверлей кнопкой: {label!r}")
                    button.click(timeout=3000)
                    page.wait_for_timeout(500)
                    dismissed = True
                    break
        except Exception as exc:
            print(
                f"[WARN] Не удалось закрыть оверлей безопасной кнопкой: "
                f"{type(exc).__name__}: {exc}"
            )

        if not dismissed:
            try:
                closeish = overlay.locator(
                    "button[aria-label*='Закры'], button[title*='Закры'], "
                    "[data-qa*='close'], [data-qa*='modal-close']"
                )
                for index in range(closeish.count()):
                    button = closeish.nth(index)
                    if button.is_visible():
                        print("[INFO] Закрываю блокирующий оверлей через close-control.")
                        button.click(timeout=3000)
                        page.wait_for_timeout(500)
                        dismissed = True
                        break
            except Exception as exc:
                print(
                    f"[WARN] Не удалось закрыть оверлей через close-control: "
                    f"{type(exc).__name__}: {exc}"
                )

        if not dismissed:
            try:
                print("[INFO] Пробую закрыть блокирующий оверлей клавишей Escape.")
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
            except Exception as exc:
                print(
                    f"[WARN] Escape не закрыл оверлей: "
                    f"{type(exc).__name__}: {exc}"
                )

        if visible_blocking_overlay(page) is None:
            print("[INFO] Блокирующий оверлей закрыт.")
            return True

    overlay = visible_blocking_overlay(page)
    if overlay is not None:
        try:
            text = " ".join((overlay.inner_text(timeout=2000) or "").split())
        except Exception:
            text = ""
        print(
            "[ERROR] Не удалось безопасно закрыть блокирующий HH-оверлей. "
            f"Текст: {text[:400]!r}"
        )

    return False


def handle_post_click_modal(page: Page) -> None:
    page.wait_for_timeout(500)
    modal = visible_modal(page)
    if modal is None:
        return

    try:
        text = (modal.inner_text(timeout=2000) or "").strip()
    except Exception:
        text = ""

    normalized = " ".join(text.split())
    print(f"[INFO] После клика HH открыл модалку: {normalized[:400]!r}")

    low = normalized.lower()
    if any(marker in low for marker in PAID_MODAL_MARKERS):
        print("[INFO] В модалке есть признаки платной услуги — платные кнопки не нажимаю.")
        close_modal_safely(page, modal)
        return

    try:
        confirm = modal.locator("button, a, [role='button'], [role='link']").filter(
            has_text=RAISE_RE
        )
        for index in range(confirm.count()):
            button = confirm.nth(index)
            if is_clickable(button):
                print("[INFO] Подтверждаю бесплатное поднятие в модалке.")
                button.click(timeout=5000)
                page.wait_for_timeout(1000)
                return
    except Exception as exc:
        print(f"[WARN] Не удалось обработать бесплатное подтверждение: {type(exc).__name__}: {exc}")

    close_modal_safely(page, modal)


def dump_diagnostics(page: Page) -> None:
    print(f"[DEBUG] PROFILE: {PROFILE_DIR}")
    print(f"[DEBUG] URL: {page.url}")
    try:
        print(f"[DEBUG] TITLE: {page.title()}")
    except Exception:
        pass

    for raw in page_text(page).splitlines():
        line = " ".join(raw.split())
        if "подня" in line:
            print(f"[DEBUG] PAGE TEXT: {line[:300]}")


def main() -> int:
    print("=" * 80)
    print("HH RESUME RAISE WORKER V2")
    print("=" * 80)
    print(f"Headless: {HEADLESS}")
    print(f"Profile: {PROFILE_DIR}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=HEADLESS,
            viewport={"width": 1440, "height": 1000},
        )

        try:
            page = context.pages[0] if context.pages else context.new_page()

            if not goto_resumes_with_retry(page):
                return 2

            wait_for_resume_ui(page)

            block = detect_block(page)
            if block:
                print(f"[ERROR] HH показал антибот/блокировку: {block}")
                return 3

            if not hh_is_authenticated(page):
                print(
                    f"[ERROR] HH SESSION EXPIRED: "
                    f"{account_label(ACTIVE_ACCOUNT.key)} не авторизован на hh.ru."
                )
                print(
                    "[ACTION] Запусти: "
                    f".\\.venv\\Scripts\\python.exe .\\hh_login.py "
                    f"--account {ACTIVE_ACCOUNT.key}"
                )
                dump_diagnostics(page)
                return 4

            if EXPECTED_RESUME_ID:
                try:
                    identity_matches = (
                        page.locator(
                            f'a[href*="/resume/{EXPECTED_RESUME_ID}"]'
                        ).count()
                        > 0
                    )
                except Exception:
                    identity_matches = False

                if not identity_matches:
                    print(
                        "[ERROR] HH ACCOUNT MISMATCH: "
                        f"{account_label(ACTIVE_ACCOUNT.key)} expected "
                        f"resume_id={EXPECTED_RESUME_ID}."
                    )
                    dump_diagnostics(page)
                    return 8

            buttons = visible_raise_buttons(page)
            if not buttons:
                # SPA может дорисовать карточки/CTA позже.
                for _ in range(20):
                    page.wait_for_timeout(500)
                    buttons = visible_raise_buttons(page)
                    if buttons:
                        break

            if not buttons:
                print("[INFO] Бесплатная кнопка «Поднять в поиске» сейчас недоступна.")
                dump_diagnostics(page)
                return 0

            print(f"[INFO] Доступных кнопок поднятия: {len(buttons)}")
            raised = 0

            while True:
                current = visible_raise_buttons(page)
                if not current:
                    break

                if not dismiss_blocking_overlay(page):
                    dump_diagnostics(page)
                    return 7

                # После закрытия SPA-оверлея DOM мог перерисоваться,
                # поэтому заново получаем локаторы кнопок.
                current = visible_raise_buttons(page)
                if not current:
                    print("[WARN] После закрытия оверлея кнопка поднятия исчезла.")
                    break

                before_count = len(current)
                try:
                    current[0].click(timeout=5000)
                    page.wait_for_timeout(700)
                except Exception as exc:
                    print(f"[ERROR] Не удалось нажать кнопку поднятия: {type(exc).__name__}: {exc}")
                    return 5

                handle_post_click_modal(page)

                block = detect_block(page)
                if block:
                    print(f"[ERROR] После клика HH показал антибот/блокировку: {block}")
                    return 6

                after = visible_raise_buttons(page)
                for _ in range(12):
                    if len(after) < before_count:
                        break
                    page.wait_for_timeout(500)
                    after = visible_raise_buttons(page)

                if len(after) < before_count:
                    raised += 1
                    print(f"[SUCCESS] Поднятие выполнено. Счётчик: {raised}")
                else:
                    print("[WARN] Клик был, но карточка не подтвердила успешное поднятие.")
                    dump_diagnostics(page)
                    break

                if raised >= 10:
                    print("[WARN] Safety-limit 10 кликов; останавливаюсь.")
                    break

            print(f"[DONE] Поднятий выполнено: {raised}")
            return 0

        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())