from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import sync_playwright

from hh_browser import PROFILE_DIR, RESUMES_URL, hh_is_authenticated


@dataclass(frozen=True)
class HHSessionStatus:
    authenticated: bool
    final_url: str
    reason: str


def check_hh_session(*, headless: bool = True) -> HHSessionStatus:
    """Check the shared persistent HH profile without changing application state."""
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=headless,
                viewport={"width": 1440, "height": 1000},
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(
                    RESUMES_URL,
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                page.wait_for_timeout(700)
                authenticated = hh_is_authenticated(page)
                final_url = page.url or ""
            finally:
                context.close()
    except Exception as exc:
        return HHSessionStatus(
            authenticated=False,
            final_url="",
            reason=f"Не удалось проверить HH-сессию: {type(exc).__name__}: {exc}",
        )

    if authenticated:
        return HHSessionStatus(
            authenticated=True,
            final_url=final_url,
            reason="HH-сессия активна.",
        )

    return HHSessionStatus(
        authenticated=False,
        final_url=final_url,
        reason=(
            "HH-сессия не авторизована или истекла. "
            "Нужно заново войти в HH через browser-profile."
        ),
    )
