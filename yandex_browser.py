from __future__ import annotations

from pathlib import Path

from playwright.sync_api import BrowserContext, Page


ROOT = Path(__file__).resolve().parent
PROFILE_DIR = ROOT / "yandex-browser-profile"
YANDEX_JOBS_URL = "https://yandex.ru/jobs/vacancies"
YANDEX_ACCOUNT_URL = "https://passport.yandex.ru/profile"


def get_page(context: BrowserContext) -> Page:
    return context.pages[0] if context.pages else context.new_page()


def page_text(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        return ""


def is_yandex_authenticated(page: Page, context: BrowserContext) -> bool:
    """Консервативная проверка авторизации Яндекса.

    Считаем сессию авторизованной только при наличии типичных auth-cookie и
    отсутствии явных признаков формы входа. Это надёжнее, чем проверять один
    конкретный DOM-селектор, который Яндекс может менять.
    """
    try:
        cookies = context.cookies("https://yandex.ru")
    except Exception:
        cookies = []

    names = {str(item.get("name") or "") for item in cookies}
    auth_cookie_names = {
        "Session_id",
        "sessionid2",
        "yandex_login",
        "L",
    }
    has_auth_cookie = bool(names & auth_cookie_names)

    url = (page.url or "").lower()
    text = page_text(page)

    logged_out_markers = (
        "passport.yandex.ru/auth",
        "войти в аккаунт",
        "войти или зарегистрироваться",
    )

    if any(marker in url or marker in text for marker in logged_out_markers):
        return False

    return has_auth_cookie


def auth_cookie_names(context: BrowserContext) -> list[str]:
    try:
        cookies = context.cookies("https://yandex.ru")
    except Exception:
        return []
    return sorted({str(item.get("name") or "") for item in cookies if item.get("name")})
