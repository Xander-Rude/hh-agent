from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = (ROOT / "background_pipeline.py").read_text(encoding="utf-8")
DISPATCHER = (ROOT / "apply_dispatcher.py").read_text(encoding="utf-8")
GUARD = (ROOT / "hh_session_guard.py").read_text(encoding="utf-8")
HH_BROWSER = (ROOT / "hh_browser.py").read_text(encoding="utf-8")
CHECK_SCRIPT = (ROOT / "check_hh_session.py").read_text(encoding="utf-8")
COLLECTOR = (ROOT / "hh_collect.py").read_text(encoding="utf-8")


class HHSessionGuardProductionTests(unittest.TestCase):
    def test_guard_reuses_shared_hh_auth_check(self) -> None:
        self.assertIn(
            "from hh_browser import PROFILE_DIR, RESUMES_URL, hh_is_authenticated",
            GUARD,
        )
        self.assertIn("authenticated = hh_is_authenticated(page)", GUARD)

    def test_stale_hhtoken_is_not_treated_as_authenticated(self) -> None:
        self.assertIn("stale ``hhtoken`` cookie is NOT enough", HH_BROWSER)
        self.assertNotIn('if "hhtoken" in hh_cookie_names(page):\n        return True', HH_BROWSER)
        self.assertIn("return False", HH_BROWSER)

    def test_manual_checker_tolerates_user_closing_browser(self) -> None:
        self.assertIn("except PlaywrightError", CHECK_SCRIPT)
        self.assertIn("AUTHENTICATED:", CHECK_SCRIPT)

    def test_pipeline_checks_session_before_hh_collection(self) -> None:
        check_index = PIPELINE.index("session_status = check_hh_session(headless=True)")
        collect_index = PIPELINE.index('run_python(\n                    "hh_collect.py"')
        self.assertLess(check_index, collect_index)
        self.assertIn("if session_status.authenticated:", PIPELINE)
        self.assertIn("force=True", PIPELINE)

    def test_expired_session_does_not_turn_approved_hh_into_manual_required(self) -> None:
        self.assertIn("queue = load_hh_queue()", DISPATCHER)
        self.assertIn("session_status = check_hh_session", DISPATCHER)
        self.assertIn("if not session_status.authenticated:", DISPATCHER)
        self.assertIn("Approved-очередь оставлена без изменений", DISPATCHER)
        auth_block = DISPATCHER.split("if not session_status.authenticated:", 1)[1].split(
            "original_hh_load_queue", 1
        )[0]
        self.assertNotIn("hh_worker.main()", auth_block)

    def test_collector_requires_real_resume_specific_recommendations(self) -> None:
        self.assertIn("from hh_browser import hh_is_authenticated", COLLECTOR)
        self.assertIn("not _is_resume_specific_recommendation_url(href)", COLLECTOR)
        self.assertIn("_has_explicit_home_recommendation_signal", COLLECTOR)
        self.assertIn("Общие поисковые ссылки игнорируются", COLLECTOR)
        self.assertIn("Работа из дома", COLLECTOR)


if __name__ == "__main__":
    unittest.main()
