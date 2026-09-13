from __future__ import annotations

import asyncio
import contextlib


HEARTBEAT_INTERVAL_SECONDS = 30
HEARTBEAT_TASK_KEY = "_telegram_heartbeat_task"


def install(module) -> None:
    """Attach a periodic heartbeat to the Telegram application's event loop."""
    if getattr(module, "_telegram_heartbeat_patch_installed", False):
        return

    original_builder = module.ApplicationBuilder

    async def heartbeat_loop() -> None:
        while True:
            module._touch_telegram_state()
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    async def post_init(application) -> None:
        module._touch_telegram_state()
        # post_init runs before python-telegram-bot marks Application as running.
        # Application.create_task() warns in that phase. We own this task's
        # lifecycle explicitly via bot_data + post_shutdown, so asyncio's task
        # API is the correct fit here.
        application.bot_data[HEARTBEAT_TASK_KEY] = asyncio.create_task(
            heartbeat_loop(),
            name="telegram-heartbeat",
        )

    async def post_shutdown(application) -> None:
        task = application.bot_data.pop(HEARTBEAT_TASK_KEY, None)
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    class HeartbeatApplicationBuilder:
        def __init__(self, *args, **kwargs) -> None:
            self._builder = original_builder(*args, **kwargs)

        def token(self, *args, **kwargs):
            self._builder.token(*args, **kwargs)
            return self

        def build(self):
            self._builder.post_init(post_init)
            self._builder.post_shutdown(post_shutdown)
            return self._builder.build()

    module.ApplicationBuilder = HeartbeatApplicationBuilder
    module._telegram_heartbeat_patch_installed = True
