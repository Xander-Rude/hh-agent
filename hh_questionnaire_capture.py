"""Read-only capture of an already-manual HH questionnaire by Application ID.

Must execute under the Windows user that owns the Playwright HH profile.
This script never clicks submit and never changes resume visibility.
"""
from __future__ import annotations

import argparse

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from app.db import Application, SessionLocal, Vacancy
from app.hh_questionnaire import (
    QuestionnaireStore, extract, generate_answers, send_card,
)
from background_common import HHProfileLock
from hh_accounts import account_resume_id, get_account
from hh_browser import hh_browser_context_options

load_dotenv()


def inspect_application(application_id: int) -> bool:
    with SessionLocal() as db:
        application = db.get(Application, application_id)
        if application is None or application.status != "manual_required":
            raise ValueError("Only an existing manual_required application may be inspected")
        vacancy = db.get(Vacancy, application.vacancy_id)
        if vacancy is None or (vacancy.source or "hh") != "hh" or not str(vacancy.hh_id).isdigit():
            raise ValueError("Not an HH vacancy")
        account = get_account(application.account_key)
        expected_resume = account_resume_id(account)
        if expected_resume and application.selected_resume_id != expected_resume:
            raise ValueError("Account/resume binding mismatch")
        vacancy_id = str(vacancy.hh_id)
        title, company, url = vacancy.title, vacancy.company or "", vacancy.url

    with HHProfileLock(account.key):
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(account.profile_dir),
                **hh_browser_context_options(headless=True),
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                questionnaire_url = (
                    "https://hh.ru/applicant/vacancy_response?vacancyId="
                    + vacancy_id + "&startedWithQuestion=false&hhtmFrom=vacancy"
                )
                page.goto(questionnaire_url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(900)
                captured = extract(page)
            finally:
                context.close()

    if not captured or captured.get("unsupported"):
        print(
            "[HH QUESTIONNAIRE] No supported form: "
            + str((captured or {}).get("unsupported") or "form not found"),
            flush=True,
        )
        return False
    questions = captured["questions"]
    store = QuestionnaireStore()
    old = store.get(application_id)
    from app.hh_questionnaire import fingerprint
    if (
        old and old["fingerprint"] == fingerprint(questions)
        and old["account_key"] == account.key
        and old["status"] in {"pending", "approved", "sent"}
    ):
        print(f"[HH QUESTIONNAIRE] Existing draft state={old['status']} app={application_id}")
        return True

    answers = generate_answers(application_id, questions)
    if not store.save_draft(application_id, account.key, questions, answers):
        return True
    print(
        f"[HH QUESTIONNAIRE] Captured app={application_id} account={account.key} "
        f"questions={len(questions)} visibility_block={captured['visibility_block']}",
        flush=True,
    )
    record = store.get(application_id)
    delivered = send_card(application_id, title, company, url, record)
    print(f"[HH QUESTIONNAIRE] Telegram card delivered={delivered}", flush=True)
    return delivered


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--application-id", type=int, required=True)
    args = parser.parse_args()
    if not inspect_application(args.application_id):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
