from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Application, Base, Evaluation, Vacancy
import telegram_bot_pending_patch as pending_patch


class ExternalManualRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()
        self.bot_module = SimpleNamespace(
            Application=Application,
            Vacancy=Vacancy,
            Evaluation=Evaluation,
            select=select,
            datetime=datetime,
            TELEGRAM_NEW_MAX_CARDS=20,
            MIN_SCORE_TO_NOTIFY=50,
            _STATUS_EXCLUDED_SOURCES=frozenset({"ozon", "tbank"}),
            build_manual_required_message=lambda vacancy, state: f"manual:{state.id}",
            build_manual_required_keyboard=lambda vacancy, state: f"manual_done_app:{state.id}",
        )
        self.sent = []

        async def send_message(**kwargs):
            self.sent.append(kwargs)
            return SimpleNamespace(chat_id=123, message_id=100 + len(self.sent))

        self.context = SimpleNamespace(bot=SimpleNamespace(send_message=send_message))
        self.sleep_patch = patch.object(pending_patch.asyncio, "sleep", new=AsyncMock())
        self.sleep_patch.start()
        self.env_patch = patch.dict("os.environ", {"OZON_ENABLED": "false"})
        self.env_patch.start()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.sleep_patch.stop()
        self.session.close()
        self.engine.dispose()

    def add_application(self, source="vk", status="manual_required", *, notified=True):
        vacancy = Vacancy(
            hh_id=f"{source}:{self.session.query(Vacancy).count() + 1}",
            source=source,
            title="Technical Product Manager",
            url="https://example.test/vacancy/1",
            description="Fixture",
        )
        self.session.add(vacancy)
        self.session.flush()
        state = Application(
            vacancy_id=vacancy.id,
            account_key="old",
            status=status,
            telegram_chat_id="123" if notified else None,
            telegram_message_id=76 if notified else None,
            telegram_notified_at=datetime(2026, 9, 29) if notified else None,
        )
        self.session.add(state)
        self.session.commit()
        return state

    async def deliver(self):
        return await pending_patch._deliver_external(
            self.bot_module, self.context, self.session, target_chat_id=123,
        )

    async def test_previously_notified_manual_card_repeats_until_completed(self) -> None:
        state = self.add_application()
        for message_id in (101, 102):
            stats = await self.deliver()
            self.assertEqual(stats["sent_manual"], 1)
            self.assertEqual(state.status, "manual_required")
            self.assertIsNone(state.applied_at)
            self.assertEqual(state.telegram_message_id, message_id)
            self.assertEqual(self.sent[-1]["text"], f"manual:{state.id}")
            self.assertEqual(self.sent[-1]["reply_markup"], f"manual_done_app:{state.id}")

        state.status = "applied"
        state.applied_at = datetime.utcnow()
        self.session.commit()
        self.assertEqual((await self.deliver())["sent_manual"], 0)
        self.assertEqual(len(self.sent), 2)

    async def test_disabled_and_hh_sources_stay_out_of_external_manual_queue(self) -> None:
        vk = self.add_application(notified=False)
        for source in ("hh", "tbank", "ozon"):
            self.add_application(source, notified=False)
        self.assertEqual((await self.deliver())["sent_manual"], 1)
        self.assertEqual([item["text"] for item in self.sent], [f"manual:{vk.id}"])

    async def test_other_application_states_are_not_manual_cards(self) -> None:
        for status in ("approved", "applying", "applied", "rejected"):
            self.add_application(status=status)
        self.assertEqual((await self.deliver())["sent_manual"], 0)
        self.assertEqual(self.sent, [])

    async def test_manual_repeats_respect_card_limit(self) -> None:
        self.add_application()
        newest = self.add_application()
        self.bot_module.TELEGRAM_NEW_MAX_CARDS = 1
        stats = await self.deliver()
        self.assertEqual(stats["sent_manual"], 1)
        self.assertTrue(stats["limit_reached"])
        self.assertEqual(self.sent[0]["text"], f"manual:{newest.id}")

    async def test_failed_delivery_keeps_existing_binding_and_manual_status(self) -> None:
        state = self.add_application()
        original_notified_at = state.telegram_notified_at
        self.context.bot.send_message = AsyncMock(side_effect=RuntimeError("fixture failure"))
        stats = await self.deliver()
        self.assertEqual(stats["failed_manual"], 1)
        self.assertEqual(stats["sent_manual"], 0)
        self.assertEqual(state.status, "manual_required")
        self.assertEqual(state.telegram_message_id, 76)
        self.assertEqual(state.telegram_notified_at, original_notified_at)


if __name__ == "__main__":
    unittest.main()
