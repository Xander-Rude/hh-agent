import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "telegram_bot.py").read_text(encoding="utf-8")
PATCH = (ROOT / "telegram_bot_pending_patch.py").read_text(encoding="utf-8")
ACCOUNTS = (ROOT / "hh_accounts.py").read_text(encoding="utf-8")


class CleanTelegramBacklogGuardTests(unittest.TestCase):
    def test_new_is_capped(self) -> None:
        self.assertIn('TELEGRAM_NEW_MAX_CARDS', BOT)
        self.assertIn('int(os.getenv("TELEGRAM_NEW_MAX_CARDS", "20"))', BOT)
        self.assertIn('>= max_cards', PATCH)
        self.assertIn('Лимит карточек за вызов', PATCH)

    def test_clean_new_uses_activation_cutoff(self) -> None:
        self.assertIn('def account_activated_at', ACCOUNTS)
        self.assertIn('active_new_vacancy_cutoff()', PATCH)
        self.assertIn(
            'bot_module.Vacancy.found_at >= clean_cutoff',
            PATCH,
        )

    def test_pending_and_manual_are_isolated_by_account(self) -> None:
        self.assertGreaterEqual(
            PATCH.count(
                'bot_module.Application.account_key == active_account.key'
            ),
            2,
        )

    def test_old_card_cannot_be_moved_to_clean(self) -> None:
        self.assertIn(
            'state_account != active_account.key',
            BOT,
        )
        self.assertIn(
            'В {account_label(active_account.key)} не переношу.',
            BOT,
        )


if __name__ == "__main__":
    unittest.main()
