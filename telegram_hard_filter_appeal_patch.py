from __future__ import annotations

from telegram_bot_pending_patch import _send_with_retry


APPEAL_MODEL_SUFFIX = "+hard-filter-appeal"


def install(telegram_module) -> None:
    """Add appeal-only delivery without replacing the existing /new workflow."""
    original_send = telegram_module.send_new_vacancies

    async def send_new_vacancies(
        context,
        chat_id: int | None = None,
    ) -> None:
        # Keep the production /new implementation intact: retry handling,
        # manual_required recovery, latest-evaluation logic and normal apply cards.
        await original_send(
            context,
            chat_id=chat_id,
        )

        target_chat_id = (
            chat_id
            if chat_id is not None
            else telegram_module.CHAT_ID
        )
        if target_chat_id is None:
            raise RuntimeError(
                "Не удалось определить Telegram chat_id для отправки вакансий."
            )

        session = telegram_module.SessionLocal()
        try:
            latest_evaluation_id = (
                telegram_module.select(
                    telegram_module.Evaluation.id
                )
                .where(
                    telegram_module.Evaluation.vacancy_id
                    == telegram_module.Vacancy.id
                )
                .order_by(
                    telegram_module.Evaluation.created_at.desc(),
                    telegram_module.Evaluation.id.desc(),
                )
                .limit(1)
                .correlate(telegram_module.Vacancy)
                .scalar_subquery()
            )

            rows = session.execute(
                telegram_module.select(
                    telegram_module.Vacancy,
                    telegram_module.Evaluation,
                )
                .join(
                    telegram_module.Evaluation,
                    telegram_module.Evaluation.id
                    == latest_evaluation_id,
                )
                .where(
                    telegram_module.Evaluation.model.endswith(
                        APPEAL_MODEL_SUFFIX
                    )
                )
                .order_by(
                    telegram_module.Evaluation.created_at.desc(),
                    telegram_module.Evaluation.id.desc(),
                )
            ).all()

            sent_new = 0
            sent_pending = 0

            for vacancy, evaluation in rows:
                state = telegram_module.get_application_state(
                    session,
                    vacancy.id,
                )

                if (
                    state is not None
                    and state.status != "notified"
                ):
                    continue

                # Normal recommended cards are already handled by the original
                # /new implementation. A notified apply card would otherwise be
                # duplicated by this appeal-only pass.
                if (
                    evaluation.decision == "apply"
                    and state is not None
                ):
                    continue

                # A new apply card above the normal threshold should also have
                # been handled by the original pass. If no state exists here,
                # the original delivery failed, so one additional bounded retry
                # is useful rather than silently losing the card.
                ok = await _send_with_retry(
                    telegram_module,
                    context,
                    chat_id=target_chat_id,
                    text=telegram_module.build_message(
                        vacancy,
                        evaluation,
                    ),
                    reply_markup=telegram_module.build_keyboard(
                        vacancy.id
                    ),
                )

                if not ok:
                    print(
                        "[TELEGRAM /new] appeal override delivery failed: "
                        f"vacancy={vacancy.id} score={evaluation.score} "
                        f"decision={evaluation.decision}",
                        flush=True,
                    )
                    continue

                if state is None:
                    telegram_module.create_notification_state(
                        session=session,
                        vacancy=vacancy,
                        evaluation=evaluation,
                    )
                    sent_new += 1
                else:
                    sent_pending += 1

                print(
                    "[TELEGRAM /new] appeal override sent: "
                    f"vacancy={vacancy.id} score={evaluation.score} "
                    f"decision={evaluation.decision}",
                    flush=True,
                )

            if sent_new or sent_pending:
                await _send_with_retry(
                    telegram_module,
                    context,
                    chat_id=target_chat_id,
                    text=(
                        "🛟 Восстановлено LLM-апелляцией hard-filter: "
                        f"новых={sent_new}, без решения={sent_pending}"
                    ),
                )
        finally:
            session.close()

    send_new_vacancies.__name__ = original_send.__name__
    send_new_vacancies.__doc__ = original_send.__doc__
    telegram_module.send_new_vacancies = send_new_vacancies
