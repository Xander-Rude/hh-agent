from __future__ import annotations

import asyncio

from telegram.error import NetworkError, RetryAfter, TimedOut


RECOMMENDED_DECISIONS = ("apply", "review")


async def _send_with_retry(
    bot_module,
    context,
    *,
    chat_id: int,
    text: str,
    reply_markup=None,
):
    """Send one Telegram message and return the Message object on success."""
    attempts = 4

    for attempt in range(1, attempts + 1):
        try:
            return await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )

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
            return None

        except (NetworkError, TimedOut) as exc:
            wait_seconds = min(8.0, 1.5 * attempt)
            print(
                f"[TELEGRAM /new] {type(exc).__name__}: "
                f"attempt={attempt}/{attempts}: {exc}",
                flush=True,
            )
            if attempt < attempts:
                await asyncio.sleep(wait_seconds)
                continue
            return None

        except Exception as exc:
            print(
                f"[TELEGRAM /new] send failed: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            return None

    return None


def _bind_message(bot_module, session, state, message) -> None:
    """Persist exactly which Telegram card controls this Application."""
    if message is None:
        return

    state.telegram_chat_id = str(message.chat_id)
    state.telegram_message_id = int(message.message_id)
    state.telegram_notified_at = bot_module.datetime.utcnow()
    session.commit()


def _account_cutoff(bot_module, account):
    if account.key != "clean":
        return None
    return bot_module.account_activated_at(account)


async def _deliver_account(
    bot_module,
    context,
    session,
    *,
    target_chat_id: int,
    account,
) -> dict[str, int | bool]:
    cutoff = _account_cutoff(bot_module, account)
    max_cards = bot_module.TELEGRAM_NEW_MAX_CARDS

    sent_new = 0
    sent_pending = 0
    sent_manual = 0
    failed_pending = 0
    failed_manual = 0
    failed_new = 0
    pending_without_evaluation = 0
    pending_not_recommended = 0
    pending_not_clean_eligible = 0
    sent_vacancy_ids: set[int] = set()
    clean_policy_context = (
        bot_module.current_policy_context()
        if account.key == "clean"
        else None
    )

    manual_query = (
        bot_module.select(
            bot_module.Application,
            bot_module.Vacancy,
        )
        .join(
            bot_module.Vacancy,
            bot_module.Vacancy.id == bot_module.Application.vacancy_id,
        )
        .where(bot_module.Application.status == "manual_required")
        .where(bot_module.Application.account_key == account.key)
        .where(bot_module.Vacancy.source == "hh")
        .order_by(bot_module.Application.id.desc())
    )
    if cutoff is not None:
        manual_query = manual_query.where(
            bot_module.Vacancy.found_at >= cutoff
        )
    manual_rows = session.execute(manual_query).all()

    print(
        f"[TELEGRAM /new] {account.key} manual_required rows: "
        f"{len(manual_rows)}",
        flush=True,
    )

    for state, vacancy in manual_rows:
        if sent_new + sent_pending + sent_manual >= max_cards:
            break
        if vacancy.id in sent_vacancy_ids:
            continue
        if state.status != "manual_required":
            continue

        message = await _send_with_retry(
            bot_module,
            context,
            chat_id=target_chat_id,
            text=bot_module.build_manual_required_message(vacancy, state),
            reply_markup=bot_module.build_manual_required_keyboard(
                vacancy,
                state,
            ),
        )

        if message is None:
            failed_manual += 1
            continue

        _bind_message(bot_module, session, state, message)
        sent_vacancy_ids.add(vacancy.id)
        sent_manual += 1
        await asyncio.sleep(0.25)

    pending_query = (
        bot_module.select(
            bot_module.Application,
            bot_module.Vacancy,
        )
        .join(
            bot_module.Vacancy,
            bot_module.Vacancy.id == bot_module.Application.vacancy_id,
        )
        .where(bot_module.Application.status == "notified")
        .where(bot_module.Application.account_key == account.key)
        .where(bot_module.Vacancy.source == "hh")
        .order_by(bot_module.Application.id.desc())
    )
    if cutoff is not None:
        pending_query = pending_query.where(
            bot_module.Vacancy.found_at >= cutoff
        )
    pending_rows = session.execute(pending_query).all()

    print(
        f"[TELEGRAM /new] {account.key} pending rows: "
        f"{len(pending_rows)}",
        flush=True,
    )

    for state, vacancy in pending_rows:
        if sent_new + sent_pending + sent_manual >= max_cards:
            break
        if vacancy.id in sent_vacancy_ids:
            continue
        if state.status != "notified":
            continue

        if clean_policy_context is not None:
            eligibility = bot_module.clean_eligibility(
                session,
                vacancy.id,
                context=clean_policy_context,
            )
            if not eligibility.eligible:
                pending_not_clean_eligible += 1
                print(
                    f"[TELEGRAM /new] clean pending suppressed: "
                    f"application={state.id} vacancy={vacancy.id} "
                    f"reason={eligibility.reason}",
                    flush=True,
                )
                continue
            evaluation = session.get(
                bot_module.Evaluation,
                eligibility.assessment.legacy_evaluation_id,
            )
        else:
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

        if (
            clean_policy_context is None
            and evaluation.decision not in RECOMMENDED_DECISIONS
        ):
            pending_not_recommended += 1
            continue

        message = await _send_with_retry(
            bot_module,
            context,
            chat_id=target_chat_id,
            text=bot_module.build_message(
                vacancy,
                evaluation,
                account_key=account.key,
            ),
            reply_markup=bot_module.build_keyboard(
                vacancy.id,
                application_id=state.id,
            ),
        )

        if message is None:
            failed_pending += 1
            continue

        _bind_message(bot_module, session, state, message)
        sent_vacancy_ids.add(vacancy.id)
        sent_pending += 1
        await asyncio.sleep(0.25)

    has_account_application = (
        bot_module.select(bot_module.Application.id)
        .where(
            bot_module.Application.vacancy_id == bot_module.Vacancy.id,
            bot_module.Application.account_key == account.key,
        )
        .exists()
    )

    if clean_policy_context is not None:
        latest_shadow_id = (
            bot_module.select(
                bot_module.func.max(bot_module.CleanShadowAssessment.id)
            )
            .where(
                bot_module.CleanShadowAssessment.vacancy_id
                == bot_module.Vacancy.id,
                *bot_module.assessment_version_filters(
                    clean_policy_context
                ),
            )
            .correlate(bot_module.Vacancy)
            .scalar_subquery()
        )
        candidate_query = (
            bot_module.select(
                bot_module.Vacancy,
                bot_module.Evaluation,
            )
            .join(
                bot_module.CleanShadowAssessment,
                bot_module.CleanShadowAssessment.id
                == latest_shadow_id,
            )
            .join(
                bot_module.Evaluation,
                bot_module.Evaluation.id
                == bot_module.CleanShadowAssessment.legacy_evaluation_id,
            )
            .where(~has_account_application)
            .where(bot_module.Vacancy.source == "hh")
            .where(
                bot_module.CleanShadowAssessment.routing_class.in_(
                    bot_module.CLEAN_ELIGIBLE_ROUTES
                )
            )
            .order_by(
                (
                    bot_module.CleanShadowAssessment.routing_class
                    == "CLEAN_STRONG"
                ).desc(),
                bot_module.CleanShadowAssessment.invite_score.desc(),
                bot_module.CleanShadowAssessment.fit_score.desc(),
                bot_module.CleanShadowAssessment.id.desc(),
            )
        )
    else:
        latest_evaluation_id = (
            bot_module.select(bot_module.Evaluation.id)
            .where(
                bot_module.Evaluation.vacancy_id
                == bot_module.Vacancy.id
            )
            .where(
                ~bot_module.Evaluation.model.startswith("hard-filter/")
            )
            .order_by(
                bot_module.Evaluation.created_at.desc(),
                bot_module.Evaluation.id.desc(),
            )
            .limit(1)
            .correlate(bot_module.Vacancy)
            .scalar_subquery()
        )
        candidate_query = (
            bot_module.select(
                bot_module.Vacancy,
                bot_module.Evaluation,
            )
            .join(
                bot_module.Evaluation,
                bot_module.Evaluation.id == latest_evaluation_id,
            )
            .where(~has_account_application)
            .where(bot_module.Vacancy.source == "hh")
            .where(
                bot_module.Evaluation.decision.in_(
                    RECOMMENDED_DECISIONS
                )
            )
            .where(
                bot_module.Evaluation.score
                >= bot_module.MIN_SCORE_TO_NOTIFY
            )
            .order_by(
                bot_module.Evaluation.score.desc(),
                bot_module.Evaluation.responsibility_match.desc(),
                bot_module.Evaluation.id.desc(),
            )
        )

    if cutoff is not None:
        candidate_query = candidate_query.where(
            bot_module.Vacancy.found_at >= cutoff
        )
    candidate_rows = session.execute(candidate_query).all()

    print(
        f"[TELEGRAM /new] {account.key} new candidate rows: "
        f"{len(candidate_rows)}",
        flush=True,
    )

    for vacancy, evaluation in candidate_rows:
        if sent_new + sent_pending + sent_manual >= max_cards:
            break
        if vacancy.id in sent_vacancy_ids:
            continue

        state = bot_module.create_notification_state(
            session=session,
            vacancy=vacancy,
            evaluation=evaluation,
            account_key=account.key,
        )

        # A concurrent/repeated /new may have created the account state first.
        if state.status != "notified":
            continue

        message = await _send_with_retry(
            bot_module,
            context,
            chat_id=target_chat_id,
            text=bot_module.build_message(
                vacancy,
                evaluation,
                account_key=account.key,
            ),
            reply_markup=bot_module.build_keyboard(
                vacancy.id,
                application_id=state.id,
            ),
        )

        if message is None:
            failed_new += 1
            continue

        _bind_message(bot_module, session, state, message)
        sent_vacancy_ids.add(vacancy.id)
        sent_new += 1

        bot_module.record_outcome_event(
            state.id,
            "card_notified",
            source="telegram",
            confidence="system_confirmed",
            details={
                "chat_id": target_chat_id,
                "message_id": int(message.message_id),
                "account_key": account.key,
                "repeat": False,
            },
        )
        await asyncio.sleep(0.25)

    return {
        "sent_new": sent_new,
        "sent_pending": sent_pending,
        "sent_manual": sent_manual,
        "failed_pending": failed_pending,
        "failed_manual": failed_manual,
        "failed_new": failed_new,
        "pending_without_evaluation": pending_without_evaluation,
        "pending_not_recommended": pending_not_recommended,
        "pending_not_clean_eligible": pending_not_clean_eligible,
        "limit_reached": (
            sent_new + sent_pending + sent_manual >= max_cards
        ),
    }


async def _deliver_external(
    bot_module,
    context,
    session,
    *,
    target_chat_id: int,
) -> dict[str, int | bool]:
    """Deliver non-HH sources exactly once, outside OLD/CLEAN account loops."""

    max_cards = bot_module.TELEGRAM_NEW_MAX_CARDS

    sent_new = 0
    sent_pending = 0
    sent_manual = 0
    failed_pending = 0
    failed_manual = 0
    failed_new = 0
    pending_without_evaluation = 0
    pending_not_recommended = 0
    sent_vacancy_ids: set[int] = set()

    external_source = bot_module.Vacancy.source != "hh"

    manual_query = (
        bot_module.select(
            bot_module.Application,
            bot_module.Vacancy,
        )
        .join(
            bot_module.Vacancy,
            bot_module.Vacancy.id == bot_module.Application.vacancy_id,
        )
        .where(bot_module.Application.status == "manual_required")
        .where(external_source)
        .order_by(bot_module.Application.id.desc())
    )
    manual_rows = session.execute(manual_query).all()

    for state, vacancy in manual_rows:
        if sent_new + sent_pending + sent_manual >= max_cards:
            break
        if vacancy.id in sent_vacancy_ids:
            continue

        message = await _send_with_retry(
            bot_module,
            context,
            chat_id=target_chat_id,
            text=bot_module.build_manual_required_message(vacancy, state),
            reply_markup=bot_module.build_manual_required_keyboard(
                vacancy,
                state,
            ),
        )
        if message is None:
            failed_manual += 1
            continue

        _bind_message(bot_module, session, state, message)
        sent_vacancy_ids.add(vacancy.id)
        sent_manual += 1
        await asyncio.sleep(0.25)

    pending_query = (
        bot_module.select(
            bot_module.Application,
            bot_module.Vacancy,
        )
        .join(
            bot_module.Vacancy,
            bot_module.Vacancy.id == bot_module.Application.vacancy_id,
        )
        .where(bot_module.Application.status == "notified")
        .where(external_source)
        .order_by(bot_module.Application.id.desc())
    )
    pending_rows = session.execute(pending_query).all()

    for state, vacancy in pending_rows:
        if sent_new + sent_pending + sent_manual >= max_cards:
            break
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
        if evaluation.decision not in RECOMMENDED_DECISIONS:
            pending_not_recommended += 1
            continue

        message = await _send_with_retry(
            bot_module,
            context,
            chat_id=target_chat_id,
            text=bot_module.build_message(
                vacancy,
                evaluation,
                account_key=state.account_key,
            ),
            reply_markup=bot_module.build_keyboard(
                vacancy.id,
                application_id=state.id,
            ),
        )
        if message is None:
            failed_pending += 1
            continue

        _bind_message(bot_module, session, state, message)
        sent_vacancy_ids.add(vacancy.id)
        sent_pending += 1
        await asyncio.sleep(0.25)

    latest_evaluation_id = (
        bot_module.select(bot_module.Evaluation.id)
        .where(
            bot_module.Evaluation.vacancy_id == bot_module.Vacancy.id
        )
        .where(
            ~bot_module.Evaluation.model.startswith("hard-filter/")
        )
        .order_by(
            bot_module.Evaluation.created_at.desc(),
            bot_module.Evaluation.id.desc(),
        )
        .limit(1)
        .correlate(bot_module.Vacancy)
        .scalar_subquery()
    )

    has_any_application = (
        bot_module.select(bot_module.Application.id)
        .where(
            bot_module.Application.vacancy_id == bot_module.Vacancy.id,
        )
        .exists()
    )

    candidate_query = (
        bot_module.select(
            bot_module.Vacancy,
            bot_module.Evaluation,
        )
        .join(
            bot_module.Evaluation,
            bot_module.Evaluation.id == latest_evaluation_id,
        )
        .where(~has_any_application)
        .where(external_source)
        .where(
            bot_module.Evaluation.decision.in_(RECOMMENDED_DECISIONS)
        )
        .where(
            bot_module.Evaluation.score
            >= bot_module.MIN_SCORE_TO_NOTIFY
        )
        .order_by(
            bot_module.Evaluation.score.desc(),
            bot_module.Evaluation.responsibility_match.desc(),
            bot_module.Evaluation.id.desc(),
        )
    )
    candidate_rows = session.execute(candidate_query).all()

    for vacancy, evaluation in candidate_rows:
        if sent_new + sent_pending + sent_manual >= max_cards:
            break
        if vacancy.id in sent_vacancy_ids:
            continue

        state = bot_module.create_notification_state(
            session=session,
            vacancy=vacancy,
            evaluation=evaluation,
            account_key="old",
        )
        if state.status != "notified":
            continue

        message = await _send_with_retry(
            bot_module,
            context,
            chat_id=target_chat_id,
            text=bot_module.build_message(
                vacancy,
                evaluation,
                account_key=state.account_key,
            ),
            reply_markup=bot_module.build_keyboard(
                vacancy.id,
                application_id=state.id,
            ),
        )
        if message is None:
            failed_new += 1
            continue

        _bind_message(bot_module, session, state, message)
        sent_vacancy_ids.add(vacancy.id)
        sent_new += 1

        bot_module.record_outcome_event(
            state.id,
            "card_notified",
            source="telegram",
            confidence="system_confirmed",
            details={
                "chat_id": target_chat_id,
                "message_id": int(message.message_id),
                "source": (vacancy.source or "unknown"),
                "repeat": False,
            },
        )
        await asyncio.sleep(0.25)

    return {
        "sent_new": sent_new,
        "sent_pending": sent_pending,
        "sent_manual": sent_manual,
        "failed_pending": failed_pending,
        "failed_manual": failed_manual,
        "failed_new": failed_new,
        "pending_without_evaluation": pending_without_evaluation,
        "pending_not_recommended": pending_not_recommended,
        "limit_reached": (
            sent_new + sent_pending + sent_manual >= max_cards
        ),
    }


def install(bot_module) -> None:
    """Show independent OLD/CLEAN queues and bind every card to Application ID."""

    async def send_new_vacancies(
        context,
        chat_id: int | None = None,
        account_key: str | None = None,
    ) -> None:
        target_chat_id = (
            chat_id if chat_id is not None else bot_module.CHAT_ID
        )
        if target_chat_id is None:
            raise RuntimeError(
                "Не удалось определить Telegram chat_id для отправки вакансий."
            )

        if account_key:
            account = bot_module.get_account(account_key)
            accounts = (account,)
        else:
            accounts = bot_module.apply_accounts()

        session = bot_module.SessionLocal()
        try:
            summaries: list[str] = []

            for account in accounts:
                stats = await _deliver_account(
                    bot_module,
                    context,
                    session,
                    target_chat_id=target_chat_id,
                    account=account,
                )

                line = (
                    f"{bot_module.account_label(account.key)}: "
                    f"новых {stats['sent_new']}, "
                    f"повторно {stats['sent_pending']}, "
                    f"ручных {stats['sent_manual']}"
                )
                if stats["limit_reached"]:
                    line += (
                        f" · лимит {bot_module.TELEGRAM_NEW_MAX_CARDS}"
                    )

                errors = (
                    int(stats["failed_pending"])
                    + int(stats["failed_manual"])
                    + int(stats["failed_new"])
                )
                if errors:
                    line += f" · ошибок доставки {errors}"

                suppressed = (
                    int(stats["pending_without_evaluation"])
                    + int(stats["pending_not_recommended"])
                )
                if suppressed:
                    line += f" · скрыто {suppressed}"

                summaries.append(line)

            if account_key is None:
                external_stats = await _deliver_external(
                    bot_module,
                    context,
                    session,
                    target_chat_id=target_chat_id,
                )
                external_line = (
                    "🌐 EXTERNAL: "
                    f"новых {external_stats['sent_new']}, "
                    f"повторно {external_stats['sent_pending']}, "
                    f"ручных {external_stats['sent_manual']}"
                )
                if external_stats["limit_reached"]:
                    external_line += (
                        f" · лимит {bot_module.TELEGRAM_NEW_MAX_CARDS}"
                    )
                summaries.append(external_line)

            if not summaries:
                summaries.append(
                    "Нет настроенных HH-аккаунтов для показа карточек."
                )

            await _send_with_retry(
                bot_module,
                context,
                chat_id=target_chat_id,
                text="\n".join(summaries),
            )

        finally:
            session.close()

    bot_module.send_new_vacancies = send_new_vacancies
