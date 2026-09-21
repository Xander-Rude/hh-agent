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
NEGOTIATION_STATUSES = (
    "active",
    "response",
    "invitations",
    "discard",
    "interview",
    "hired",
)
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


def classify_negotiation_text(
    text: str | None,
    *,
    collection_status: str | None = None,
) -> str | None:
    """Classify HH workflow state without inventing a human interaction.

    Applicant negotiation collections are HH workflow states. In particular,
    the interview collection can be entered automatically by an employer
    workflow, so it is stored as workflow_interview, not interview_agreed.
    """
    if collection_status == "discard":
        return "rejected"
    if collection_status == "interview":
        return "workflow_interview"
    if collection_status == "hired":
        return "workflow_hired"
    if collection_status == "invitations":
        return "workflow_invitation"
    if collection_status == "response":
        return "submitted"

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


def _snapshot_key(
    application_id: int,
    collection_status: str,
    text: str,
) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
    return f"hh-negotiation:{application_id}:{collection_status}:{digest}"


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


def _process_card(card, *, collection_status: str) -> tuple[bool, str]:
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
        state = classify_negotiation_text(
            text,
            collection_status=collection_status,
        )

        details = {
            "hh_id": hh_id,
            "vacancy_title": vacancy.title if vacancy is not None else title,
            "card_title": title,
            "href": href,
            "unread_badge": unread,
            "workflow_state": state,
            "collection_status": collection_status,
            "card_text": text[:2000],
        }

        record_application_event(
            application.id,
            "hh_negotiation_snapshot",
            source="hh_response_sync",
            details=details,
            # Snapshot hashes make polling idempotent without hiding changes.
            dedupe_key=_snapshot_key(
                application.id,
                collection_status,
                normalized,
            ),
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
                dedupe_key=(
                    f"hh-unread:{application.id}:{collection_status}:"
                    f"{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:24]}"
                ),
            )

        if state == "rejected":
            set_career_state(
                application.id,
                "rejected",
                source="hh_response_sync",
                details={"hh_id": hh_id},
                dedupe_key=f"hh-state:{application.id}:rejected",
            )
        elif state in {"workflow_invitation", "workflow_interview", "workflow_hired"}:
            # Never let a generic HH workflow invitation overwrite a terminal
            # or explicitly verified later state.
            if application.career_state in {
                None,
                "submitted",
                "viewed",
                "workflow_invitation",
                "workflow_interview",
                "workflow_hired",
            }:
                set_career_state(
                    application.id,
                    state,
                    source="hh_response_sync",
                    details={
                        "hh_id": hh_id,
                        "note": (
                            "HH workflow state; not a verified human contact "
                            "or a confirmed interview"
                        ),
                    },
                    is_human_contact=False,
                    dedupe_key=f"hh-state:{application.id}:{state}",
                )
            else:
                touch_response_check(application.id)
        elif state == "submitted":
            if application.career_state is None:
                set_career_state(
                    application.id,
                    "submitted",
                    source="hh_response_sync",
                    details={"hh_id": hh_id},
                    dedupe_key=f"hh-state:{application.id}:submitted",
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

            total_cards = 0
            processed = 0
            skipped = 0
            states: dict[str, int] = {}
            per_collection: dict[str, int] = {}

            for collection_status in NEGOTIATION_STATUSES:
                page.goto(
                    f"{NEGOTIATIONS_URL}?status={collection_status}",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                page.wait_for_timeout(1000)

                cards = page.locator(CARD_SELECTOR)
                count = min(cards.count(), max(0, MAX_CARDS))
                per_collection[collection_status] = count
                total_cards += count

                for index in range(count):
                    ok, state = _process_card(
                        cards.nth(index),
                        collection_status=collection_status,
                    )
                    if ok:
                        processed += 1
                        states[state] = states.get(state, 0) + 1
                    else:
                        skipped += 1
                        if state not in {
                            "not_our_application",
                            "no_vacancy_link",
                        }:
                            print(
                                "[RESPONSE SYNC WARN] "
                                f"status={collection_status} card={index} {state}"
                            )

            print(
                "[RESPONSE SYNC] "
                f"cards={total_cards} processed={processed} skipped={skipped} "
                f"collections={per_collection} states={states}"
            )
            return 0
        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
