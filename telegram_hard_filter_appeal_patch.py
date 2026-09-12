from __future__ import annotations

from sqlalchemy import or_, select


APPEAL_MODEL_SUFFIX = "+hard-filter-appeal"


def install(telegram_module) -> None:
    original_send = telegram_module.send_new_vacancies

    async def send_new_vacancies(
        context,
        chat_id: int | None = None,
    ) -> None:
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
            sent_manual, manual_vacancy_ids = (
                await telegram_module._send_manual_required_cards(
                    context,
                    session,
                    target_chat_id,
                )
            )

            rows = session.execute(
                select(
                    telegram_module.Vacancy,
                    telegram_module.Evaluation,
                )
                .join(
                    telegram_module.Evaluation,
                    telegram_module.Evaluation.vacancy_id
                    == telegram_module.Vacancy.id,
                )
                .where(
                    or_(
                        telegram_module.Evaluation.score
                        >= telegram_module.MIN_SCORE_TO_NOTIFY,
                        telegram_module.Evaluation.model.endswith(
                            APPEAL_MODEL_SUFFIX
                        ),
                    )
                )
                .where(
                    ~telegram_module.Evaluation.model.startswith(
                        "hard-filter/"
                    )
                )
                .where(
                    ~telegram_module.Evaluation.model.startswith(
                        "hard-filter+appeal/"
                    )
                )
                .order_by(
                    telegram_module.Evaluation.score.desc(),
                    telegram_module.Evaluation.responsibility_match.desc(),
                )
            ).all()

            sent_new = 0
            sent_pending = 0
            seen_vacancy_ids: set[int] = set(
                manual_vacancy_ids
            )

            for vacancy, evaluation in rows:
                if vacancy.id in seen_vacancy_ids:
                    continue
                seen_vacancy_ids.add(vacancy.id)

                state = telegram_module.get_application_state(
                    session,
                    vacancy.id,
                )

                if (
                    state is not None
                    and state.status != "notified"
                ):
                    continue

                await context.bot.send_message(
                    chat_id=target_chat_id,
                    text=telegram_module.build_message(
                        vacancy=vacancy,
                        evaluation=evaluation,
                    ),
                    reply_markup=telegram_module.build_keyboard(
                        vacancy.id
                    ),
                    disable_web_page_preview=True,
                )

                if state is None:
                    telegram_module.create_notification_state(
                        session=session,
                        vacancy=vacancy,
                        evaluation=evaluation,
                    )
                    sent_new += 1
                else:
                    sent_pending += 1

            total_sent = (
                sent_new
                + sent_pending
                + sent_manual
            )

            if total_sent == 0:
                await context.bot.send_message(
                    chat_id=target_chat_id,
                    text=(
                        "Нет новых вакансий, карточек без решения и откликов, "
                        "требующих ручного действия."
                    ),
                )
            else:
                await context.bot.send_message(
                    chat_id=target_chat_id,
                    text=(
                        f"Новых вакансий: {sent_new}\n"
                        f"Без решения, показаны повторно: {sent_pending}\n"
                        f"Требуют ручного действия: {sent_manual}"
                    ),
                )
        finally:
            session.close()

    send_new_vacancies.__name__ = original_send.__name__
    send_new_vacancies.__doc__ = original_send.__doc__
    telegram_module.send_new_vacancies = send_new_vacancies
