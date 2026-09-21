from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from sqlalchemy import or_, select

from app.application_events import (
    record_application_event,
    set_career_state,
    touch_response_check,
)
from app.db import Application, SessionLocal, Vacancy
from hh_browser import PROFILE_DIR, RESUMES_URL, hh_is_authenticated


load_dotenv()

NEGOTIATIONS_URL = "https://hh.ru/applicant/negotiations"
HEADLESS = os.getenv("HH_RESPONSE_SYNC_HEADLESS", "true").lower() == "true"
MAX_CARDS = int(os.getenv("HH_RESPONSE_SYNC_MAX_CARDS", "100"))

CARD_SELECTOR = 'div[data-qa="negotiations-item"]'
VACANCY_LINK_SELECTOR = 'a[data-qa="negotiations-item-vacancy-link"]'
UNREAD_BADGE_SELECTOR = 'span[data-qa="negotiations-item-badge"]'

REJECTION_MARKERS = (
    "работодатель отказал",
    "работодатель отклонил",
    "отказ работодателя",
    "вам отказали",
    "вам отказано",
    "ваш отклик отклонен",
    "ваш отклик отклонён",
    "не готовы пригласить",
)
WORKFLOW_INVITATION_MARKERS = (
    "приглашение",
    "пригласил",
    "пригласили",
)
VIEW_MARKERS = (
    "резюме просмотрено",
    "отклик просмотрен",
    "работодатель просмотрел",
    "просмотрел резюме",
)


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _normalize(text: str | None) -> str:
    return " ".join((text or "").lower().replace("ё", "е").split())


def classify_negotiation_text(text: str | None) -> str | None:
    """Classify only HH workflow state, never a real human interview.

    HH can auto-create invitations as part of an employer workflow. Therefore
    an invitation is deliberately classified as workflow_invitation and must
    not be treated as human_response or interview_agreed.
    """
    value = _normalize(text)
    if any(_normalize(marker) in value for marker in REJECTION_MARKERS):
        return "rejected"
    if any(_normalize(marker) in value for marker in WORKFLOW_INVITATION_MARKERS):
        return "workflow_invitation"
    if any(_normalize(marker) in value for marker in VIEW_MARKERS):
        return "viewed"
    return None


def _vacancy_id_from_href(href: str | None) -> str | None:
    match = re.search(r"/vacancy/(\d+)", href or "")
    return match.group(1) if match else None


def _snapshot_key(application_id: int, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
    return f"hh-negotiation:{application_id}:{digest}"


def _latest_application_for_hh_id(hh_id: str):
    session = SessionLocal()
    try:
        row = session.execute(
            select(Application, Vacancy)
            .join(Vacancy, Vacancy.id == Application.vacancy_id)
            .where(
                or_(
                    Vacancy.external_id == hh_id,
                    Vacancy.hh_id == hh_id,
                )
            )
            .order_by(Application.id.desc())
            .limit(1)
        ).first()
        if row is None:
            return None, None

        application, vacancy = row
        session.expunge(application)
        session.expunge(vacancy)
        return application, vacancy
    finally:
        session.close()


def _is_visible(locator) -> bool:
    try:
        return locator.count() > 0 and locator.first.is_visible()
    except Exception:
        return False


def _process_card(card) -> tuple[bool, str]:
    try:
        link = card.locator(VACANCY_LINK_SELECTOR).first
        if not _is_visible(link):
            return False, "no_vacancy_link"

        href = link.get_attribute("href") or ""
        title = (link.inner_text(timeout=1500) or "").strip()
        hh_id = _vacancy_id_from_href(href)
        if not hh_id:
            return False, "no_hh_id"

        application, vacancy = _latest_application_for_hh_id(hh_id)
        if application is None:
            return False, "not_our_application"

        text = (card.inner_text(timeout=2500) or "").strip()
        normalized = _normalize(text)
        if not normalized:
            touch_response_check(application.id)
            return False, "empty_card"

        unread = _is_visible(card.locator(UNREAD_BADGE_SELECTOR))
        state = classify_negotiation_text(text)

        details = {
            "hh_id": hh_id,
            "vacancy_title": vacancy.title if vacancy is not None else title,
            "card_title": title,
            "href": href,
            "unread_badge": unread,
            "workflow_state": state,
            "card_text": text[:2000],
        }

        record_application_event(
            application.id,
            "hh_negotiation_snapshot",
            source="hh_response_sync",
            details=details,
            # Snapshot hashes make polling idempotent without hiding changes.
            dedupe_key=_snapshot_key(application.id, normalized),
        )

        if unread:
            record_application_event(
                application.id,
                "hh_unread_topic",
                source="hh_response_sync",
                details={
                    "hh_id": hh_id,
                    "vacancy_title": details["vacancy_title"],
                },
                # An unread HH topic may be a bot/system workflow event.
                is_human_contact=False,
                dedupe_key=f"hh-unread:{application.id}:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:24]}",
            )

        if state == "rejected":
            set_career_state(
                application.id,
                "rejected",
                source="hh_response_sync",
                details={"hh_id": hh_id},
                dedupe_key=f"hh-state:{application.id}:rejected",
            )
        elif state == "workflow_invitation":
            # Never let a generic HH workflow invitation overwrite a terminal
            # or explicitly verified later state.
            if application.career_state in {
                None,
                "submitted",
                "viewed",
                "workflow_invitation",
            }:
                set_career_state(
                    application.id,
                    "workflow_invitation",
                    source="hh_response_sync",
                    details={
                        "hh_id": hh_id,
                        "note": "HH workflow invitation; not a verified human contact",
                    },
                    is_human_contact=False,
                    dedupe_key=f"hh-state:{application.id}:workflow_invitation",
                )
            else:
                touch_response_check(application.id)
        elif state == "viewed":
            # Do not downgrade a later workflow state to viewed.
            if application.career_state in {None, "submitted", "viewed"}:
                set_career_state(
                    application.id,
                    "viewed",
                    source="hh_response_sync",
                    details={"hh_id": hh_id},
                    dedupe_key=f"hh-state:{application.id}:viewed",
                )
            else:
                touch_response_check(application.id)
        else:
            touch_response_check(application.id)

        return True, state or "snapshot"
    except Exception as exc:
        return False, f"{type(exc).__name__}:{exc}"


def main() -> int:
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=HEADLESS,
            viewport={"width": 1440, "height": 1000},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()

            # Validate the shared applicant session on a page where our auth
            # detector has stable markers, then switch to negotiations.
            page.goto(RESUMES_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1000)
            if not hh_is_authenticated(page):
                print("[RESPONSE SYNC] HH session is not authenticated")
                return 4

            page.goto(
                NEGOTIATIONS_URL,
                wait_until="domcontentloaded",
                timeout=30000,
            )
            page.wait_for_timeout(1500)

            cards = page.locator(CARD_SELECTOR)
            count = min(cards.count(), max(0, MAX_CARDS))
            processed = 0
            skipped = 0
            states: dict[str, int] = {}

            for index in range(count):
                ok, state = _process_card(cards.nth(index))
                if ok:
                    processed += 1
                    states[state] = states.get(state, 0) + 1
                else:
                    skipped += 1
                    if state not in {"not_our_application", "no_vacancy_link"}:
                        print(f"[RESPONSE SYNC WARN] card={index} {state}")

            print(
                "[RESPONSE SYNC] "
                f"cards={count} processed={processed} skipped={skipped} "
                f"states={states}"
            )
            return 0
        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
