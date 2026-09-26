from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from app.clean_rescore import (
    create_rescore_run,
    get_rescore_summary,
    process_rescore_batch,
    retry_errors,
)
from app.clean_live_guard import (
    CANDIDATE_PROFILE_VERSION,
    RECRUITER_RESUME_VERSION,
)
from app.db import init_db
from background_common import AgentLock, DATA_DIR


ROOT = Path(__file__).resolve().parent
load_dotenv()

CANDIDATE_FACTS_PATH = Path(
    os.getenv(
        "CLEAN_CANDIDATE_FACTS_PATH",
        str(ROOT / "data" / "resume.txt"),
    )
)
VISIBLE_RESUME_PATH = Path(
    os.getenv(
        "CLEAN_VISIBLE_RESUME_PATH",
        str(ROOT / "data" / "clean_resume_visible.txt"),
    )
)
DEFAULT_MAX_RUNTIME_MINUTES = max(
    0.0,
    float(os.getenv("CLEAN_RESCORE_MAX_RUNTIME_MINUTES", "20")),
)
ITEM_START_GUARD_SECONDS = max(
    0.0,
    float(os.getenv("CLEAN_RESCORE_ITEM_START_GUARD_SECONDS", "180")),
)
RESCORE_WORKER_LOCK_PATH = DATA_DIR / "clean_rescore_worker.lock"

ARCHIVED_MARKERS = (
    "вакансия в архиве",
    "вакансия закрыта",
    "больше не принимает отклики",
    "больше не актуальна",
    "работодатель уже нашел нужного кандидата",
)


class StatelessHHAvailability:
    def __init__(self, *, headless: bool = True) -> None:
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._page = None

    def __enter__(self):
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.headless
        )
        self._page = self._browser.new_page(
            viewport={"width": 1280, "height": 900}
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()

    def __call__(self, snapshot: dict) -> str:
        if self._page is None:
            raise RuntimeError("availability browser is not started")

        url = str(snapshot.get("url") or "").strip()
        hh_id = str(snapshot.get("hh_id") or "").strip()
        if not url and hh_id:
            url = f"https://hh.ru/vacancy/{hh_id}"
        if not url:
            return "unresolved"

        try:
            response = self._page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=20000,
            )
            self._page.wait_for_timeout(700)
        except Exception:
            return "unresolved"

        try:
            status_code = int(response.status) if response else 200
        except Exception:
            status_code = 200

        if status_code in {404, 410}:
            return "closed"
        if status_code >= 400:
            return "unresolved"

        try:
            body_text = self._page.locator("body").evaluate(
                """
                body => {
                  const clone = body.cloneNode(true);
                  clone.querySelectorAll(
                    '[data-qa="vacancy-description"], .vacancy-description, script, style'
                  ).forEach(node => node.remove());
                  return (clone.innerText || '').toLowerCase();
                }
                """
            )
        except Exception:
            body_text = ""

        normalized = " ".join(str(body_text or "").split()).replace("ё", "е")
        if any(
            marker.replace("ё", "е") in normalized
            for marker in ARCHIVED_MARKERS
        ):
            return "closed"

        try:
            if self._page.locator('[data-qa="vacancy-title"]').count() > 0:
                return "active"
        except Exception:
            pass

        return "unresolved"


def _read_inputs() -> tuple[str, str]:
    if not CANDIDATE_FACTS_PATH.exists():
        raise FileNotFoundError(
            f"candidate facts not found: {CANDIDATE_FACTS_PATH}"
        )
    if not VISIBLE_RESUME_PATH.exists():
        raise FileNotFoundError(
            f"visible CLEAN resume not found: {VISIBLE_RESUME_PATH}"
        )

    candidate_facts = CANDIDATE_FACTS_PATH.read_text(
        encoding="utf-8",
        errors="replace",
    )
    visible_resume = VISIBLE_RESUME_PATH.read_text(
        encoding="utf-8",
        errors="replace",
    )
    return candidate_facts, visible_resume


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create or resume an isolated 7-day CLEAN full-rescore snapshot. "
            "Legacy Evaluation score/decision/cover are never evaluator inputs."
        )
    )
    parser.add_argument("--create-run", action="store_true")
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--max-runtime-minutes",
        type=float,
        default=DEFAULT_MAX_RUNTIME_MINUTES,
        help=(
            "Stop taking new items when the batch runtime budget is nearly "
            "exhausted. Use 0 to disable. Default: 20 minutes."
        ),
    )
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--skip-availability", action="store_true")
    parser.add_argument(
        "--headed-availability",
        action="store_true",
        help="Show Chromium used only for stateless vacancy availability checks.",
    )
    args = parser.parse_args()

    needs_worker_lock = (
        not args.summary_only
        or args.retry_errors
        or args.create_run
    )
    worker_lock = None
    if needs_worker_lock:
        worker_lock = AgentLock(RESCORE_WORKER_LOCK_PATH)
        try:
            worker_lock.__enter__()
        except RuntimeError as exc:
            if str(exc) == "agent_lock_busy":
                print(
                    "[CLEAN RESCORE] worker_lock_busy: "
                    "another CLEAN rescore worker is already running"
                )
                return 7
            raise

    try:
        return _run(args, parser)
    finally:
        if worker_lock is not None:
            worker_lock.__exit__(None, None, None)


def _run(args, parser) -> int:
    init_db()

    run_id = args.run_id
    if args.create_run:
        run = create_rescore_run(
            candidate_profile_version=CANDIDATE_PROFILE_VERSION,
            recruiter_resume_version=RECRUITER_RESUME_VERSION,
            window_days=args.window_days,
            note=(
                "Task 19 7-day full CLEAN rescore; "
                "legacy score/decision/cover excluded"
            ),
        )
        run_id = run.id
        print(
            "[CLEAN RESCORE] snapshot "
            f"run_id={run.id} selected={run.selected_count} "
            f"dataset_hash={run.dataset_hash[:16]} "
            f"window={run.window_from.isoformat()}..{run.window_to.isoformat()}"
        )

    if run_id is None:
        parser.error("--run-id or --create-run is required")

    if args.retry_errors:
        count = retry_errors(run_id)
        print(f"[CLEAN RESCORE] reset_errors={count}")

    if args.summary_only:
        print(
            json.dumps(
                get_rescore_summary(run_id),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    candidate_facts, visible_resume = _read_inputs()
    max_runtime_seconds = (
        None
        if args.max_runtime_minutes <= 0
        else float(args.max_runtime_minutes) * 60.0
    )

    if args.skip_availability:
        result = process_rescore_batch(
            run_id=run_id,
            candidate_facts=candidate_facts,
            recruiter_visible_resume=visible_resume,
            candidate_profile_version=CANDIDATE_PROFILE_VERSION,
            recruiter_resume_version=RECRUITER_RESUME_VERSION,
            limit=args.limit,
            availability_probe=None,
            max_runtime_seconds=max_runtime_seconds,
            item_start_guard_seconds=ITEM_START_GUARD_SECONDS,
        )
    else:
        with StatelessHHAvailability(
            headless=not args.headed_availability
        ) as availability:
            result = process_rescore_batch(
                run_id=run_id,
                candidate_facts=candidate_facts,
                recruiter_visible_resume=visible_resume,
                candidate_profile_version=CANDIDATE_PROFILE_VERSION,
                recruiter_resume_version=RECRUITER_RESUME_VERSION,
                limit=args.limit,
                availability_probe=availability,
                max_runtime_seconds=max_runtime_seconds,
                item_start_guard_seconds=ITEM_START_GUARD_SECONDS,
            )

    print(
        "[CLEAN RESCORE] batch "
        f"run_id={result.run_id} "
        f"batch_processed={result.batch_processed} "
        f"batch_failed={result.batch_failed} "
        f"total={result.processed_count}/{result.selected_count} "
        f"ok={result.ok_count} errors={result.error_count} "
        f"clean_candidates={result.clean_candidate_count} "
        f"status={result.status} "
        f"budget_exhausted={result.budget_exhausted}"
    )
    print(
        json.dumps(
            get_rescore_summary(run_id),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
