import asyncio
from types import SimpleNamespace
import unittest

import telegram_new_background_patch as patch


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeApplication:
    def __init__(self) -> None:
        self.bot_data: dict = {}

    def create_task(self, coroutine, *, name=None):
        return asyncio.create_task(coroutine, name=name)


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, *, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))


class TelegramNewBackgroundTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_handler_returns_while_delivery_keeps_running(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def send_new_vacancies(context, chat_id=None) -> None:
            started.set()
            await release.wait()

        bot_module = SimpleNamespace(send_new_vacancies=send_new_vacancies)
        patch.install(bot_module)

        application = FakeApplication()
        context = SimpleNamespace(application=application, bot=FakeBot())
        message = FakeMessage()
        update = SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(id=12345),
        )

        await bot_module.new_command(update, context)
        await asyncio.wait_for(started.wait(), timeout=1)

        task = application.bot_data[patch.NEW_TASK_KEY]
        self.assertFalse(task.done())
        self.assertIn("в фоне", message.replies[0])

        await bot_module.new_command(update, context)
        self.assertIn("уже выполняется", message.replies[-1])
        self.assertFalse(task.done())

        release.set()
        await asyncio.wait_for(task, timeout=1)
        self.assertNotIn(patch.NEW_TASK_KEY, application.bot_data)
        self.assertNotIn(patch.NEW_CHAT_KEY, application.bot_data)

    async def test_background_failure_is_reported_and_registry_is_cleared(self) -> None:
        async def send_new_vacancies(context, chat_id=None) -> None:
            await asyncio.sleep(0)
            raise RuntimeError("boom")

        bot_module = SimpleNamespace(send_new_vacancies=send_new_vacancies)
        patch.install(bot_module)

        application = FakeApplication()
        telegram_bot = FakeBot()
        context = SimpleNamespace(application=application, bot=telegram_bot)
        message = FakeMessage()
        update = SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(id=67890),
        )

        await bot_module.new_command(update, context)
        task = application.bot_data[patch.NEW_TASK_KEY]
        await asyncio.wait_for(task, timeout=1)

        self.assertNotIn(patch.NEW_TASK_KEY, application.bot_data)
        self.assertEqual(len(telegram_bot.messages), 1)
        self.assertIn("Ошибка при обработке /new", telegram_bot.messages[0][1])


if __name__ == "__main__":
    unittest.main()
