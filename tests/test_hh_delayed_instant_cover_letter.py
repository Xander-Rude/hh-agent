import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import apply_dispatcher as dispatcher


class DelayedInstantCoverLetterGuardTests(unittest.TestCase):
    def setUp(self):
        self.page = MagicMock()

    def test_initial_click_waits_for_delayed_applied_state(self):
        with (
            patch.object(dispatcher, "_HH_ORIGINAL_CLICK_INITIAL_APPLY", return_value=True) as click,
            patch.object(dispatcher.hh_worker, "already_applied", side_effect=[False, False, True]),
            patch.object(dispatcher.hh_worker, "find_visible", return_value=None),
        ):
            self.assertTrue(dispatcher._hh_click_initial_apply_with_settle(self.page))

        click.assert_called_once_with(self.page)
        self.assertEqual(self.page.wait_for_timeout.call_count, 2)
        self.page.wait_for_timeout.assert_called_with(250)

    def test_initial_click_does_not_delay_regular_form(self):
        regular_submit = MagicMock()
        with (
            patch.object(dispatcher, "_HH_ORIGINAL_CLICK_INITIAL_APPLY", return_value=True),
            patch.object(dispatcher.hh_worker, "already_applied", return_value=False),
            patch.object(dispatcher.hh_worker, "find_visible", return_value=regular_submit),
        ):
            self.assertTrue(dispatcher._hh_click_initial_apply_with_settle(self.page))

        self.page.wait_for_timeout.assert_not_called()

    def test_final_submit_is_blocked_if_application_arrives_late(self):
        with (
            patch.object(dispatcher.hh_worker, "already_applied", return_value=True),
            patch.object(dispatcher, "_HH_ORIGINAL_FIND_FINAL_SUBMIT") as find_submit,
        ):
            self.assertIsNone(dispatcher._hh_find_final_submit_guarded(self.page))

        find_submit.assert_not_called()

    def test_final_submit_rechecks_after_short_guard_window(self):
        with (
            patch.object(dispatcher.hh_worker, "already_applied", side_effect=[False, True]),
            patch.object(dispatcher, "_HH_ORIGINAL_FIND_FINAL_SUBMIT") as find_submit,
        ):
            self.assertIsNone(dispatcher._hh_find_final_submit_guarded(self.page))

        self.page.wait_for_timeout.assert_called_once_with(200)
        find_submit.assert_not_called()

    def test_regular_final_submit_is_preserved(self):
        submit = MagicMock()
        with (
            patch.object(dispatcher.hh_worker, "already_applied", side_effect=[False, False]),
            patch.object(dispatcher, "_HH_ORIGINAL_FIND_FINAL_SUBMIT", return_value=submit) as find_submit,
        ):
            self.assertIs(dispatcher._hh_find_final_submit_guarded(self.page), submit)

        find_submit.assert_called_once_with(self.page)

    def test_late_sent_application_recovers_to_separate_letter_flow(self):
        vacancy = SimpleNamespace(id=10)
        application = SimpleNamespace(id=1331, cover_letter="Текст письма")
        with (
            patch.object(dispatcher, "_HH_ORIGINAL_PROCESS_APPLICATION", return_value="manual_required"),
            patch.object(dispatcher.hh_worker, "already_applied", return_value=True),
            patch.object(
                dispatcher.hh_worker,
                "attach_post_apply_cover_letter",
                return_value="applied",
            ) as attach,
        ):
            result = dispatcher._hh_process_application_with_late_instant_recovery(
                self.page,
                vacancy,
                application,
            )

        self.assertEqual(result, "applied")
        attach.assert_called_once_with(self.page, application)

    def test_confirmed_regular_result_is_not_reprocessed(self):
        vacancy = SimpleNamespace(id=10)
        application = SimpleNamespace(id=1331, cover_letter="Текст письма")
        with (
            patch.object(dispatcher, "_HH_ORIGINAL_PROCESS_APPLICATION", return_value="applied"),
            patch.object(dispatcher.hh_worker, "already_applied") as already_applied,
            patch.object(dispatcher.hh_worker, "attach_post_apply_cover_letter") as attach,
        ):
            result = dispatcher._hh_process_application_with_late_instant_recovery(
                self.page,
                vacancy,
                application,
            )

        self.assertEqual(result, "applied")
        already_applied.assert_not_called()
        attach.assert_not_called()

    def test_hh_run_restores_worker_functions(self):
        queue = [(SimpleNamespace(id=1), SimpleNamespace(id=2))]
        session_status = SimpleNamespace(authenticated=True, reason="ok", final_url=None)

        original_load_queue = dispatcher.hh_worker.load_queue
        original_click = dispatcher.hh_worker.click_initial_apply
        original_find_submit = dispatcher.hh_worker.find_final_submit
        original_process = dispatcher.hh_worker.process_application

        def assert_guards_are_installed():
            self.assertIs(dispatcher.hh_worker.click_initial_apply, dispatcher._hh_click_initial_apply_with_settle)
            self.assertIs(dispatcher.hh_worker.find_final_submit, dispatcher._hh_find_final_submit_guarded)
            self.assertIs(
                dispatcher.hh_worker.process_application,
                dispatcher._hh_process_application_with_late_instant_recovery,
            )

        with (
            patch.object(dispatcher, "load_hh_queue", return_value=queue),
            patch.object(dispatcher, "check_hh_session", return_value=session_status),
            patch.object(dispatcher.hh_worker, "main", side_effect=assert_guards_are_installed),
        ):
            dispatcher._run_hh_source()

        self.assertIs(dispatcher.hh_worker.load_queue, original_load_queue)
        self.assertIs(dispatcher.hh_worker.click_initial_apply, original_click)
        self.assertIs(dispatcher.hh_worker.find_final_submit, original_find_submit)
        self.assertIs(dispatcher.hh_worker.process_application, original_process)


if __name__ == "__main__":
    unittest.main()
