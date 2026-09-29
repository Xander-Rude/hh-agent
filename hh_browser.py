from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import Page

from hh_accounts import active_apply_account, get_account


ROOT = Path(__file__).resolve().parent
PROFILE_DIR = active_apply_account().profile_dir
RESUMES_URL = "https://hh.ru/applicant/resumes"
DEFAULT_HEADLESS_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)


def hh_browser_context_options(*, headless: bool) -> dict:
    """Shared Chromium options for HH browser contexts.

    HH can return HTTP 403 to the default Playwright HeadlessChrome user
    agent even when the persisted applicant session is valid. Use the normal
    Chrome UA for headless HH traffic while preserving browser defaults for
    interactive/headful login flows.
    """
    options = {
        "headless": headless,
        "viewport": {"width": 1440, "height": 1000},
    }
    if headless:
        options["user_agent"] = (
            os.getenv("HH_HEADLESS_USER_AGENT")
            or DEFAULT_HEADLESS_USER_AGENT
        ).strip()
    return options


def profile_dir_for(account_key: str) -> Path:
    return get_account(account_key).profile_dir


def hh_cookie_names(page: Page) -> set[str]:
    try:
        cookies = page.context.cookies("https://hh.ru")
    except Exception:
        return set()

    return {
        str(cookie.get("name") or "").lower()
        for cookie in cookies
    }


def hh_is_authenticated(page: Page) -> bool:
    """Conservative check that the shared Playwright HH session is alive.

    Important: a stale ``hhtoken`` cookie is NOT enough. HH can keep that
    cookie while the server-side applicant session is no longer usable. We
    therefore require evidence from the rendered authenticated UI.
    """
    url = (page.url or "").lower()

    if (
        "account/login" in url
        or "/login" in url
        or "account/signup" in url
    ):
        return False

    try:
        if page.locator('[data-qa="resume"]').count() > 0:
            return True
    except Exception:
        pass

    try:
        text = page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        text = ""

    logged_out_markers = (
        "войти",
        "зарегистрироваться",
        "вход для соискателя",
    )
    if any(marker in text for marker in logged_out_markers):
        return False

    logged_in_markers = (
        "мои резюме",
        "резюме и профиль",
        "статус поиска",
        "создать резюме",
        "поднять в поиске",
    )
    if any(marker in text for marker in logged_in_markers):
        return True

    # Cookies are useful diagnostics only. A stale hhtoken must never turn an
    # otherwise unknown page (for example TITLE=HeadHunter with a login form)
    # into an authenticated state.
    return False
