from __future__ import annotations

import asyncio


def build_vacancy_header(result) -> str:
    lines = [f"✉️ {result.title}"]

    if result.company:
        lines.append(str(result.company))

    if result.used_cached_evaluation:
        lines.extend(
            [
                "",
                "⚡ Использована уже рассчитанная оценка из базы.",
            ]
        )

    return "\n".join(lines)


def install(cover_module) -> None:
    """Send vacancy metadata and copy-ready cover letter as two messages."""

    async def vacancy_link_message(update, context) -> None:
        message = update.effective_message
        if message is None:
            return

        url = cover_module.extract_first_url(message.text or "")
        if url is None:
            return

        progress = await message.reply_text(
            "✍️ Готовлю сопроводительное по вакансии..."
        )

        try:
            result = await asyncio.to_thread(
                cover_module.create_cover_letter_for_url,
                url,
            )

            await progress.edit_text(
                build_vacancy_header(result),
                disable_web_page_preview=True,
            )

            # Отдельное сообщение содержит только письмо, чтобы его можно было
            # копировать целиком без заголовка вакансии и служебных пометок.
            await message.reply_text(
                result.cover_letter,
                disable_web_page_preview=True,
            )

        except Exception as exc:
            print(
                f"[TELEGRAM COVER] ERROR {url}: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            await progress.edit_text(
                "❌ Не удалось подготовить сопроводительное.\n\n"
                f"{type(exc).__name__}: {exc}"
            )

    cover_module.vacancy_link_message = vacancy_link_message
