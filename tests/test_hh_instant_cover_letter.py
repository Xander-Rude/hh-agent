import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import apply_worker as worker
from application_notifications import build_manual_required_message


class InstantCoverLetterTests(unittest.TestCase):
    def setUp(self):
        self.page = MagicMock()
        self.application = SimpleNamespace(id=1331, cover_letter='Текст письма')
        self.vacancy = SimpleNamespace(title='IT', company='Company', url='https://hh.ru/vacancy/1')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.status = self.stack.enter_context(patch.object(worker, 'set_status'))
        self.stack.enter_context(patch.object(worker, 'detect_manual_required', return_value=None))

    def setup_letter(self):
        field = MagicMock()
        field.input_value.return_value = self.application.cover_letter
        self.stack.enter_context(patch.object(worker, 'ensure_cover_letter_field', return_value=field))
        submit = MagicMock()
        self.stack.enter_context(patch.object(worker, 'find_letter_submit', return_value=submit))
        self.page.get_by_text.return_value.all.return_value = []
        return field, submit

    def test_instant_apply_routes_to_separate_attachment(self):
        self.stack.enter_context(patch.object(worker, 'already_applied', side_effect=[False, True]))
        click = self.stack.enter_context(patch.object(worker, 'click_initial_apply', return_value=True))
        attach = self.stack.enter_context(patch.object(worker, 'attach_post_apply_cover_letter', return_value='applied'))
        final = self.stack.enter_context(patch.object(worker, 'find_final_submit'))
        self.assertEqual(worker.process_application(self.page, self.vacancy, self.application), 'applied')
        click.assert_called_once()
        attach.assert_called_once_with(self.page, self.application)
        final.assert_not_called()

    def test_preexisting_application_does_not_send_again(self):
        self.stack.enter_context(patch.object(worker, 'already_applied', return_value=True))
        click = self.stack.enter_context(patch.object(worker, 'click_initial_apply'))
        attach = self.stack.enter_context(patch.object(worker, 'attach_post_apply_cover_letter'))
        self.assertEqual(worker.process_application(self.page, self.vacancy, self.application), 'applied')
        click.assert_not_called()
        attach.assert_not_called()

    def test_empty_letter_blocks_initial_click(self):
        self.application.cover_letter = ' '
        self.stack.enter_context(patch.object(worker, 'already_applied', return_value=False))
        click = self.stack.enter_context(patch.object(worker, 'click_initial_apply'))
        self.assertEqual(worker.process_application(self.page, self.vacancy, self.application), 'manual_required')
        click.assert_not_called()

    def test_old_application_banner_is_not_letter_confirmation(self):
        _, submit = self.setup_letter()
        self.stack.enter_context(patch.object(worker, 'page_text', return_value='отклик отправлен'))
        self.assertEqual(worker.attach_post_apply_cover_letter(self.page, self.application), 'manual_required')
        submit.click.assert_called_once()
        self.assertTrue(self.status.call_args.kwargs['applied'])
        self.assertIn('письмо не подтверждено', self.status.call_args.kwargs['manual_reason'])

    def test_new_letter_confirmation_succeeds(self):
        field, submit = self.setup_letter()
        self.stack.enter_context(patch.object(worker, 'page_text', side_effect=['отклик отправлен', 'сопроводительное письмо приложено']))
        self.assertEqual(worker.attach_post_apply_cover_letter(self.page, self.application), 'applied')
        field.fill.assert_called_once_with(self.application.cover_letter)
        submit.click.assert_called_once()
        self.status.assert_called_once_with(1331, 'applied', applied=True)

    def test_timeout_verifies_without_second_click(self):
        _, submit = self.setup_letter()
        submit.click.side_effect = worker.PlaywrightTimeoutError('timeout')
        self.stack.enter_context(patch.object(worker, 'page_text', side_effect=['отклик отправлен', 'сопроводительное письмо отправлено']))
        self.assertEqual(worker.attach_post_apply_cover_letter(self.page, self.application), 'applied')
        submit.click.assert_called_once()

    def test_field_mismatch_never_submits(self):
        field, submit = self.setup_letter()
        field.input_value.return_value = 'wrong text'
        self.assertEqual(worker.attach_post_apply_cover_letter(self.page, self.application), 'manual_required')
        submit.click.assert_not_called()

    def test_missing_field_preserves_sent_application(self):
        self.stack.enter_context(patch.object(worker, 'ensure_cover_letter_field', return_value=None))
        self.assertEqual(worker.attach_post_apply_cover_letter(self.page, self.application), 'manual_required')
        self.assertTrue(self.status.call_args.kwargs['applied'])

    def test_text_still_in_editor_is_not_confirmation(self):
        self.stack.enter_context(patch.object(worker, 'page_text', return_value='отклик отправлен'))
        item = MagicMock()
        item.is_visible.return_value = True
        item.evaluate.return_value = False
        self.page.get_by_text.return_value.all.return_value = [item]
        self.assertFalse(worker.letter_delivery_confirmed(self.page, 'text', 'отклик отправлен'))
        item.evaluate.return_value = True
        self.assertTrue(worker.letter_delivery_confirmed(self.page, 'text', 'отклик отправлен'))

    def test_preapply_letter_form_avoids_instant_apply_click(self):
        self.stack.enter_context(patch.object(worker, 'already_applied', return_value=False))
        preapply = self.stack.enter_context(
            patch.object(worker, 'try_open_preapply_cover_letter', return_value=True)
        )
        click = self.stack.enter_context(patch.object(worker, 'click_initial_apply'))
        self.stack.enter_context(patch.object(worker, 'choose_resume_if_needed'))
        fill = self.stack.enter_context(patch.object(worker, 'fill_cover_letter', return_value=True))
        submit = MagicMock()
        self.stack.enter_context(patch.object(worker, 'find_final_submit', return_value=submit))
        self.stack.enter_context(patch.object(worker, 'page_text', return_value='отклик отправлен'))

        self.assertEqual(
            worker.process_application(self.page, self.vacancy, self.application),
            'applied',
        )
        preapply.assert_called_once_with(self.page)
        click.assert_not_called()
        fill.assert_called_once_with(self.page, self.application.cover_letter)
        submit.click.assert_called_once()

    def test_regular_form_flow_still_submits_with_letter(self):
        self.stack.enter_context(patch.object(worker, 'already_applied', return_value=False))
        self.stack.enter_context(patch.object(worker, 'click_initial_apply', return_value=True))
        self.stack.enter_context(patch.object(worker, 'choose_resume_if_needed'))
        fill = self.stack.enter_context(patch.object(worker, 'fill_cover_letter', return_value=True))
        submit = MagicMock()
        self.stack.enter_context(patch.object(worker, 'find_final_submit', return_value=submit))
        self.stack.enter_context(patch.object(worker, 'page_text', return_value='отклик отправлен'))
        self.assertEqual(worker.process_application(self.page, self.vacancy, self.application), 'applied')
        fill.assert_called_once_with(self.page, self.application.cover_letter)
        submit.click.assert_called_once()

    def test_partial_notification_does_not_claim_unsent(self):
        text = build_manual_required_message(vacancy_title='IT', company='Company', application_id=1331,
                                             reason='Письмо не подтверждено', application_sent=True)
        self.assertIn('Отклик уже отправлен', text)
        self.assertNotIn('не считается отправленным', text)
