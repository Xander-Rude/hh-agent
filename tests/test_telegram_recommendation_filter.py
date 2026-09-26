from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "telegram_bot_pending_patch.py").read_text(encoding="utf-8")
BOT = (ROOT / "telegram_bot.py").read_text(encoding="utf-8")


class TelegramRecommendationFilterTests(unittest.TestCase):
    def test_new_candidates_allow_apply_and_review_decisions(self) -> None:
        self.assertIn('RECOMMENDED_DECISIONS = ("apply", "review")', SOURCE)
        self.assertIn(
            "bot_module.Evaluation.decision.in_(RECOMMENDED_DECISIONS)",
            SOURCE,
        )

    def test_old_notified_cards_are_suppressed_when_latest_decision_is_not_recommended(self) -> None:
        self.assertIn(
            "if evaluation.decision not in RECOMMENDED_DECISIONS:",
            SOURCE,
        )
        self.assertIn("pending_not_recommended += 1", SOURCE)

    def test_external_cards_use_source_label_not_hh_account_label(self) -> None:
        self.assertIn("def notification_scope_label(", BOT)
        self.assertIn('"yandex": "🟡 YANDEX"', BOT)
        self.assertIn('"vk": "🔵 VK"', BOT)
        self.assertIn('"tbank": "🟣 Т-БАНК"', BOT)

    def test_hh_candidates_require_current_account_discovery(self) -> None:
        self.assertIn(
            "bot_module.HhVacancyDiscovery.account_key == account.key",
            SOURCE,
        )
        self.assertGreaterEqual(
            SOURCE.count(".where(has_account_discovery)"),
            3,
        )

    def test_external_manual_required_is_not_resent(self) -> None:
        self.assertIn(
            "Application.telegram_notified_at.is_(None)",
            SOURCE,
        )

    def test_manual_required_copy_is_source_aware(self) -> None:
        self.assertIn(
            '"tbank": "Автоматический отклик на сайте Т-Банка не был завершён."',
            BOT,
        )
        self.assertIn(
            '"ozon": "Автоматический отклик на сайте Ozon не был завершён."',
            BOT,
        )

    def test_summary_is_account_specific(self) -> None:
        self.assertIn(
            "bot_module.account_label(account.key)",
            SOURCE,
        )
        self.assertIn(
            "f\"новых {stats['sent_new']}, \"",
            SOURCE,
        )


if __name__ == "__main__":
    unittest.main()
