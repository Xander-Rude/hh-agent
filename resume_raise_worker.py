from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

load_dotenv()

ROOT = Path(__file__).resolve().parent
PROFILE_DIR = ROOT / "browser-profile"

HEADLESS = os.getenv("HH_RESUME_RAISE_HEADLESS", "true").lower() == "true"
RESUMES_URL = "https://hh.ru/applicant/resumes"
NAV_TIMEOUT_MS = int(os.getenv("HH_RESUME_RAISE_NAV_TIMEOUT_MS", "30000"))

RAISE_TEXTS = (
    "Поднять в поиске",
    "Поднять резюме в поиске",
)

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


def visible_modal(page: Page):
    """Возвращает видимую HH-модалку/диалог после клика, если она появилась."""
    selectors = (
        "[role='dialog']",
        "[data-qa*='modal']:not([data-qa='modal-overlay'])",
    )

    for selector in selectors:
        loc = page.locator(selector)
        try:
            count = loc.count()
        except Exception:
            continue

        for i in range(count):
            item = loc.nth(i)
            try:
                if item.is_visible():
                    return item
            except Exception:
                continue

    return None


def modal_overlay_visible(page: Page) -> bool:
    overlay = page.locator("[data-qa='modal-overlay']")
    try:
        return any(overlay.nth(i).is_visible() for i in range(overlay.count()))
    except Exception:
        return False


def close_modal_safely(page: Page, modal=None) -> None:
    """Закрывает информационную/upsell-модалку, не нажимая потенциально платные CTA."""
    roots = [modal] if modal is not None else []
    roots.append(page)

    safe_names = re.compile(r"^(закрыть|понятно|готово|ок|отмена)$", re.IGNORECASE)

    for root in roots:
        try:
            btns = root.get_by_role("button", name=safe_names)
            for i in range(btns.count()):
                btn = btns.nth(i)
                if btn.is_visible() and btn.is_enabled():
                    btn.click(timeout=2000)
                    page.wait_for_timeout(500)
                    return
        except Exception:
            pass

        # Частый вариант HH: крестик без текста, но с aria-label/title.
        try:
            closeish = root.locator(
                "button[aria-label*='Закры'], button[title*='Закры'], "
                "[data-qa*='close'], [data-qa*='modal-close']"
            )
            for i in range(closeish.count()):
                btn = closeish.nth(i)
                if btn.is_visible():
                    btn.click(timeout=2000)
                    page.wait_for_timeout(500)
                    return
        except Exception:
            pass

    # Escape безопаснее, чем кликать неизвестную CTA.
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    except Exception:
        pass


def handle_post_click_modal(page: Page) -> tuple[bool, str]:
    """
    Обрабатывает модалку HH после нажатия «Поднять в поиске».

    Возвращает (confirmation_clicked, description).
    Не нажимает никакие CTA, если в модалке есть признаки платной услуги.
    """
    # Даём HH короткое время показать overlay/dialog.
    for _ in range(10):
        if modal_overlay_visible(page) or visible_modal(page) is not None:
            break
        page.wait_for_timeout(200)
    else:
        return False, "no-modal"

    modal = visible_modal(page)

    try:
        text = (modal.inner_text(timeout=2000) if modal is not None else page_text(page)) or ""
    except Exception:
        text = ""

    normalized = " ".join(text.split())
    print(f"[INFO] После клика HH открыл модалку: {normalized[:400]!r}")

    low = normalized.lower()
    paid = any(marker in low for marker in PAID_MODAL_MARKERS)

    # Если это бесплатное подтверждение и внутри есть явная кнопка «Поднять ...»,
    # подтверждаем. При любых признаках оплаты ничего такого не нажимаем.
    if not paid and modal is not None:
        try:
            confirm = modal.locator("button, a, [role='button'], [role='link']").filter(
                has_text=RAISE_RE
            )
            for i in range(confirm.count()):
                btn = confirm.nth(i)
                if _is_clickable(btn):
                    label = _element_label(btn)
                    print(f"[INFO] Подтверждаю бесплатное поднятие в модалке: {label!r}")
                    btn.click(timeout=5000)
                    page.wait_for_timeout(1000)

                    # После подтверждения HH может ещё ненадолго оставить overlay.
                    for _ in range(20):
                        if not modal_overlay_visible(page):
                            break
                        page.wait_for_timeout(250)

                    if modal_overlay_visible(page):
                        close_modal_safely(page, visible_modal(page))

                    return True, "free-confirmation"
        except Exception as exc:
            print(f"[WARN] Не удалось нажать бесплатное подтверждение в модалке: {type(exc).__name__}: {exc}")

    if paid:
        print("[INFO] В модалке есть признаки платной услуги — платные кнопки не нажимаю.")

    close_modal_safely(page, modal)

    # Подождём исчезновения overlay, чтобы он не перехватывал следующий клик.
    for _ in range(20):
        if not modal_overlay_visible(page):
            break
        page.wait_for_timeout(250)

    return False, "paid-or-info-modal" if paid else "info-modal"


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


def looks_logged_out(page: Page) -> bool:
    url = (page.url or "").lower()
    if "account/login" in url or "/login" in url:
        return True

    text = page_text(page)
    if any(x in text for x in ("мои резюме", "резюме и профиль", "поднять в поиске")):
        return False

    return "войти" in text and "зарегистрироваться" in text


def _element_label(item) -> str:
    """Собирает видимый/доступный текст элемента без привязки к точной разметке HH."""
    parts = []

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


def _is_clickable(item) -> bool:
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
    """
    Ищет доступные кнопки поднятия устойчиво к изменениям DOM HH.

    Раньше поиск требовал одновременно:
      * role=button/link;
      * точное accessible-name;
      * точный inner_text.

    Это ломалось при любом вложенном span, переносе строки или дополнительном тексте.
    """
    result = []

    # 1. Основной путь: реальные кликабельные элементы с подходящим текстом.
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
            label = _element_label(item)
            if not RAISE_RE.search(label):
                continue
            if not _is_clickable(item):
                continue
            result.append(item)
        except Exception:
            continue

    if result:
        return result

    # 2. Fallback: HH может повесить click-handler на span/div без корректной role.
    # В таком случае ищем сам видимый текст. Клик по дочернему элементу всплывает
    # до обработчика родителя.
    fallback = page.get_by_text(RAISE_RE)

    try:
        count = fallback.count()
    except Exception:
        count = 0

    for index in range(count):
        item = fallback.nth(index)
        try:
            label = _element_label(item)
            if not RAISE_RE.search(label):
                continue
            if not _is_clickable(item):
                continue
            result.append(item)
        except Exception:
            continue

    return result


def wait_for_raise_buttons(page: Page, timeout_ms: int = 10000):
    """Даёт SPA HH время дорисовать карточки резюме и кнопки после DOMContentLoaded."""
    elapsed = 0
    step_ms = 500

    while elapsed <= timeout_ms:
        buttons = visible_raise_buttons(page)
        if buttons:
            return buttons

        page.wait_for_timeout(step_ms)
        elapsed += step_ms

    return []


def dump_raise_diagnostics(page: Page) -> None:
    """Пишет в лог минимум данных, нужных для следующей диагностики DOM HH."""
    try:
        print(f"[DEBUG] URL: {page.url}")
    except Exception:
        pass

    try:
        print(f"[DEBUG] TITLE: {page.title()}")
    except Exception:
        pass

    text = page_text(page)
    lines = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if "подня" in line and line not in lines:
            lines.append(line)

    for line in lines[:20]:
        print(f"[DEBUG] PAGE TEXT: {line[:300]}")

    try:
        candidates = page.locator("button, a, [role='button'], [role='link'], [data-qa]")
        count = min(candidates.count(), 500)
    except Exception:
        count = 0

    shown = 0
    for index in range(count):
        item = candidates.nth(index)
        try:
            label = _element_label(item)
            if "подня" not in label.lower():
                continue

            tag = item.evaluate("el => el.tagName.toLowerCase()")
            data_qa = item.get_attribute("data-qa")
            role = item.get_attribute("role")
            aria_disabled = item.get_attribute("aria-disabled")
            disabled = item.get_attribute("disabled")

            try:
                visible = item.is_visible()
            except Exception:
                visible = None

            try:
                enabled = item.is_enabled()
            except Exception:
                enabled = None

            print(
                "[DEBUG] CANDIDATE "
                f"tag={tag} role={role!r} data-qa={data_qa!r} "
                f"visible={visible} enabled={enabled} "
                f"aria-disabled={aria_disabled!r} disabled={disabled!r} "
                f"text={label[:250]!r}"
            )
            shown += 1
            if shown >= 20:
                break
        except Exception:
            continue


def available_time_hints(page: Page) -> list[str]:
    text = page_text(page)
    patterns = (
        r"(?:можно|сможете)\s+поднять[^.\n]{0,80}",
        r"поднять\s+можно[^.\n]{0,80}",
        r"следующее\s+поднятие[^.\n]{0,80}",
    )

    hints = []

    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            cleaned = " ".join(match.split())
            if cleaned and cleaned not in hints:
                hints.append(cleaned)

    return hints[:10]


def main() -> int:
    print("=" * 80)
    print("HH RESUME RAISE WORKER")
    print("=" * 80)
    print(f"Headless: {HEADLESS}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=HEADLESS,
            viewport={"width": 1440, "height": 1000},
        )

        try:
            page = context.pages[0]

            try:
                page.goto(
                    RESUMES_URL,
                    wait_until="domcontentloaded",
                    timeout=NAV_TIMEOUT_MS,
                )
            except PlaywrightTimeoutError:
                print(f"[ERROR] HH не загрузил страницу за {NAV_TIMEOUT_MS // 1000} сек.")
                return 2

            # DOMContentLoaded для hh.ru недостаточно: карточки резюме могут
            # дорисоваться клиентским JS позже.
            page.wait_for_timeout(1800)
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass

            block = detect_block(page)
            if block:
                print(f"[ERROR] HH показал антибот/блокировку: {block}")
                return 3

            if looks_logged_out(page):
                print("[ERROR] browser-profile не авторизован на hh.ru.")
                return 4

            buttons = wait_for_raise_buttons(page, timeout_ms=10000)

            if not buttons:
                print("[INFO] Бесплатная кнопка «Поднять в поиске» сейчас недоступна.")
                dump_raise_diagnostics(page)

                for hint in available_time_hints(page):
                    print(f"[INFO] {hint}")

                print("[INFO] Ничего не нажимаю. Следующая проверка будет по расписанию.")
                return 0

            print(f"[INFO] Доступных кнопок поднятия: {len(buttons)}")

            raised = 0

            while True:
                current = visible_raise_buttons(page)

                if not current:
                    break

                before_count = len(current)

                try:
                    current[0].click(timeout=5000)
                    page.wait_for_timeout(700)
                except Exception as exc:
                    print(
                        "[ERROR] Не удалось нажать бесплатную кнопку поднятия: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    return 5

                # HH может открыть модальное окно поверх списка. Если его не
                # обработать, overlay перехватывает следующий клик.
                handle_post_click_modal(page)

                block = detect_block(page)
                if block:
                    print(f"[ERROR] После клика HH показал антибот/блокировку: {block}")
                    return 6

                # Ждём, пока карточка обновится и доступных кнопок станет меньше.
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
                    print(
                        "[WARN] Клик выполнен, но число доступных кнопок не уменьшилось "
                        f"({before_count} -> {len(after)}). Не считаю это успешным поднятием."
                    )
                    dump_raise_diagnostics(page)
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
