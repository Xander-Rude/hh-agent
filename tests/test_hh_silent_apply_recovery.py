import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import apply_dispatcher as dispatcher
import apply_worker as worker


class HHSilentApplyRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.page = MagicMock()
        self.application = SimpleNamespace(
            id=1674,
            cover_letter="Подготовленное письмо",
        )
        self.vacancy = SimpleNamespace(
            title="Владелец продукта",
            company="МТС Банк",
            url="https://hh.ru/vacancy/137527192",
        )
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(
            patch.object(worker, "set_status")
        )
        self.stack.enter_context(
            patch.object(worker, "detect_manual_required", return_value=None)
        )

    def test_ambiguous_submit_reloads_and_repairs_letter(self):
        self.stack.enter_context(
            patch.object(
                worker,
                "already_applied",
                side_effect=[False, False, True],
            )
        )
        self.stack.enter_context(
            patch.object(
                worker,
                "try_open_preapply_cover_letter",
                return_value=False,
            )
        )
        initial_click = self.stack.enter_context(
            patch.object(worker, "click_initial_apply", return_value=True)
        )
        self.stack.enter_context(
            patch.object(worker, "choose_resume_if_needed")
        )
        self.stack.enter_context(
            patch.object(worker, "fill_cover_letter", return_value=True)
        )
        submit = MagicMock()
        self.stack.enter_context(
            patch.object(worker, "find_final_submit", return_value=submit)
        )
        self.stack.enter_context(
            patch.object(worker, "page_text", return_value="без подтверждения")
        )
        self.stack.enter_context(
            patch.object(
                worker,
                "find_post_apply_cover_letter_trigger",
                side_effect=[None] * 8 + [MagicMock()],
            )
        )
        attach = self.stack.enter_context(
            patch.object(
                worker,
                "attach_post_apply_cover_letter",
                return_value="applied",
            )
        )

        result = worker.process_application(
            self.page,
            self.vacancy,
            self.application,
        )

        self.assertEqual(result, "applied")
        initial_click.assert_called_once_with(self.page)
        submit.click.assert_called_once()
        self.assertEqual(self.page.goto.call_count, 2)
        attach.assert_called_once_with(self.page, self.application)

    def test_preexisting_response_repairs_letter_without_primary_apply(self):
        self.stack.enter_context(
            patch.object(worker, "already_applied", return_value=True)
        )
        trigger = MagicMock()
        self.stack.enter_context(
            patch.object(
                worker,
                "find_post_apply_cover_letter_trigger",
                return_value=trigger,
            )
        )
        attach = self.stack.enter_context(
            patch.object(
                worker,
                "attach_post_apply_cover_letter",
                return_value="applied",
            )
        )
        primary_click = self.stack.enter_context(
            patch.object(worker, "click_initial_apply")
        )

        result = worker.process_application(
            self.page,
            self.vacancy,
            self.application,
        )

        self.assertEqual(result, "applied")
        primary_click.assert_not_called()
        attach.assert_called_once_with(self.page, self.application)

    def test_recovery_never_submits_when_hh_does_not_confirm_response(self):
        self.stack.enter_context(
            patch.object(worker, "already_applied", return_value=False)
        )
        attach = self.stack.enter_context(
            patch.object(
                dispatcher,
                "_hh_attach_post_apply_cover_letter_strict",
            )
        )

        result = dispatcher._recover_hh_manual_required_application(
            self.page,
            self.vacancy,
            self.application,
        )

        self.assertEqual(result, "manual_required")
        attach.assert_not_called()

    def test_recovery_attaches_only_after_existing_response_confirmation(self):
        self.stack.enter_context(
            patch.object(worker, "already_applied", return_value=True)
        )
        self.stack.enter_context(
            patch.object(
                worker,
                "find_post_apply_cover_letter_trigger",
                return_value=MagicMock(),
            )
        )
        attach = self.stack.enter_context(
            patch.object(
                dispatcher,
                "_hh_attach_post_apply_cover_letter_strict",
                return_value="applied",
            )
        )

        result = dispatcher._recover_hh_manual_required_application(
            self.page,
            self.vacancy,
            self.application,
        )

        self.assertEqual(result, "applied")
        attach.assert_called_once_with(self.page, self.application)


if __name__ == "__main__":
    unittest.main()
