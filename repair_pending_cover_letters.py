from __future__ import annotations

import argparse
import asyncio
import json
import os
from types import SimpleNamespace

from dotenv import load_dotenv
from sqlalchemy import select
from telegram import Bot

from app.clean_live_guard import current_clean_assessment
from app.cover_letter_runtime import (
    build_clean_cover_letter,
    build_legacy_vacancy_cover_letter,
    parse_strengths,
)
from app.db import Application, Evaluation, SessionLocal, Vacancy
from app.decision_snapshot import refresh_pending_decision_snapshot_cover_letter
from app.cross_account import confirmed_other_account_application


def _latest_evaluation(session, vacancy_id: int) -> Evaluation | None:
    return session.scalar(
        select(Evaluation)
        .where(Evaluation.vacancy_id == vacancy_id)
        .where(~Evaluation.model.startswith("hard-filter/"))
        .order_by(Evaluation.created_at.desc(), Evaluation.id.desc())
        .limit(1)
    )


def _new_cover_letter(session, application: Application, vacancy: Vacancy) -> str:
    if (application.account_key or "old") == "clean":
        assessment = current_clean_assessment(session, vacancy.id)
        if assessment is None:
            raise RuntimeError("no current CLEAN assessment")
        return build_clean_cover_letter(
            vacancy_title=vacancy.title,
            vacancy_company=vacancy.company,
            vacancy_description=vacancy.description or "",
            extraction_json=assessment.extraction_json,
        )

    evaluation = _latest_evaluation(session, vacancy.id)
    return build_legacy_vacancy_cover_letter(
        vacancy_title=vacancy.title,
        vacancy_company=vacancy.company,
        vacancy_description=vacancy.description or "",
        stored_text=(
            evaluation.cover_letter
            if evaluation is not None
            else application.cover_letter
        ),
        strengths=(
            parse_strengths(evaluation.strengths)
            if evaluation is not None
            else []
        ),
    )


def _pending_rows(session):
    return session.execute(
        select(Application, Vacancy)
        .join(Vacancy, Vacancy.id == Application.vacancy_id)
        .where(Application.status == "notified")
        .where(Application.applied_at.is_(None))
        .where(Application.telegram_chat_id.is_not(None))
        .where(Application.telegram_message_id.is_not(None))
        .where(Vacancy.source == "hh")
        .order_by(Application.id.asc())
    ).all()


async def run(*, apply_changes: bool) -> int:
    load_dotenv()
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if apply_changes and not bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required with --apply")

    bot = Bot(token=bot_token) if apply_changes else None

    # Import after dotenv so telegram_bot sees the same production config.
    import telegram_bot as bot_module

    would_change = 0
    changed = 0
    failed = 0
    session = SessionLocal()
    try:
        rows = _pending_rows(session)
        print(f"pending_cards={len(rows)} apply={apply_changes}")

        for application, vacancy in rows:
            try:
                evaluation = _latest_evaluation(session, vacancy.id)
                new_letter = _new_cover_letter(session, application, vacancy).strip()
                old_letter = (application.cover_letter or "").strip()
                if not new_letter:
                    raise RuntimeError("empty generated cover letter")

                assessment = None
                if (application.account_key or "old") == "clean":
                    assessment = current_clean_assessment(session, vacancy.id)
                    if assessment is None:
                        raise RuntimeError("no current CLEAN assessment")
                    clean_evaluation = evaluation or SimpleNamespace(
                        strengths="[]",
                        selected_resume_title=application.selected_resume_title,
                        selected_resume_key=application.selected_resume_key,
                    )
                    text = bot_module.build_clean_message(
                        vacancy,
                        clean_evaluation,
                        assessment,
                        cross_account_application=confirmed_other_account_application(
                            session,
                            vacancy_id=vacancy.id,
                            account_key=application.account_key or "old",
                        ),
                    )
                else:
                    if evaluation is None:
                        raise RuntimeError("no legacy evaluation")
                    text = bot_module.build_message(
                        vacancy,
                        evaluation,
                        account_key=application.account_key or "old",
                        cross_account_application=confirmed_other_account_application(
                            session,
                            vacancy_id=vacancy.id,
                            account_key=application.account_key or "old",
                        ),
                    )

                letter_changed = old_letter != new_letter
                if letter_changed:
                    would_change += 1

                payload = {
                    "application_id": application.id,
                    "account": application.account_key,
                    "title": vacancy.title,
                    "company": vacancy.company,
                    "changed": letter_changed,
                    "old": old_letter,
                    "new": new_letter,
                }
                print(json.dumps(payload, ensure_ascii=False))

                if not apply_changes or not letter_changed:
                    continue

                # The user can press a Telegram decision button while this repair
                # is running. Re-read the row immediately before the write and
                # never touch a card that is no longer pending.
                session.refresh(application)
                if application.status != "notified" or application.applied_at is not None:
                    print(
                        json.dumps(
                            {
                                "application_id": application.id,
                                "skipped": "no_longer_pending",
                            },
                            ensure_ascii=False,
                        )
                    )
                    continue

                application.cover_letter = new_letter
                refresh_pending_decision_snapshot_cover_letter(
                    session,
                    application=application,
                    vacancy=vacancy,
                )

                await bot.edit_message_text(
                    chat_id=int(application.telegram_chat_id),
                    message_id=int(application.telegram_message_id),
                    text=text,
                    reply_markup=bot_module.build_keyboard(
                        vacancy.id,
                        application_id=application.id,
                    ),
                    disable_web_page_preview=True,
                )
                session.commit()
                changed += 1
            except Exception as exc:
                session.rollback()
                failed += 1
                print(
                    json.dumps(
                        {
                            "application_id": application.id,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                        ensure_ascii=False,
                    )
                )

        print(
            json.dumps(
                {
                    "pending_cards": len(rows),
                    "would_change": would_change,
                    "changed": changed,
                    "failed": failed,
                    "apply": apply_changes,
                },
                ensure_ascii=False,
            )
        )
        return 1 if failed else 0
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate human cover letters for pending Telegram HH cards."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist letters and edit the existing Telegram cards.",
    )
    args = parser.parse_args()
    return asyncio.run(run(apply_changes=args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
