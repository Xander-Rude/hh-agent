import os
from pathlib import Path
import unittest
from unittest.mock import patch

from hh_browser import hh_browser_context_options


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = (ROOT / "background_pipeline.py").read_text(encoding="utf-8")
DISPATCHER = (ROOT / "apply_dispatcher.py").read_text(encoding="utf-8")
GUARD = (ROOT / "hh_session_guard.py").read_text(encoding="utf-8")
HH_BROWSER = (ROOT / "hh_browser.py").read_text(encoding="utf-8")
CHECK_SCRIPT = (ROOT / "check_hh_session.py").read_text(encoding="utf-8")
HEADLESS_RUNTIME_FILES = (
    "hh_session_guard.py",
    "hh_collect.py",
    "apply_worker.py",
    "response_sync_worker.py",
    "resume_raise_worker_v2.py",
    "resume_telemetry_worker.py",
)


class HHSessionGuardProductionTests(unittest.TestCase):
    def test_headless_hh_context_uses_normal_chrome_user_agent(self) -> None:
        options = hh_browser_context_options(headless=True)
        self.assertTrue(options["headless"])
        self.assertIn("Chrome/", options["user_agent"])
        self.assertNotIn("HeadlessChrome", options["user_agent"])

    def test_headful_hh_context_preserves_browser_user_agent(self) -> None:
        options = hh_browser_context_options(headless=False)
        self.assertFalse(options["headless"])
        self.assertNotIn("user_agent", options)

    def test_headless_user_agent_can_be_overridden(self) -> None:
        with patch.dict(
            os.environ,
            {"HH_HEADLESS_USER_AGENT": "Custom Chrome UA"},
            clear=False,
        ):
            options = hh_browser_context_options(headless=True)
        self.assertEqual(options["user_agent"], "Custom Chrome UA")

    def test_all_live_hh_headless_contexts_use_shared_options(self) -> None:
        for relative_path in HEADLESS_RUNTIME_FILES:
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            self.assertIn("hh_browser_context_options", source, msg=relative_path)

    def test_guard_reuses_shared_hh_auth_check(self) -> None:
        self.assertIn("from hh_browser import RESUMES_URL, hh_is_authenticated", GUARD)
        self.assertIn("authenticated = hh_is_authenticated(page)", GUARD)
        self.assertIn("identity_verified", GUARD)
        self.assertIn("account_resume_id", GUARD)

    def test_stale_hhtoken_is_not_treated_as_authenticated(self) -> None:
        self.assertIn("stale ``hhtoken`` cookie is NOT enough", HH_BROWSER)
        self.assertNotIn("if \"hhtoken\" in hh_cookie_names(page):\n        return True", HH_BROWSER)
        self.assertIn("return False", HH_BROWSER)

    def test_manual_checker_tolerates_user_closing_browser(self) -> None:
        self.assertIn("except PlaywrightError", CHECK_SCRIPT)
        self.assertIn("AUTHENTICATED:", CHECK_SCRIPT)

    def test_pipeline_checks_old_identity_before_old_profile_collection(self) -> None:
        check_index = PIPELINE.index("session_status = check_hh_session(")
        collect_index = PIPELINE.index('collect_code = _run_hh_collect_with_retry("old")')
        self.assertLess(check_index, collect_index)
        self.assertIn('account="old"', PIPELINE)
        self.assertIn("session_status.identity_verified", PIPELINE)
        self.assertIn('"hh_collect_optimized.py"', PIPELINE)
        self.assertIn("force=True", PIPELINE)

    def test_clean_identity_is_checked_before_clean_collection(self) -> None:
        clean_check = PIPELINE.index(
            'clean_session_status = check_hh_session('
        )
        clean_collect = PIPELINE.index(
            'clean_collect_code = _run_hh_collect_with_retry("clean")'
        )
        self.assertLess(clean_check, clean_collect)
        clean_block = PIPELINE[clean_check:clean_collect]
        self.assertIn('account="clean"', clean_block)
        self.assertIn("clean_session_status.identity_verified", clean_block)

    def test_old_collect_failure_does_not_abort_clean_collection(self) -> None:
        failure_index = PIPELINE.index(
            '"OLD hh_collect_optimized.py failed "'
        )
        clean_check = PIPELINE.index(
            'clean_session_status = check_hh_session('
        )
        failure_block = PIPELINE[failure_index:clean_check]
        self.assertNotIn("return collect_code", failure_block)

    def test_expired_or_wrong_session_does_not_touch_approved_queue(self) -> None:
        self.assertIn("queue = load_hh_queue()", DISPATCHER)
        self.assertIn("session_status = check_hh_session", DISPATCHER)
        self.assertIn("not session_status.authenticated", DISPATCHER)
        self.assertIn("not session_status.identity_verified", DISPATCHER)
        self.assertIn("Approved-очередь оставлена без изменений", DISPATCHER)
        auth_block = DISPATCHER.split(
            "session_status = check_hh_session",
            1,
        )[1].split("original_hh_load_queue", 1)[0]
        self.assertNotIn("hh_worker.main()", auth_block)


if __name__ == "__main__":
    unittest.main()
