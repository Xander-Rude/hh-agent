import unittest
from unittest.mock import MagicMock, patch

import apply_dispatcher as dispatcher


class _EventPage:
    def __init__(self):
        self._listeners = {"request": [], "response": []}

    def on(self, event, callback):
        self._listeners[event].append(callback)

    def remove_listener(self, event, callback):
        if callback in self._listeners[event]:
            self._listeners[event].remove(callback)

    def wait_for_timeout(self, _milliseconds):
        return None

    def emit(self, event, value):
        for callback in list(self._listeners[event]):
            callback(value)


class _Request:
    def __init__(self, payload):
        self.method = "POST"
        self.url = "https://chatik.hh.ru/chatik/api/save"
        self.post_data_json = payload


class _Response:
    def __init__(self, request, status=200):
        self.request = request
        self.status = status
        self.url = request.url


class HHChatikCoverLetterEditModeTests(unittest.TestCase):
    def setUp(self):
        self.letter = (
            "Здравствуйте!\n"
            "Мой основной профиль - управление IT-проектами и delivery полного цикла.\n"
            "С уважением,\nАлександр Руденко"
        )
        self.page = _EventPage()
        self.context = MagicMock()
        self.composer = MagicMock()
        self.composer.input_value.side_effect = ["", self.letter]
        self.action = MagicMock()
        self.message = MagicMock()
        self.message.get_attribute.return_value = "chatik-chat-message-777"
        self.send = MagicMock()
        self.send.is_enabled.return_value = True

    def _missing_snapshot(self):
        return {
            "state": "missing",
            "reason": "Без сопроводительного письма",
            "message": self.message,
            "action": self.action,
        }

    def _wire_native_save(self, *, message_id=777, text=None, status=200):
        payload = {
            "messageId": message_id,
            "text": self.letter if text is None else text,
        }

        def click(**_kwargs):
            request = _Request(payload)
            self.page.emit("request", request)
            self.page.emit("response", _Response(request, status=status))

        self.send.click.side_effect = click

    def test_native_preview_reuses_composer_and_requires_exact_save_payload(self):
        self._wire_native_save()

        with (
            patch.object(
                dispatcher,
                "_hh_verify_response_card",
                side_effect=[
                    (self._missing_snapshot(), self.context, self.composer),
                    (
                        {
                            "state": "confirmed",
                            "reason": "письмо находится внутри карточки отклика",
                        },
                        self.context,
                        self.composer,
                    ),
                ],
            ),
            patch.object(
                dispatcher,
                "_hh_activate_cover_mode",
                return_value=True,
            ),
            patch.object(
                dispatcher,
                "_hh_cover_mode_active",
                side_effect=[True, True],
            ),
            patch.object(
                dispatcher,
                "_hh_first_visible_in_context",
                return_value=self.send,
            ),
        ):
            ok, reason = dispatcher._hh_attach_cover_letter_via_response_card(
                self.page,
                self.letter,
            )

        self.assertTrue(ok)
        self.assertIn("Chatik edit mode", reason)
        self.composer.fill.assert_called_once_with(self.letter)
        self.send.click.assert_called_once()
        self.assertEqual(self.page._listeners["request"], [])
        self.assertEqual(self.page._listeners["response"], [])

    def test_missing_native_preview_never_clicks_ordinary_send(self):
        with (
            patch.object(
                dispatcher,
                "_hh_verify_response_card",
                return_value=(
                    self._missing_snapshot(),
                    self.context,
                    self.composer,
                ),
            ),
            patch.object(
                dispatcher,
                "_hh_activate_cover_mode",
                return_value=True,
            ),
            patch.object(
                dispatcher,
                "_hh_cover_mode_active",
                return_value=False,
            ),
        ):
            ok, reason = dispatcher._hh_attach_cover_letter_via_response_card(
                self.page,
                self.letter,
            )

        self.assertFalse(ok)
        self.assertIn("preview", reason)
        self.composer.fill.assert_not_called()
        self.send.click.assert_not_called()

    def test_wrong_save_message_id_is_not_confirmed(self):
        self._wire_native_save(message_id=999)

        with (
            patch.object(
                dispatcher,
                "_hh_verify_response_card",
                return_value=(
                    self._missing_snapshot(),
                    self.context,
                    self.composer,
                ),
            ),
            patch.object(
                dispatcher,
                "_hh_activate_cover_mode",
                return_value=True,
            ),
            patch.object(
                dispatcher,
                "_hh_cover_mode_active",
                side_effect=[True, True],
            ),
            patch.object(
                dispatcher,
                "_hh_first_visible_in_context",
                return_value=self.send,
            ),
        ):
            ok, reason = dispatcher._hh_attach_cover_letter_via_response_card(
                self.page,
                self.letter,
            )

        self.assertFalse(ok)
        self.assertIn("expected native /save payload", reason)

    def test_failed_save_response_is_not_confirmed(self):
        self._wire_native_save(status=403)

        with (
            patch.object(
                dispatcher,
                "_hh_verify_response_card",
                return_value=(
                    self._missing_snapshot(),
                    self.context,
                    self.composer,
                ),
            ),
            patch.object(
                dispatcher,
                "_hh_activate_cover_mode",
                return_value=True,
            ),
            patch.object(
                dispatcher,
                "_hh_cover_mode_active",
                side_effect=[True, True],
            ),
            patch.object(
                dispatcher,
                "_hh_first_visible_in_context",
                return_value=self.send,
            ),
        ):
            ok, reason = dispatcher._hh_attach_cover_letter_via_response_card(
                self.page,
                self.letter,
            )

        self.assertFalse(ok)
        self.assertIn("did not return success", reason)


if __name__ == "__main__":
    unittest.main()
