import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import apply_dispatcher as dispatcher


class HHCoverLetterNativeRecoveryTests(unittest.TestCase):
    def test_failed_native_attachment_never_sends_letter_as_chat_message(self):
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
                return_value={"confirmed": False},
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
            patch.object(
                dispatcher.hh_worker,
                "set_cover_letter_status",
            ) as set_cover_status,
        ):
            result = dispatcher._hh_attach_post_apply_cover_letter_strict(
                page,
                application,
            )

        self.assertEqual(result, "cover_letter_pending")
        self.assertEqual(submit_letter.call_count, 2)
        chat_delivery.assert_not_called()
        set_status.assert_called_once_with(
            application.id,
            "applied",
            applied=True,
        )
        self.assertEqual(
            set_cover_status.call_args.args[:2],
            (application.id, "failed"),
        )

    def test_native_associated_form_confirmation_marks_letter_sent(self):
        page = MagicMock()
        application = SimpleNamespace(
            id=1753,
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
                side_effect=[
                    {"confirmed": False},
                    {
                        "confirmed": True,
                        "mode": "native-form-submit",
                        "status": 200,
                    },
                ],
            ),
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
            patch.object(
                dispatcher.hh_worker,
                "set_cover_letter_status",
            ) as set_cover_status,
        ):
            result = dispatcher._hh_attach_post_apply_cover_letter_strict(
                page,
                application,
            )

        self.assertEqual(result, "applied")
        set_status.assert_called_once_with(
            application.id,
            "applied",
            applied=True,
        )
        set_cover_status.assert_called_once_with(
            application.id,
            "sent",
        )


if __name__ == "__main__":
    unittest.main()
