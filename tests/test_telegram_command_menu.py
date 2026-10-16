from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from telegram import BotCommandScopeChat, MenuButtonCommands
from telegram.error import TelegramError

from telegram_command_menu import OWNER_COMMANDS, register_owner_command_menu
import telegram_heartbeat_patch


ROOT = Path(__file__).resolve().parents[1]
COMMAND_FILES = (
    "telegram_bot.py",
    "telegram_sber_screening_patch.py",
    "telegram_hh_questionnaire_patch.py",
    "targeted_hunt_telegram_patch.py",
)


def registered_commands() -> set[str]:
    found: set[str] = set()
    for filename in COMMAND_FILES:
        tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "CommandHandler"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                found.add(node.args[0].value)
    return found


class TelegramCommandMenuTests(unittest.IsolatedAsyncioTestCase):
    def test_menu_matches_all_registered_handlers(self) -> None:
        menu_names = [command.command for command in OWNER_COMMANDS]
        self.assertEqual(len(menu_names), len(set(menu_names)))
        self.assertEqual(set(menu_names), registered_commands())
        self.assertTrue(all(3 <= len(command.description) <= 256 for command in OWNER_COMMANDS))

    async def test_menu_is_scoped_to_owner_and_button_is_commands(self) -> None:
        bot = SimpleNamespace(
            set_my_commands=AsyncMock(),
            set_chat_menu_button=AsyncMock(),
        )
        await register_owner_command_menu(SimpleNamespace(bot=bot), 123456)

        bot.set_my_commands.assert_awaited_once()
        kwargs = bot.set_my_commands.await_args.kwargs
        self.assertEqual(tuple(kwargs["commands"]), OWNER_COMMANDS)
        self.assertIsInstance(kwargs["scope"], BotCommandScopeChat)
        self.assertEqual(kwargs["scope"].chat_id, 123456)

        bot.set_chat_menu_button.assert_awaited_once()
        button_kwargs = bot.set_chat_menu_button.await_args.kwargs
        self.assertEqual(button_kwargs["chat_id"], 123456)
        self.assertIsInstance(button_kwargs["menu_button"], MenuButtonCommands)

    async def test_menu_registration_failure_does_not_block_button(self) -> None:
        bot = SimpleNamespace(
            set_my_commands=AsyncMock(side_effect=TelegramError("temporarily offline")),
            set_chat_menu_button=AsyncMock(),
        )
        await register_owner_command_menu(SimpleNamespace(bot=bot), 123)
        bot.set_chat_menu_button.assert_awaited_once()

    async def test_menu_button_failure_does_not_stop_bot(self) -> None:
        bot = SimpleNamespace(
            set_my_commands=AsyncMock(),
            set_chat_menu_button=AsyncMock(side_effect=TelegramError("temporarily offline")),
        )
        await register_owner_command_menu(SimpleNamespace(bot=bot), 123)
        bot.set_my_commands.assert_awaited_once()

    async def test_heartbeat_initialization_installs_menu(self) -> None:
        callbacks = {}

        class FakeBuilder:
            def token(self, _token):
                return self

            def post_init(self, callback):
                callbacks["post_init"] = callback
                return self

            def post_shutdown(self, callback):
                callbacks["post_shutdown"] = callback
                return self

            def build(self):
                return SimpleNamespace(bot_data={})

        module = SimpleNamespace(
            ApplicationBuilder=FakeBuilder,
            CHAT_ID=123,
            _touch_telegram_state=lambda: None,
        )
        with patch.object(
            telegram_heartbeat_patch,
            "register_owner_command_menu",
            new_callable=AsyncMock,
        ) as register:
            telegram_heartbeat_patch.install(module)
            application = module.ApplicationBuilder().token("test").build()
            await callbacks["post_init"](application)
            register.assert_awaited_once_with(application, 123)
            await callbacks["post_shutdown"](application)


if __name__ == "__main__":
    unittest.main()
