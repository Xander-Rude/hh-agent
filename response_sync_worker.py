from __future__ import annotations

import os
import re
from collections import defaultdict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from sqlalchemy import or_, select

from app.application_events import (
    record_due_no_response_events,
    update_career_status,
)
from app.db import Application, SessionLocal, Vacancy
from hh_accounts import observable_accounts
from hh_browser import RESUMES_URL, hh_is_authenticated
from hh_response_state import classify_hh_negotiation_text


load_dotenv()

NEGOTIATIONS_URL = os.getenv(
    "HH_NEGOTIATIONS_URL",
    "https://hh.ru/applicant/negotiations",
)
HEADLESS = os.getenv("HH_RESPONSE_SYNC_HEADLESS", "true").lower() == "true"
MAX_PAGES = max(1, int(os.getenv("HH_RESPONSE_SYNC_MAX_PAGES", "8")))
NEGOTIATION_STATUS_FILTERS = (
    "response",
    "invitations",
    "discard",
    "active",
    "all",
)
FILTER_STATUS_FALLBACK = {
    "response": "submitted",
    "invitations": "workflow_invited",
    # The HH discard bucket is a platform workflow signal. Only explicit
    # rejection text may become the stronger rejected outcome.
    "discard": "workflow_discarded",
}

VACANCY_ID_RE = re.compile(r"/vacancy/(\d+)")
STATUS_PRIORITY = {
    "submitted": 10,
    "viewed": 20,
    "workflow_invited": 30,
    "workflow_discarded": 40,
    "rejected": 100,
}


def _candidate_texts(anchor) -> list[str]:
    try:
        items = anchor.evaluate(
            """
            el => {
              const result = [];
              const ownHref = el.getAttribute('href') || '';
              const ownMatch = ownHref.match(/\\/vacancy\\/(\\d+)/);
              const ownId = ownMatch ? ownMatch[1] : null;
              let node = el;

              for (let depth = 0; node && depth < 9; depth += 1) {
                const vacancyIds = new Set(
                  Array.from(node.querySelectorAll('a[href*="/vacancy/"]'))
                    .map(item => {
                      const href = item.getAttribute('href') || '';
                      const match = href.match(/\\/vacancy\\/(\\d+)/);
                      return match ? match[1] : null;
                    })
                    .filter(Boolean)
                );

                // Never climb into a list/container that mixes several
                // vacancies: a rejection on the neighbouring card must not be
                // attributed to this application.
                if (
                  ownId
                  && vacancyIds.size > 0
                  && (
                    vacancyIds.size > 1
                    || !vacancyIds.has(ownId)
                  )
                ) {
                  break;
                }

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


def _extract_page_states(
    page,
    *,
    status_filter: str | None = None,
) -> dict[str, dict[str, str]]:
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
            best_status = FILTER_STATUS_FALLBACK.get(status_filter or "")
            if best_status is not None:
                try:
                    best_text = (anchor.inner_text(timeout=500) or "").strip()
                except Exception:
                    best_text = ""

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


def _negotiations_page_url(status_filter: str, page_index: int) -> str:
    parts = urlsplit(NEGOTIATIONS_URL)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["status"] = status_filter
    query["page"] = str(page_index)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query),
            parts.fragment,
        )
    )


def _validate_status_filter_sets(
    filter_ids: dict[str, set[str]],
) -> None:
    """Fail closed when HH ignores applicant status filters.

    response, invitations and discard are mutually exclusive applicant
    categories. If HH returns nearly the same vacancy IDs for two of them,
    treating the URL filter as truth would fabricate career outcomes.
    """
    comparable = ("response", "invitations", "discard")
    for index, left in enumerate(comparable):
        left_ids = filter_ids.get(left, set())
        if not left_ids:
            continue

        for right in comparable[index + 1 :]:
            right_ids = filter_ids.get(right, set())
            if not right_ids:
                continue

            overlap = len(left_ids & right_ids)
            denominator = min(len(left_ids), len(right_ids))
            ratio = overlap / denominator if denominator else 0.0

            if ratio >= 0.80:
                raise RuntimeError(
                    "HH negotiation status filter is not trustworthy: "
                    f"{left}={len(left_ids)} {right}={len(right_ids)} "
                    f"overlap={overlap} ratio={ratio:.2f}. "
                    "No career states were written."
                )


def collect_hh_states(page) -> dict[str, dict[str, str]]:
    collected: dict[str, dict[str, str]] = {}
    filter_ids: dict[str, set[str]] = {}

    for status_filter in NEGOTIATION_STATUS_FILTERS:
        filter_seen: set[str] = set()

        for page_index in range(MAX_PAGES):
            url = _negotiations_page_url(status_filter, page_index)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1200)

            try:
                anchor_count = page.locator('a[href*="/vacancy/"]').count()
            except Exception:
                anchor_count = -1

            page_states = _extract_page_states(
                page,
                status_filter=status_filter,
            )
            new_filter_ids = set(page_states) - filter_seen
            filter_seen.update(page_states)

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
                f"status={status_filter} page={page_index} "
                f"anchors={anchor_count} states={len(page_states)} "
                f"new={len(new_filter_ids)} total={len(collected)}"
            )

            # HH can repeat the last page instead of returning an empty one.
            if page_index > 0 and not new_filter_ids:
                break
            if not page_states:
                break

        filter_ids[status_filter] = set(filter_seen)

    _validate_status_filter_sets(filter_ids)
    return collected


def _applications_by_external_id(
    account_key: str,
) -> dict[str, list[int]]:
    session = SessionLocal()
    try:
        rows = session.execute(
            select(Application.id, Vacancy.external_id, Vacancy.hh_id)
            .join(Vacancy, Vacancy.id == Application.vacancy_id)
            .where(
                Application.account_key == account_key,
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


def _sync_account(playwright, account) -> tuple[int, int, int]:
    application_ids = _applications_by_external_id(account.key)
    if not application_ids:
        print(
            f"[RESPONSE SYNC] {account.label}: "
            "no submitted HH applications in database."
        )
        return 0, 0, 0

    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(account.profile_dir),
        headless=HEADLESS,
        viewport={"width": 1440, "height": 1000},
    )
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(RESUMES_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1000)
        if not hh_is_authenticated(page):
            print(
                f"[RESPONSE SYNC] {account.label}: "
                "HH session is not authenticated."
            )
            return 0, 0, 4

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
                    "account_key": account.key,
                },
                confidence="platform_observed",
                raw_ref=(
                    f"hh-negotiations:{account.key}:{vacancy_id}"
                ),
            )
            changed += int(was_changed)
            note = (
                " platform-only; not a human response"
                if status == "workflow_invited"
                else ""
            )
            print(
                "[FUNNEL] "
                f"{account.label} application={application_id} hh={vacancy_id} "
                f"career_status={status}{note}"
            )

    print(
        "[RESPONSE SYNC] "
        f"{account.label} matched={matched} changed={changed} "
        + " ".join(
            f"{name}={count}"
            for name, count in sorted(by_status.items())
        )
    )
    return matched, changed, 0


def main() -> int:
    total_matched = 0
    total_changed = 0
    failures: list[str] = []
    successful_accounts: set[str] = set()

    with sync_playwright() as playwright:
        for account in observable_accounts():
            matched, changed, code = _sync_account(playwright, account)
            total_matched += matched
            total_changed += changed
            if code:
                failures.append(f"{account.key}:{code}")
            else:
                successful_accounts.add(account.key)

    no_response = record_due_no_response_events(
        account_keys=successful_accounts,
    )

    print(
        "[RESPONSE SYNC] "
        f"total_matched={total_matched} total_changed={total_changed} "
        f"no_response_7d={no_response['no_response_7d']} "
        f"no_response_30d={no_response['no_response_30d']}"
    )
    if failures:
        print("[RESPONSE SYNC] account warnings: " + ", ".join(failures))

    # OLD is intentionally best-effort observe mode. A stale historical
    # session must not block CLEAN response tracking or the rest of the agent.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
