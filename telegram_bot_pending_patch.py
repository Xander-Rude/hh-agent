from __future__ import annotations


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
                    continue

                await context.bot.send_message(
                    chat_id=target_chat_id,
                    text=bot_module.build_message(vacancy, evaluation),
                    reply_markup=bot_module.build_keyboard(vacancy.id),
                    disable_web_page_preview=True,
                )

                sent_vacancy_ids.add(vacancy.id)
                sent_pending += 1

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

                await context.bot.send_message(
                    chat_id=target_chat_id,
                    text=bot_module.build_message(vacancy, evaluation),
                    reply_markup=bot_module.build_keyboard(vacancy.id),
                    disable_web_page_preview=True,
                )

                bot_module.create_notification_state(
                    session=session,
                    vacancy=vacancy,
                    evaluation=evaluation,
                )

                sent_vacancy_ids.add(vacancy.id)
                sent_new += 1

            if sent_new + sent_pending == 0:
                text = "Нет новых вакансий и нет карточек без решения."
                if pending_without_evaluation:
                    text += (
                        "\n\n⚠️ Pending без доступной Evaluation: "
                        f"{pending_without_evaluation}"
                    )
            else:
                text = (
                    f"Новых вакансий: {sent_new}\n"
                    f"Без решения, показаны повторно: {sent_pending}"
                )
                if pending_without_evaluation:
                    text += (
                        "\n⚠️ Не удалось повторить карточек без Evaluation: "
                        f"{pending_without_evaluation}"
                    )

            await context.bot.send_message(
                chat_id=target_chat_id,
                text=text,
            )

        finally:
            session.close()

    bot_module.send_new_vacancies = send_new_vacancies
