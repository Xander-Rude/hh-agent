import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import apply_dispatcher as dispatcher


class HHResponseCardRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.page = MagicMock()
        self.page.url = "https://hh.ru/vacancy/1"
        self.application = SimpleNamespace(
            id=2067,
            cover_letter=(
                "Здравствуйте!\n"
                "Мой основной профиль - управление IT-проектами и delivery полного цикла.\n"
                "С уважением,\nАлександр Руденко"
            ),
        )

    def test_already_confirmed_response_card_never_submits_again(self):
        with (
            patch.object(
                dispatcher,
                "_hh_verify_response_card",
                return_value=(
                    {
                        "state": "confirmed",
                        "reason": "письмо находится внутри карточки отклика",
                    },
                    MagicMock(),
                    MagicMock(),
                ),
            ),
            patch.object(
                dispatcher,
                "_hh_submit_post_apply_letter",
            ) as submit_letter,
            patch.object(
                dispatcher,
                "_hh_attach_cover_letter_via_response_card",
            ) as response_card_send,
            patch.object(
                dispatcher.hh_worker,
                "set_status",
            ) as set_status,
            patch.object(
                dispatcher.hh_worker,
                "set_cover_letter_status",
            ) as cover_status,
        ):
            result = dispatcher._hh_attach_post_apply_cover_letter_strict(
                self.page,
                self.application,
            )

        self.assertEqual(result, "applied")
        submit_letter.assert_not_called()
        response_card_send.assert_not_called()
        cover_status.assert_called_once_with(
            self.application.id,
            "confirmed",
        )
        set_status.assert_called_once_with(
            self.application.id,
            "applied",
            applied=True,
        )

    def test_native_ui_click_is_single_then_response_card_fallback(self):
        field = MagicMock()
        field.input_value.return_value = self.application.cover_letter
        submit = MagicMock()

        with (
            patch.object(
                dispatcher,
                "_hh_verify_response_card",
                side_effect=[
                    (
                        {
                            "state": "missing",
                            "reason": "Без сопроводительного письма",
                        },
                        MagicMock(),
                        MagicMock(),
                    ),
                    (
                        {
                            "state": "missing",
                            "reason": "Без сопроводительного письма",
                        },
                        MagicMock(),
                        MagicMock(),
                    ),
                ],
            ),
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
                return_value={"mode": "click", "confirmed": False},
            ) as native_submit,
            patch.object(
                dispatcher,
                "_hh_attach_cover_letter_via_response_card",
                return_value=(
                    True,
                    "письмо подтверждено внутри карточки отклика",
                ),
            ) as response_card_send,
            patch.object(
                dispatcher.hh_worker,
                "set_status",
            ) as set_status,
            patch.object(
                dispatcher.hh_worker,
                "set_cover_letter_status",
            ) as cover_status,
        ):
            result = dispatcher._hh_attach_post_apply_cover_letter_strict(
                self.page,
                self.application,
            )

        self.assertEqual(result, "applied")
        native_submit.assert_called_once_with(
            self.page,
            submit,
            fallback=False,
        )
        response_card_send.assert_called_once_with(
            self.page,
            self.application.cover_letter,
        )
        self.assertEqual(
            cover_status.call_args.args[:2],
            (self.application.id, "confirmed"),
        )
        self.assertEqual(
            set_status.call_args.args[:2],
            (self.application.id, "applied"),
        )

    def test_missing_native_popup_can_recover_through_response_card(self):
        with (
            patch.object(
                dispatcher,
                "_hh_verify_response_card",
                side_effect=[
                    (
                        {
                            "state": "missing",
                            "reason": "Без сопроводительного письма",
                        },
                        MagicMock(),
                        MagicMock(),
                    ),
                    (
                        {
                            "state": "missing",
                            "reason": "Без сопроводительного письма",
                        },
                        MagicMock(),
                        MagicMock(),
                    ),
                ],
            ),
            patch.object(
                dispatcher,
                "_hh_find_post_apply_cover_letter_field",
                return_value=None,
            ),
            patch.object(
                dispatcher,
                "_hh_attach_cover_letter_via_response_card",
                return_value=(True, "response-card flow confirmed"),
            ) as response_card_send,
            patch.object(
                dispatcher.hh_worker,
                "set_status",
            ),
            patch.object(
                dispatcher.hh_worker,
                "set_cover_letter_status",
            ) as cover_status,
        ):
            result = dispatcher._hh_attach_post_apply_cover_letter_strict(
                self.page,
                self.application,
            )

        self.assertEqual(result, "applied")
        response_card_send.assert_called_once()
        self.assertEqual(
            cover_status.call_args.args[:2],
            (self.application.id, "confirmed"),
        )

    def test_unverified_response_card_never_sends_ordinary_chat_message(self):
        with (
            patch.object(
                dispatcher,
                "_hh_verify_response_card",
                side_effect=[
                    (
                        {
                            "state": "missing",
                            "reason": "Без сопроводительного письма",
                        },
                        MagicMock(),
                        MagicMock(),
                    ),
                    (
                        {
                            "state": "missing",
                            "reason": "Без сопроводительного письма",
                        },
                        MagicMock(),
                        MagicMock(),
                    ),
                ],
            ),
            patch.object(
                dispatcher,
                "_hh_find_post_apply_cover_letter_field",
                return_value=None,
            ),
            patch.object(
                dispatcher,
                "_hh_attach_cover_letter_via_response_card",
                return_value=(
                    False,
                    "режим сопроводительного письма не подтверждён",
                ),
            ),
            patch.object(
                dispatcher.hh_worker,
                "set_status",
            ) as set_status,
            patch.object(
                dispatcher.hh_worker,
                "set_cover_letter_status",
            ) as cover_status,
        ):
            result = dispatcher._hh_attach_post_apply_cover_letter_strict(
                self.page,
                self.application,
            )

        self.assertEqual(result, "applied")
        set_status.assert_called_once_with(
            self.application.id,
            "applied",
            applied=True,
        )
        self.assertEqual(
            cover_status.call_args.args[:2],
            (self.application.id, "needs_manual"),
        )
        self.assertTrue(cover_status.call_args.kwargs["notify"])


if __name__ == "__main__":
    unittest.main()
