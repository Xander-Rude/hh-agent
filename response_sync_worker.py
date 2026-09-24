from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timedelta

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from sqlalchemy import or_, select

from app.application_events import (
    record_due_no_response_events,
    update_career_status,
)
from app.db import Application, SessionLocal, Vacancy
from background_common import HHProfileLock
from hh_accounts import account_resume_id, observable_accounts
from hh_browser import RESUMES_URL, hh_is_authenticated
from hh_response_state import detect_hh_vacancy_career_state


load_dotenv()

HEADLESS = os.getenv(
    "HH_RESPONSE_SYNC_HEADLESS",
    "true",
).lower() == "true"
LOOKBACK_DAYS = max(
    7,
    int(os.getenv("HH_RESPONSE_SYNC_LOOKBACK_DAYS", "45")),
)
MAX_APPLICATIONS_PER_ACCOUNT = max(
    1,
    int(os.getenv("HH_RESPONSE_SYNC_MAX_APPLICATIONS", "40")),
)
PAGE_SETTLE_MS = max(
    250,
    int(os.getenv("HH_RESPONSE_SYNC_PAGE_SETTLE_MS", "900")),
)

TERMINAL_CAREER_STATES = {
    "rejected",
    "offer",
    "declined_by_user",
    "withdrawn",
    "vacancy_closed",
}


def _vacancy_redirected_to_auth(page) -> bool:
    url = str(getattr(page, "url", "") or "").lower()
    return (
        "account/login" in url
        or "/login" in url
        or "account/signup" in url
    )


def _candidate_applications(
    account_key: str,
) -> list[dict]:
    cutoff = datetime.utcnow() - timedelta(days=LOOKBACK_DAYS)
    session = SessionLocal()
    try:
        rows = session.execute(
            select(
                Application.id,
                Application.career_status,
                Application.applied_at,
                Application.created_at,
                Vacancy.external_id,
                Vacancy.hh_id,
                Vacancy.url,
                Vacancy.title,
            )
            .join(
                Vacancy,
                Vacancy.id == Application.vacancy_id,
            )
            .where(
                Application.account_key == account_key,
                or_(
                    Vacancy.source == "hh",
                    Vacancy.source.is_(None),
                ),
                or_(
                    Application.status == "applied",
                    Application.applied_at.is_not(None),
                ),
                or_(
                    Application.applied_at >= cutoff,
                    Application.created_at >= cutoff,
                ),
            )
            .order_by(
                Application.applied_at.desc(),
                Application.id.desc(),
            )
            .limit(MAX_APPLICATIONS_PER_ACCOUNT)
        ).all()
    finally:
        session.close()

    result: list[dict] = []
    for row in rows:
        career_status = (
            str(row.career_status or "unknown").strip()
            or "unknown"
        )
        if career_status in TERMINAL_CAREER_STATES:
            continue

        hh_id = str(
            row.external_id
            or row.hh_id
            or ""
        ).strip()
        url = str(row.url or "").strip()
        if not url and hh_id:
            url = f"https://hh.ru/vacancy/{hh_id}"
        if not url:
            continue

        result.append(
            {
                "application_id": int(row.id),
                "career_status": career_status,
                "hh_id": hh_id,
                "url": url,
                "title": str(row.title or ""),
            }
        )

    return result


def _probe_application(
    page,
    *,
    account_key: str,
    item: dict,
) -> tuple[bool, str | None]:
    application_id = item["application_id"]
    hh_id = item["hh_id"]
    url = item["url"]

    try:
        response = page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=30000,
        )
        page.wait_for_timeout(PAGE_SETTLE_MS)
    except Exception as exc:
        print(
            "[RESPONSE SYNC] "
            f"application={application_id} hh={hh_id} "
            f"probe_error={type(exc).__name__}: {exc}"
        )
        return False, None

    if _vacancy_redirected_to_auth(page):
        print(
            "[RESPONSE SYNC] "
            f"application={application_id} hh={hh_id} "
            "session_lost"
        )
        return False, "session_lost"

    try:
        status_code = (
            int(response.status)
            if response is not None
            else 200
        )
    except Exception:
        status_code = 200

    if status_code >= 400:
        print(
            "[RESPONSE SYNC] "
            f"application={application_id} hh={hh_id} "
            f"http_status={status_code}; skip"
        )
        return False, None

    state, evidence = detect_hh_vacancy_career_state(page)
    details = {
        "hh_vacancy_id": hh_id,
        "platform_text": evidence,
        "probe": "vacancy_response_widget_v2",
        "human_response": False,
        "account_key": account_key,
    }
    raw_ref = (
        f"hh-vacancy:{account_key}:{hh_id or application_id}"
    )

    if state is None:
        print(
            "[RESPONSE SYNC] "
            f"application={application_id} hh={hh_id} "
            "state=unresolved verified=0"
        )
        return False, None

    if state == "submitted":
        update_career_status(
            application_id,
            state,
            source="hh_vacancy_probe",
            details=details,
            confidence="platform_observed",
            raw_ref=raw_ref,
            emit_event=False,
        )
    else:
        update_career_status(
            application_id,
            state,
            source="hh_vacancy_probe",
            details=details,
            confidence="platform_observed",
            raw_ref=raw_ref,
        )

    print(
        "[FUNNEL] "
        f"application={application_id} hh={hh_id} "
        f"career_status={state} "
        f"evidence={evidence[:180]!r}"
    )
    return True, state


def _sync_account(
    playwright,
    account,
) -> tuple[int, int, int]:
    candidates = _candidate_applications(account.key)
    if not candidates:
        print(
            f"[RESPONSE SYNC] {account.label}: "
            "no recent submitted HH applications."
        )
        return 0, 0, 0

    try:
        profile_lock = HHProfileLock(account.key)
        profile_lock.__enter__()
    except RuntimeError as exc:
        if str(exc) == "agent_lock_busy":
            print(
                f"[RESPONSE SYNC] {account.label}: "
                "profile busy, skipping this account for this run."
            )
            return 0, 0, 5
        raise

    context = None
    try:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(account.profile_dir),
            headless=HEADLESS,
            viewport={"width": 1440, "height": 1000},
        )
        page = (
            context.pages[0]
            if context.pages
            else context.new_page()
        )
        page.goto(
            RESUMES_URL,
            wait_until="domcontentloaded",
            timeout=30000,
        )
        page.wait_for_timeout(700)
        if not hh_is_authenticated(page):
            print(
                f"[RESPONSE SYNC] {account.label}: "
                "HH session is not authenticated."
            )
            return 0, 0, 4

        expected_resume_id = account_resume_id(account)
        if expected_resume_id:
            try:
                identity_matches = (
                    page.locator(
                        f'a[href*="/resume/{expected_resume_id}"]'
                    ).count()
                    > 0
                )
            except Exception:
                identity_matches = False

            if not identity_matches:
                print(
                    f"[RESPONSE SYNC] {account.label}: "
                    "authenticated profile does not match expected resume_id; "
                    "skipping to avoid cross-account attribution."
                )
                return 0, 0, 7

        checked_ids: set[int] = set()
        by_status: dict[str, int] = defaultdict(int)
        session_lost = False

        for item in candidates:
            checked, state = _probe_application(
                page,
                account_key=account.key,
                item=item,
            )
            if checked:
                checked_ids.add(item["application_id"])
            if state == "session_lost":
                session_lost = True
                break
            if state:
                by_status[state] += 1

        no_response = record_due_no_response_events(
            application_ids=checked_ids,
            hh_only=True,
        )

        print(
            "[RESPONSE SYNC] "
            f"{account.label} candidates={len(candidates)} "
            f"checked={len(checked_ids)} "
            + " ".join(
                f"{name}={count}"
                for name, count in sorted(by_status.items())
            )
            + (
                " "
                f"no_response_7d={no_response['no_response_7d']} "
                f"no_response_30d={no_response['no_response_30d']}"
            )
        )

        return (
            len(candidates),
            len(checked_ids),
            4 if session_lost else 0,
        )
    finally:
        if context is not None:
            context.close()
        profile_lock.__exit__(None, None, None)


def main() -> int:
    total_candidates = 0
    total_checked = 0
    warnings: list[str] = []

    with sync_playwright() as playwright:
        for account in observable_accounts():
            candidates, checked, code = _sync_account(
                playwright,
                account,
            )
            total_candidates += candidates
            total_checked += checked
            if code:
                warnings.append(f"{account.key}:{code}")

    print(
        "[RESPONSE SYNC] "
        f"total_candidates={total_candidates} "
        f"total_checked={total_checked}"
    )
    if warnings:
        print(
            "[RESPONSE SYNC] account warnings: "
            + ", ".join(warnings)
        )

    if total_candidates > 0 and total_checked == 0:
        print(
            "[RESPONSE SYNC] ERROR: candidates exist but no application "
            "had trustworthy response evidence. Refusing silent success."
        )
        return 6

    # One stale account session must not fail the rest of the agent.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
