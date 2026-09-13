from __future__ import annotations

import asyncio


NEW_TASK_KEY = "_telegram_new_delivery_task"
NEW_CHAT_KEY = "_telegram_new_delivery_chat_id"


def install(bot_module) -> None:
    """Run /new delivery outside the update handler so other commands stay responsive."""
    if getattr(bot_module, "_telegram_new_background_patch_installed", False):
        return

    async def run_delivery(context, chat_id: int) -> None:
        try:
            print(
                f"[TELEGRAM /new] background delivery started: chat={chat_id}",
                flush=True,
            )
            await bot_module.send_new_vacancies(context, chat_id=chat_id)
            print(
                f"[TELEGRAM /new] background delivery finished: chat={chat_id}",
                flush=True,
            )
        except asyncio.CancelledError:
            print(
                f"[TELEGRAM /new] background delivery cancelled: chat={chat_id}",
                flush=True,
            )
            raise
        except Exception as exc:
            print(
                f"[TELEGRAM /new] background delivery failed: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "❌ Ошибка при обработке /new. "
                        "Бот продолжает работать; подробности в logs/telegram.log."
                    ),
                )
            except Exception as notify_exc:
                print(
                    f"[TELEGRAM /new] failed to report background error: "
                    f"{type(notify_exc).__name__}: {notify_exc}",
                    flush=True,
                )
        finally:
            current_task = asyncio.current_task()
            if context.application.bot_data.get(NEW_TASK_KEY) is current_task:
                context.application.bot_data.pop(NEW_TASK_KEY, None)
                context.application.bot_data.pop(NEW_CHAT_KEY, None)

    async def new_command(update, context) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None:
            return

        current = context.application.bot_data.get(NEW_TASK_KEY)
        if current is not None and not current.done():
            active_chat_id = context.application.bot_data.get(NEW_CHAT_KEY)
            suffix = (
                f" (chat {active_chat_id})"
                if active_chat_id is not None and int(active_chat_id) != int(chat.id)
                else ""
            )
            await message.reply_text(
                "⏳ /new уже выполняется"
                f"{suffix}. /health и остальные команды доступны."
            )
            return

        await message.reply_text(
            "Проверяю базу в фоне. /health и остальные команды доступны."
        )

        task = context.application.create_task(
            run_delivery(context, int(chat.id)),
            name=f"telegram-new-{chat.id}",
        )
        context.application.bot_data[NEW_TASK_KEY] = task
        context.application.bot_data[NEW_CHAT_KEY] = int(chat.id)

    bot_module.new_command = new_command
    bot_module._telegram_new_background_patch_installed = True
