import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "telegram_bot.py").read_text(encoding="utf-8")
PATCH = (ROOT / "telegram_bot_pending_patch.py").read_text(encoding="utf-8")
ACCOUNTS = (ROOT / "hh_accounts.py").read_text(encoding="utf-8")


class CleanTelegramBacklogGuardTests(unittest.TestCase):
    def test_new_is_capped_per_account(self) -> None:
        self.assertIn("TELEGRAM_NEW_MAX_CARDS", BOT)
        self.assertIn('int(os.getenv("TELEGRAM_NEW_MAX_CARDS", "20"))', BOT)
        self.assertIn(
            "sent_new + sent_pending + sent_manual >= max_cards",
            PATCH,
        )

    def test_clean_new_uses_activation_cutoff(self) -> None:
        self.assertIn("def account_activated_at", ACCOUNTS)
        self.assertIn('if account.key != "clean":', PATCH)
        self.assertIn(
            "bot_module.account_activated_at(account)",
            PATCH,
        )
        self.assertIn(
            "bot_module.Vacancy.found_at >= cutoff",
            PATCH,
        )

    def test_pending_and_manual_are_isolated_by_account(self) -> None:
        self.assertGreaterEqual(
            PATCH.count(
                "bot_module.Application.account_key == account.key"
            ),
            3,
        )

    def test_new_without_filter_covers_all_apply_accounts(self) -> None:
        self.assertIn(
            "accounts = bot_module.apply_accounts()",
            PATCH,
        )
        self.assertIn(
            "account_key=account.key",
            PATCH,
        )

    def test_callbacks_are_bound_to_application_id(self) -> None:
        self.assertIn(
            'callback_data=f"approve{suffix}:{target_id}"',
            BOT,
        )
        self.assertIn(
            'if action.endswith("_app")',
            BOT,
        )
        self.assertIn(
            "application_id=state.id",
            PATCH,
        )

    def test_stale_buttons_cannot_overwrite_state(self) -> None:
        self.assertIn(
            'allowed_from = {',
            BOT,
        )
        self.assertIn(
            "current_status not in allowed_from[action]",
            BOT,
        )
        self.assertIn(
            "Ничего не меняю.",
            BOT,
        )


if __name__ == "__main__":
    unittest.main()
