import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import apply_dispatcher as dispatcher


class HHCoverLetterChatFallbackTests(unittest.TestCase):
    def test_failed_post_apply_attachment_falls_back_to_exact_chat(self):
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
                return_value=(True, "письмо доставлено через чат отклика"),
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
                return_value="вы откликнулись",
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

        self.assertEqual(result, "applied")
        self.assertEqual(submit_letter.call_count, 2)
        chat_delivery.assert_called_once_with(
            page,
            application.cover_letter,
        )
        set_status.assert_called_once_with(
            application.id,
            "applied",
            applied=True,
        )

    def test_chat_failure_keeps_manual_required(self):
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
                return_value=(False, "чат недоступен"),
            ),
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
        self.assertTrue(set_status.call_args.kwargs["applied"])
        self.assertIn(
            "Резервная доставка через чат тоже не удалась",
            set_status.call_args.kwargs["manual_reason"],
        )


if __name__ == "__main__":
    unittest.main()
