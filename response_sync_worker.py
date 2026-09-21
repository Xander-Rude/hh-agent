from __future__ import annotations

import os
import re
from collections import defaultdict

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from sqlalchemy import or_, select

from app.application_events import update_career_status
from app.db import Application, SessionLocal, Vacancy
from hh_browser import PROFILE_DIR, RESUMES_URL, hh_is_authenticated
from hh_response_state import classify_hh_negotiation_text


load_dotenv()

NEGOTIATIONS_URL = os.getenv(
    "HH_NEGOTIATIONS_URL",
    "https://hh.ru/applicant/negotiations",
)
HEADLESS = os.getenv("HH_RESPONSE_SYNC_HEADLESS", "true").lower() == "true"
MAX_PAGES = max(1, int(os.getenv("HH_RESPONSE_SYNC_MAX_PAGES", "8")))

VACANCY_ID_RE = re.compile(r"/vacancy/(\d+)")
STATUS_PRIORITY = {
    "submitted": 10,
    "viewed": 20,
    "workflow_invited": 30,
    "rejected": 40,
}


def _candidate_texts(anchor) -> list[str]:
    try:
        items = anchor.evaluate(
            """
            el => {
              const result = [];
              let node = el;
              for (let depth = 0; node && depth < 9; depth += 1) {
                const text = (node.innerText || '').trim();
                if (text && text.length <= 3500 && !result.includes(text)) {
                  result.push(text);
                }
                const qa = (node.getAttribute && node.getAttribute('data-qa')) || '';
                if (/negotiat|response|vacancy-serp-item/i.test(qa) && depth > 0) {
                  break;
                }
                node = node.parentElement;
              }
              return result;
            }
            """
        )
    except Exception:
        return []

    return [str(item) for item in (items or []) if str(item).strip()]


def _extract_page_states(page) -> dict[str, dict[str, str]]:
    states: dict[str, dict[str, str]] = {}
    anchors = page.locator('a[href*="/vacancy/"]')

    try:
        count = anchors.count()
    except Exception:
        return states

    for index in range(count):
        anchor = anchors.nth(index)
        try:
            href = anchor.get_attribute("href") or ""
        except Exception:
            continue

        match = VACANCY_ID_RE.search(href)
        if not match:
            continue
        vacancy_id = match.group(1)

        best_status = None
        best_text = ""
        for text in _candidate_texts(anchor):
            status = classify_hh_negotiation_text(text)
            if status is None:
                continue
            if (
                best_status is None
                or STATUS_PRIORITY[status] > STATUS_PRIORITY[best_status]
            ):
                best_status = status
                best_text = text

        if best_status is None:
            continue

        previous = states.get(vacancy_id)
        if (
            previous is None
            or STATUS_PRIORITY[best_status]
            > STATUS_PRIORITY[previous["status"]]
        ):
            states[vacancy_id] = {
                "status": best_status,
                "text": " ".join(best_text.split())[:1200],
            }

    return states


def collect_hh_states(page) -> dict[str, dict[str, str]]:
    collected: dict[str, dict[str, str]] = {}

    for page_index in range(MAX_PAGES):
        separator = "&" if "?" in NEGOTIATIONS_URL else "?"
        url = f"{NEGOTIATIONS_URL}{separator}page={page_index}"
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1200)

        page_states = _extract_page_states(page)
        new_ids = set(page_states) - set(collected)

        for vacancy_id, snapshot in page_states.items():
            old = collected.get(vacancy_id)
            if (
                old is None
                or STATUS_PRIORITY[snapshot["status"]]
                > STATUS_PRIORITY[old["status"]]
            ):
                collected[vacancy_id] = snapshot

        print(
            "[RESPONSE SYNC] "
            f"page={page_index} states={len(page_states)} new={len(new_ids)}"
        )

        # HH can repeat the last page instead of returning an empty one.
        if page_index > 0 and not new_ids:
            break
        if not page_states:
            break

    return collected


def _applications_by_external_id() -> dict[str, list[int]]:
    session = SessionLocal()
    try:
        rows = session.execute(
            select(Application.id, Vacancy.external_id, Vacancy.hh_id)
            .join(Vacancy, Vacancy.id == Application.vacancy_id)
            .where(
                or_(Vacancy.source == "hh", Vacancy.source.is_(None)),
                or_(
                    Application.status == "applied",
                    Application.applied_at.is_not(None),
                ),
            )
        ).all()
    finally:
        session.close()

    result: dict[str, list[int]] = defaultdict(list)
    for application_id, external_id, hh_id in rows:
        key = str(external_id or hh_id or "").strip()
        if key:
            result[key].append(int(application_id))
    return dict(result)


def main() -> int:
    application_ids = _applications_by_external_id()
    if not application_ids:
        print("[RESPONSE SYNC] No submitted HH applications in database.")
        return 0

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=HEADLESS,
            viewport={"width": 1440, "height": 1000},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()

            # Authenticate on a page for which hh_is_authenticated has stable
            # applicant UI markers, then move to negotiations.
            page.goto(RESUMES_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1000)
            if not hh_is_authenticated(page):
                print("[RESPONSE SYNC] HH session is not authenticated.")
                return 4

            states = collect_hh_states(page)
        finally:
            context.close()

    matched = 0
    changed = 0
    by_status: dict[str, int] = defaultdict(int)

    for vacancy_id, snapshot in states.items():
        ids = application_ids.get(vacancy_id, [])
        if not ids:
            continue

        for application_id in ids:
            matched += 1
            status = snapshot["status"]
            by_status[status] += 1
            was_changed = update_career_status(
                application_id,
                status,
                source="hh_negotiations",
                details={
                    "hh_vacancy_id": vacancy_id,
                    "platform_text": snapshot["text"],
                    "human_response": False,
                },
            )
            changed += int(was_changed)
            note = (
                " platform-only; not a human response"
                if status == "workflow_invited"
                else ""
            )
            print(
                "[FUNNEL] "
                f"application={application_id} hh={vacancy_id} "
                f"career_status={status}{note}"
            )

    print(
        "[RESPONSE SYNC] "
        f"matched={matched} changed={changed} "
        + " ".join(
            f"{name}={count}"
            for name, count in sorted(by_status.items())
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
