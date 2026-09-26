from types import SimpleNamespace
import unittest

from telegram_bot_link_patch import install


class _Session:
    def __init__(self, vacancy):
        self.vacancy = vacancy

    def get(self, model, vacancy_id):
        return self.vacancy

    def close(self):
        pass


class TelegramBotLinkPatchTests(unittest.TestCase):
    def test_modern_native_builder_is_preserved(self) -> None:
        def native_builder(vacancy_id, application_id=None):
            return vacancy_id, application_id

        module = SimpleNamespace(
            build_keyboard=native_builder,
            _vacancy_open_target=object(),
        )

        install(module)

        self.assertIs(module.build_keyboard, native_builder)
        self.assertEqual(module.build_keyboard(10, application_id=77), (10, 77))

    def test_legacy_fallback_accepts_application_id(self) -> None:
        vacancy = SimpleNamespace(
            source="hh",
            url="https://hh.ru/vacancy/10",
            hh_id="10",
        )
        session = _Session(vacancy)
        module = SimpleNamespace(
            SessionLocal=lambda: session,
            Vacancy=object,
        )

        install(module)
        keyboard = module.build_keyboard(10, application_id=77)

        rows = keyboard.inline_keyboard
        self.assertEqual(rows[0][0].callback_data, "approve_app:77")
        self.assertEqual(rows[0][1].callback_data, "skip_app:77")
        self.assertEqual(
            rows[1][0].callback_data,
            "blacklist_company_app:77",
        )


if __name__ == "__main__":
    unittest.main()
