from __future__ import annotations

import argparse
import json
import os
import time

from playwright.sync_api import sync_playwright
from sqlalchemy import or_, select

from app.db import SessionLocal, Vacancy
from app.hh_vacancy_snapshot import (
    build_hh_source_payload,
    capture_vacancy_dom_snapshot,
    record_vacancy_source_snapshot,
)


PROFILE_DIR = "browser-profile"
HEADLESS = os.getenv("HH_COLLECT_HEADLESS", "false").lower() == "true"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill semantic/raw HH vacancy card snapshots for vacancies "
            "already stored in hh_agent.db. Uses the authenticated browser "
            "profile and does not depend on api.hh.ru."
        )
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum vacancies per run; 0 means no limit (default: 50).",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=3.0,
        help="Delay between HH card loads in seconds (default: 3.0).",
    )
    parser.add_argument(
        "--navigation-timeout-ms",
        type=int,
        default=30000,
        help="Per-card navigation timeout (default: 30000).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refresh vacancies that already have a source payload.",
    )
    return parser.parse_args()


def looks_blocked(page) -> str | None:
    try:
        url = (page.url or "").lower()
        body = (
            page.locator("body")
            .inner_text(timeout=3000)
            .lower()
        )
    except Exception:
        return None

    markers = (
        "подтвердите, что вы не робот",
        "проверка, что вы не робот",
        "введите код с картинки",
        "слишком много запросов",
        "доступ временно ограничен",
        "подозрительная активность",
        "verify you are human",
        "security check",
        "captcha",
    )

    if "captcha" in url or "challenge" in url:
        return url

    return next((marker for marker in markers if marker in body), None)


def main() -> int:
    args = parse_args()
    session = SessionLocal()

    try:
        stmt = (
            select(Vacancy)
            .where(
                or_(
                    Vacancy.source == "hh",
                    Vacancy.source.is_(None),
                )
            )
            .order_by(Vacancy.id.desc())
        )

        if not args.force:
            stmt = stmt.where(
                or_(
                    Vacancy.source_payload_hash.is_(None),
                    Vacancy.source_payload_hash == "",
                )
            )

        if args.limit > 0:
            stmt = stmt.limit(args.limit)

        vacancies = list(session.scalars(stmt).all())
        print(
            "[HH SNAPSHOT BACKFILL] "
            f"selected={len(vacancies)} force={int(args.force)} "
            f"headless={int(HEADLESS)}"
        )

        ok = 0
        failed = 0
        changed = 0

        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR,
                headless=HEADLESS,
                viewport={"width": 1440, "height": 1000},
            )
            page = context.pages[0]

            try:
                for index, vacancy in enumerate(vacancies, start=1):
                    hh_id = str(
                        vacancy.external_id
                        or vacancy.hh_id
                        or ""
                    ).strip()

                    if not hh_id.isdigit():
                        print(
                            "[HH SNAPSHOT BACKFILL SKIP] "
                            f"vacancy={vacancy.id} invalid_hh_id={hh_id!r}"
                        )
                        failed += 1
                        continue

                    url = f"https://hh.ru/vacancy/{hh_id}"

                    try:
                        page.goto(
                            url,
                            wait_until="domcontentloaded",
                            timeout=args.navigation_timeout_ms,
                        )
                        page.wait_for_timeout(1800)

                        blocked = looks_blocked(page)
                        if blocked:
                            print(
                                "[HH SNAPSHOT BACKFILL BLOCKED] "
                                f"{index}/{len(vacancies)} hh={hh_id} "
                                f"marker={blocked!r}"
                            )
                            return 3

                        dom_snapshot = capture_vacancy_dom_snapshot(page)
                        source_payload = build_hh_source_payload(
                            hh_id=hh_id,
                            api_payload=None,
                            dom_snapshot=dom_snapshot,
                            api_error=(
                                "not_requested: DOM is the primary HH source"
                            ),
                        )

                        added = record_vacancy_source_snapshot(
                            session,
                            vacancy,
                            source_payload,
                        )
                        session.commit()

                        ok += 1
                        changed += int(added)
                        print(
                            "[HH SNAPSHOT BACKFILL OK] "
                            f"{index}/{len(vacancies)} vacancy={vacancy.id} "
                            f"hh={hh_id} "
                            f"skills={len(json.loads(vacancy.key_skills_json or '[]'))} "
                            f"history_added={int(added)} "
                            f"main_text={len(dom_snapshot.get('main_text') or '')} "
                            f"raw_html={int(bool(dom_snapshot.get('main_html_gzip_b64')))}"
                        )

                    except Exception as exc:
                        session.rollback()
                        failed += 1
                        print(
                            "[HH SNAPSHOT BACKFILL ERROR] "
                            f"{index}/{len(vacancies)} vacancy={vacancy.id} "
                            f"hh={hh_id} error={type(exc).__name__}: {exc}"
                        )

                    time.sleep(max(0.0, args.sleep))

            finally:
                context.close()

        print(
            "[HH SNAPSHOT BACKFILL DONE] "
            f"ok={ok} failed={failed} history_added={changed}"
        )
        return 0 if failed == 0 else 2

    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
