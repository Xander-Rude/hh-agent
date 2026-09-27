import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import apply_dispatcher as dispatcher


class HHCoverLetterFailureTests(unittest.TestCase):
    def test_failed_post_apply_attachment_never_sends_letter_to_chat(self):
        page = MagicMock()
        application = SimpleNamespace(
            id=1752,
            cover_letter="Подготовленное сопроводительное письмо",
        )
        field = MagicMock()
        field.input_value.return_value = application.cover_letter
        submit = MagicMock()

        with (
            patch.object(
                dispatcher,
                "_hh_find_post_apply_cover_letter_field",
                return_value=field,
            ),
            patch.object(
                dispatcher,
                "_hh_find_letter_submit_robust",
                return_value=submit,
            ),
            patch.object(
                dispatcher,
                "_hh_submit_post_apply_letter",
            ) as submit_letter,
            patch.object(
                dispatcher,
                "_hh_post_apply_form_still_unsent",
                return_value=True,
            ),
            patch.object(
                dispatcher,
                "_hh_resync_letter_field_for_retry",
                return_value=True,
            ),
            patch.object(
                dispatcher,
                "_hh_deliver_cover_letter_via_chat",
            ) as chat_delivery,
            patch.object(
                dispatcher.hh_worker,
                "detect_manual_required",
                return_value=None,
            ),
            patch.object(
                dispatcher.hh_worker,
                "letter_delivery_confirmed",
                return_value=False,
            ),
            patch.object(
                dispatcher.hh_worker,
                "page_text",
                return_value="резюме доставлено",
            ),
            patch.object(
                dispatcher.hh_worker,
                "set_status",
            ) as set_status,
        ):
            result = dispatcher._hh_attach_post_apply_cover_letter_strict(
                page,
                application,
            )

        self.assertEqual(result, "manual_required")
        self.assertEqual(submit_letter.call_count, 2)
        chat_delivery.assert_not_called()
        set_status.assert_called_once()
        self.assertEqual(set_status.call_args.args[:2], (application.id, "manual_required"))
        self.assertTrue(set_status.call_args.kwargs["applied"])
        self.assertIn(
            "сопроводительное письмо не подтверждено",
            set_status.call_args.kwargs["manual_reason"],
        )

    def test_missing_post_apply_field_never_uses_chat(self):
        page = MagicMock()
        application = SimpleNamespace(
            id=1753,
            cover_letter="Подготовленное сопроводительное письмо",
        )

        with (
            patch.object(
                dispatcher,
                "_hh_find_post_apply_cover_letter_field",
                return_value=None,
            ),
            patch.object(
                dispatcher,
                "_hh_deliver_cover_letter_via_chat",
            ) as chat_delivery,
            patch.object(
                dispatcher.hh_worker,
                "detect_manual_required",
                return_value=None,
            ),
            patch.object(
                dispatcher.hh_worker,
                "set_status",
            ) as set_status,
        ):
            result = dispatcher._hh_attach_post_apply_cover_letter_strict(
                page,
                application,
            )

        self.assertEqual(result, "manual_required")
        chat_delivery.assert_not_called()
        self.assertTrue(set_status.call_args.kwargs["applied"])


if __name__ == "__main__":
    unittest.main()
