from __future__ import annotations

from telegram_bot_pending_patch import RECOMMENDED_DECISIONS, _send_with_retry


APPEAL_MODEL_SUFFIX = "+hard-filter-appeal"
APPEAL_ACCOUNT_KEY = "old"


def install(telegram_module) -> None:
    """Add OLD-only appeal delivery without replacing the existing /new workflow."""
    original_send = telegram_module.send_new_vacancies

    async def send_new_vacancies(
        context,
        chat_id: int | None = None,
        account_key: str | None = None,
    ) -> None:
        # Keep the production /new implementation intact: retry handling,
        # manual_required recovery, latest-evaluation logic and normal apply cards.
        await original_send(
            context,
            chat_id=chat_id,
            account_key=account_key,
        )

        # Hard-filter appeals predate the automatic OLD stream. Default /new
        # must stay operator-light: OLD ordinary decisions are automatic now.
        # Keep these legacy cards available only when the operator explicitly
        # asks for /new old.
        if account_key != APPEAL_ACCOUNT_KEY:
            return

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
                .where(
                    telegram_module.Evaluation.decision.in_(
                        RECOMMENDED_DECISIONS
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
                    account_key=APPEAL_ACCOUNT_KEY,
                )

                if (
                    state is not None
                    and state.status != "notified"
                ):
                    continue

                # Normal recommended cards are already handled by the original
                # /new implementation. A notified apply/review card would
                # otherwise be duplicated by this appeal-only pass.
                if state is not None:
                    continue

                state = telegram_module.create_notification_state(
                    session=session,
                    vacancy=vacancy,
                    evaluation=evaluation,
                    account_key=APPEAL_ACCOUNT_KEY,
                )
                # An OLD hard-filter appeal with a recommended decision has
                # already been decided by the automated policy. OLD ordinary
                # decision cards are intentionally suppressed, so keeping this
                # row as "notified" strands it between queues. Route it to the
                # repeatable manual-application queue instead.
                state.status = "manual_required"
                session.commit()

                ok = await _send_with_retry(
                    telegram_module,
                    context,
                    chat_id=target_chat_id,
                    text=telegram_module.build_manual_required_message(
                        vacancy,
                        state,
                    ),
                    reply_markup=telegram_module.build_manual_required_keyboard(
                        vacancy,
                        state,
                    ),
                )

                if not ok:
                    session.delete(state)
                    session.commit()
                    print(
                        "[TELEGRAM /new] appeal override delivery failed: "
                        f"vacancy={vacancy.id} score={evaluation.score} "
                        f"decision={evaluation.decision}",
                        flush=True,
                    )
                    continue

                sent_new += 1
                print(
                    "[TELEGRAM /new] appeal override sent: "
                    f"vacancy={vacancy.id} score={evaluation.score} "
                    f"decision={evaluation.decision}",
                    flush=True,
                )

            if sent_new:
                await _send_with_retry(
                    telegram_module,
                    context,
                    chat_id=target_chat_id,
                    text=(
                        "🛟 Восстановлено LLM-апелляцией hard-filter: "
                        f"новых={sent_new}"
                    ),
                )
        finally:
            session.close()

    send_new_vacancies.__name__ = original_send.__name__
    send_new_vacancies.__doc__ = original_send.__doc__
    telegram_module.send_new_vacancies = send_new_vacancies
