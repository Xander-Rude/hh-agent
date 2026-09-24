from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.sync_api import sync_playwright

from hh_accounts import (
    HHAccount,
    account_for_worker,
    account_label,
    account_resume_id,
    get_account,
)
from hh_browser import RESUMES_URL, hh_is_authenticated


_RESUME_ID_RE = re.compile(r"/resume/([0-9a-f]+)", re.IGNORECASE)


@dataclass(frozen=True)
class HHSessionStatus:
    authenticated: bool
    identity_verified: bool
    account_key: str
    expected_resume_id: str | None
    observed_resume_ids: tuple[str, ...]
    final_url: str
    reason: str


def _observed_resume_ids(page) -> tuple[str, ...]:
    result: list[str] = []
    try:
        links = page.locator('a[href*="/resume/"]')
        count = links.count()
    except Exception:
        return ()

    for index in range(count):
        try:
            href = links.nth(index).get_attribute("href") or ""
        except Exception:
            continue
        match = _RESUME_ID_RE.search(href)
        if match:
            value = match.group(1)
            if value not in result:
                result.append(value)

    return tuple(result)


def check_hh_session(
    *,
    account: HHAccount | str | None = None,
    headless: bool = True,
) -> HHSessionStatus:
    """Verify both authentication and the expected isolated HH identity."""

    if account is None:
        item = account_for_worker()
    elif isinstance(account, str):
        item = get_account(account)
    else:
        item = account

    expected_resume_id = account_resume_id(item)

    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(item.profile_dir),
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
                page.wait_for_timeout(900)
                authenticated = hh_is_authenticated(page)
                final_url = page.url or ""
                observed_resume_ids = (
                    _observed_resume_ids(page)
                    if authenticated
                    else ()
                )
            finally:
                context.close()
    except Exception as exc:
        return HHSessionStatus(
            authenticated=False,
            identity_verified=False,
            account_key=item.key,
            expected_resume_id=expected_resume_id,
            observed_resume_ids=(),
            final_url="",
            reason=(
                f"{account_label(item.key)}: не удалось проверить HH-сессию: "
                f"{type(exc).__name__}: {exc}"
            ),
        )

    if not authenticated:
        return HHSessionStatus(
            authenticated=False,
            identity_verified=False,
            account_key=item.key,
            expected_resume_id=expected_resume_id,
            observed_resume_ids=observed_resume_ids,
            final_url=final_url,
            reason=(
                f"{account_label(item.key)}: HH-сессия не авторизована "
                "или истекла."
            ),
        )

    if expected_resume_id:
        if expected_resume_id not in observed_resume_ids:
            return HHSessionStatus(
                authenticated=True,
                identity_verified=False,
                account_key=item.key,
                expected_resume_id=expected_resume_id,
                observed_resume_ids=observed_resume_ids,
                final_url=final_url,
                reason=(
                    f"{account_label(item.key)}: сессия авторизована, но "
                    "ожидаемое резюме этого аккаунта не найдено. "
                    "Apply остановлен, чтобы не откликнуться не тем аккаунтом."
                ),
            )

    return HHSessionStatus(
        authenticated=True,
        identity_verified=True,
        account_key=item.key,
        expected_resume_id=expected_resume_id,
        observed_resume_ids=observed_resume_ids,
        final_url=final_url,
        reason=f"{account_label(item.key)}: HH-сессия и identity подтверждены.",
    )
