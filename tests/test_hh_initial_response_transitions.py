"""HH initial response route regressions: 3581, 3593, 3596."""
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import apply_worker as worker


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.page = MagicMock()
        self.application = SimpleNamespace(
            id=3593, account_key=worker.ACTIVE_ACCOUNT.key,
            cover_letter="Точное письмо", selected_resume_id=None
        )
        self.vacancy = SimpleNamespace(
            id=11202, title="PM", company="HH",
            url="https://hh.ru/vacancy/138646314"
        )
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.status = self.stack.enter_context(patch.object(worker, "set_status"))
        for name, result in (
            ("already_applied", False),
            ("try_open_preapply_cover_letter", False),
            ("captcha_reason_from_page", None),
            ("find_relocation_warning_confirm", None),
            ("wait_for_post_apply_transition", False),
            ("find_visible", None),
            ("detect_manual_required", None),
            ("visible_response_error", None),
        ):
            self.stack.enter_context(patch.object(worker, name, return_value=result))

    def guard(self, **updates):
        state = dict(post_seen=True, letter_verified=True, letter_injected=True,
                     blocked=False, http_status=None, request_finished=False)
        state.update(updates)
        return state

    def test_3593_unconfirmed_post_never_fills_again(self):
        self.stack.enter_context(patch.object(
            worker, "click_initial_apply_with_letter",
            return_value=(True, self.guard())))
        recovery = self.stack.enter_context(patch.object(
            worker, "recover_ambiguous_application", return_value=None))
        fill = self.stack.enter_context(patch.object(worker, "fill_cover_letter"))
        self.assertEqual(worker.process_application(
            self.page, self.vacancy, self.application), "manual_required")
        recovery.assert_called_once()
        fill.assert_not_called()
        self.assertIn("Повторная отправка запрещена",
                      self.status.call_args.kwargs["manual_reason"])

    def test_3596_recovered_post_no_second_submit(self):
        self.application.id = 3596
        self.stack.enter_context(patch.object(
            worker, "click_initial_apply_with_letter",
            return_value=(True, self.guard(http_status=200))))
        recovery = self.stack.enter_context(patch.object(
            worker, "recover_ambiguous_application", return_value="applied"))
        fill = self.stack.enter_context(patch.object(worker, "fill_cover_letter"))
        self.assertEqual(worker.process_application(
            self.page, self.vacancy, self.application), "applied")
        recovery.assert_called_once()
        fill.assert_not_called()

    def test_3581_relocation_confirm_still_guarded(self):
        self.application.id = 3581
        confirm = MagicMock()
        self.stack.enter_context(patch.object(
            worker, "find_relocation_warning_confirm", return_value=confirm))
        self.stack.enter_context(patch.object(
            worker, "page_text", return_value="Формат работы: удалённо или гибрид"))
        self.stack.enter_context(patch.object(
            worker, "click_initial_apply_with_letter",
            return_value=(True, self.guard(post_seen=False, letter_verified=False))))
        second = self.stack.enter_context(patch.object(
            worker, "_guard_hh_response_post",
            side_effect=lambda _p, _letter, action: (action(), self.guard())))
        self.stack.enter_context(patch.object(
            worker, "recover_ambiguous_application", return_value="applied"))
        self.assertEqual(worker.process_application(
            self.page, self.vacancy, self.application), "applied")
        second.assert_called_once()
        confirm.click.assert_called_once()

    def test_3581_overseas_office_requires_decision(self):
        confirm = MagicMock()
        self.stack.enter_context(patch.object(
            worker, "find_relocation_warning_confirm", return_value=confirm))
        self.stack.enter_context(patch.object(
            worker, "page_text", return_value="Формат работы: в офисе"))
        self.stack.enter_context(patch.object(
            worker, "click_initial_apply_with_letter",
            return_value=(True, self.guard(post_seen=False))))
        second = self.stack.enter_context(patch.object(worker, "_guard_hh_response_post"))
        self.assertEqual(worker.process_application(
            self.page, self.vacancy, self.application), "manual_required")
        confirm.click.assert_not_called()
        second.assert_not_called()

    def test_post_that_returns_questionnaire_enters_hhq(self):
        self.stack.enter_context(patch.object(
            worker, "click_initial_apply_with_letter",
            return_value=(True, self.guard())))
        self.stack.enter_context(patch.object(
            worker, "detect_manual_required", return_value="вопросы работодателя"))
        draft = self.stack.enter_context(patch.object(
            worker, "handle_hh_questionnaire", return_value="manual_required"))
        recovery = self.stack.enter_context(
            patch.object(worker, "recover_ambiguous_application"))
        self.assertEqual(worker.process_application(
            self.page, self.vacancy, self.application), "manual_required")
        draft.assert_called_once()
        recovery.assert_not_called()


    def test_forbid_invisible_resume_visibility_mutation(self):
        boundary = "----WebKitFormBoundaryV"
        content_type = "multipart/form-data; boundary=" + boundary
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="vacancy_id"\r\n\r\n'
            "138646314\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="mark_applicant_visible_in_vacancy_country"\r\n\r\n'
            "true\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")
        route = MagicMock()
        route.request.method = "POST"
        route.request.headers = {"content-type": content_type}
        route.request.post_data_buffer = body
        page = MagicMock()

        def action():
            callback = page.route.call_args.args[1]
            callback(route)
            return True

        _result, outcome = worker._guard_hh_response_post(
            page, "Точное письмо", action
        )
        self.assertTrue(outcome["blocked"])
        self.assertIn("visibility", outcome["error"])
        route.abort.assert_called_once_with("blockedbyclient")
        route.continue_.assert_not_called()

if __name__ == "__main__":
    unittest.main()
