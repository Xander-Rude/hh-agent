from __future__ import annotations

import asyncio

from telegram.error import NetworkError, RetryAfter, TimedOut


async def _send_with_retry(bot_module, context, *, chat_id: int, text: str, reply_markup=None) -> bool:
    """Send one Telegram message with bounded retries for transient network/flood errors."""
    attempts = 4

    for attempt in range(1, attempts + 1):
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
            return True

        except RetryAfter as exc:
            wait_seconds = float(getattr(exc, "retry_after", 1) or 1) + 0.5
            print(
                f"[TELEGRAM /new] RetryAfter: attempt={attempt}/{attempts}, "
                f"wait={wait_seconds:.1f}s",
                flush=True,
            )
            if attempt < attempts:
                await asyncio.sleep(wait_seconds)
                continue
            return False

        except (NetworkError, TimedOut) as exc:
            wait_seconds = min(8.0, 1.5 * attempt)
            print(
                f"[TELEGRAM /new] {type(exc).__name__}: attempt={attempt}/{attempts}: {exc}",
                flush=True,
            )
            if attempt < attempts:
                await asyncio.sleep(wait_seconds)
                continue
            return False

        except Exception as exc:
            print(
                f"[TELEGRAM /new] send failed: {type(exc).__name__}: {exc}",
                flush=True,
            )
            return False

    return False


def install(bot_module) -> None:
    """Make /new always resend unresolved notified cards, independent of score filters."""

    async def send_new_vacancies(context, chat_id: int | None = None) -> None:
        target_chat_id = chat_id if chat_id is not None else bot_module.CHAT_ID

        if target_chat_id is None:
            raise RuntimeError(
                "Не удалось определить Telegram chat_id для отправки вакансий."
            )

        session = bot_module.SessionLocal()

        try:
            sent_new = 0
            sent_pending = 0
            failed_pending = 0
            failed_new = 0
            pending_without_evaluation = 0
            sent_vacancy_ids: set[int] = set()

            pending_rows = session.execute(
                bot_module.select(
                    bot_module.Application,
                    bot_module.Vacancy,
                )
                .join(
                    bot_module.Vacancy,
                    bot_module.Vacancy.id == bot_module.Application.vacancy_id,
                )
                .where(bot_module.Application.status == "notified")
                .order_by(bot_module.Application.id.desc())
            ).all()

            print(
                f"[TELEGRAM /new] pending rows: {len(pending_rows)}",
                flush=True,
            )

            for application, vacancy in pending_rows:
                if vacancy.id in sent_vacancy_ids:
                    continue

                evaluation = session.scalars(
                    bot_module.select(bot_module.Evaluation)
                    .where(bot_module.Evaluation.vacancy_id == vacancy.id)
                    .where(
                        ~bot_module.Evaluation.model.startswith("hard-filter/")
                    )
                    .order_by(
                        bot_module.Evaluation.created_at.desc(),
                        bot_module.Evaluation.id.desc(),
                    )
                    .limit(1)
                ).first()

                if evaluation is None:
                    pending_without_evaluation += 1
                    print(
                        f"[TELEGRAM /new] pending skipped: app={application.id} "
                        f"vacancy={vacancy.id} reason=no_evaluation",
                        flush=True,
                    )
                    continue

                ok = await _send_with_retry(
                    bot_module,
                    context,
                    chat_id=target_chat_id,
                    text=bot_module.build_message(vacancy, evaluation),
                    reply_markup=bot_module.build_keyboard(vacancy.id),
                )

                if not ok:
                    failed_pending += 1
                    print(
                        f"[TELEGRAM /new] pending failed: app={application.id} "
                        f"vacancy={vacancy.id} source={vacancy.source}",
                        flush=True,
                    )
                    continue

                sent_vacancy_ids.add(vacancy.id)
                sent_pending += 1
                print(
                    f"[TELEGRAM /new] pending sent: app={application.id} "
                    f"vacancy={vacancy.id} source={vacancy.source}",
                    flush=True,
                )

                # Не долбим Telegram пачкой из десятков сообщений без пауз.
                await asyncio.sleep(0.25)

            candidate_rows = session.execute(
                bot_module.select(
                    bot_module.Vacancy,
                    bot_module.Evaluation,
                )
                .join(
                    bot_module.Evaluation,
                    bot_module.Evaluation.vacancy_id == bot_module.Vacancy.id,
                )
                .where(
                    bot_module.Evaluation.score >= bot_module.MIN_SCORE_TO_NOTIFY
                )
                .where(
                    ~bot_module.Evaluation.model.startswith("hard-filter/")
                )
                .order_by(
                    bot_module.Evaluation.score.desc(),
                    bot_module.Evaluation.responsibility_match.desc(),
                    bot_module.Evaluation.id.desc(),
                )
            ).all()

            for vacancy, evaluation in candidate_rows:
                if vacancy.id in sent_vacancy_ids:
                    continue

                state = bot_module.get_application_state(session, vacancy.id)
                if state is not None:
                    continue

                ok = await _send_with_retry(
                    bot_module,
                    context,
                    chat_id=target_chat_id,
                    text=bot_module.build_message(vacancy, evaluation),
                    reply_markup=bot_module.build_keyboard(vacancy.id),
                )

                if not ok:
                    failed_new += 1
                    print(
                        f"[TELEGRAM /new] new failed: vacancy={vacancy.id} "
                        f"source={vacancy.source}",
                        flush=True,
                    )
                    continue

                bot_module.create_notification_state(
                    session=session,
                    vacancy=vacancy,
                    evaluation=evaluation,
                )

                sent_vacancy_ids.add(vacancy.id)
                sent_new += 1
                await asyncio.sleep(0.25)

            if sent_new + sent_pending == 0:
                text = "Нет новых вакансий и нет карточек без решения."
            else:
                text = (
                    f"Новых вакансий: {sent_new}\n"
                    f"Без решения, показаны повторно: {sent_pending}"
                )

            if pending_without_evaluation:
                text += (
                    "\n⚠️ Без доступной Evaluation: "
                    f"{pending_without_evaluation}"
                )

            if failed_pending or failed_new:
                text += (
                    "\n⚠️ Ошибки доставки: "
                    f"pending={failed_pending}, new={failed_new}"
                )

            await _send_with_retry(
                bot_module,
                context,
                chat_id=target_chat_id,
                text=text,
            )

        finally:
            session.close()

    bot_module.send_new_vacancies = send_new_vacancies
