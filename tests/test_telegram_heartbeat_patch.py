from __future__ import annotations

import asyncio
import types
import unittest

import telegram_heartbeat_patch


class FakeApplication:
    def __init__(self) -> None:
        self.bot_data: dict[str, object] = {}


class TelegramHeartbeatPatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_heartbeat_task_is_owned_without_application_create_task(self) -> None:
        callbacks: dict[str, object] = {}
        touches: list[str] = []

        class FakeBuilder:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def token(self, *args, **kwargs):
                return self

            def post_init(self, callback):
                callbacks["post_init"] = callback
                return self

            def post_shutdown(self, callback):
                callbacks["post_shutdown"] = callback
                return self

            def build(self):
                return FakeApplication()

        module = types.SimpleNamespace(
            ApplicationBuilder=FakeBuilder,
            _touch_telegram_state=lambda: touches.append("touch"),
        )

        telegram_heartbeat_patch.install(module)
        application = module.ApplicationBuilder().token("test").build()

        post_init = callbacks["post_init"]
        post_shutdown = callbacks["post_shutdown"]

        await post_init(application)
        await asyncio.sleep(0)

        task = application.bot_data.get(
            telegram_heartbeat_patch.HEARTBEAT_TASK_KEY
        )
        self.assertIsInstance(task, asyncio.Task)
        self.assertFalse(task.done())
        self.assertGreaterEqual(len(touches), 2)

        await post_shutdown(application)

        self.assertNotIn(
            telegram_heartbeat_patch.HEARTBEAT_TASK_KEY,
            application.bot_data,
        )
        self.assertTrue(task.cancelled())


if __name__ == "__main__":
    unittest.main()
