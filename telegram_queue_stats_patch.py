from __future__ import annotations


def install(bot_module) -> None:
    """Show queue totals with source and HH-account breakdowns."""
    if getattr(bot_module, "_queue_stats_source_patch_installed", False):
        return

    def queue_stats() -> list[str]:
        session = bot_module.SessionLocal()

        try:
            vacancies_total = session.scalar(
                bot_module.select(
                    bot_module.func.count(bot_module.Vacancy.id)
                )
            ) or 0

            unprocessed = session.scalar(
                bot_module.select(
                    bot_module.func.count(bot_module.Vacancy.id)
                )
                .where(
                    bot_module.Vacancy.processed.is_(False)
                )
            ) or 0

            approved_rows = session.execute(
                bot_module.select(
                    bot_module.func.coalesce(
                        bot_module.Vacancy.source,
                        "hh",
                    ).label("source"),
                    bot_module.func.count(
                        bot_module.Application.id
                    ).label("count"),
                )
                .join(
                    bot_module.Vacancy,
                    bot_module.Vacancy.id
                    == bot_module.Application.vacancy_id,
                )
                .where(
                    bot_module.Application.status
                    == "approved"
                )
                .group_by(
                    bot_module.func.coalesce(
                        bot_module.Vacancy.source,
                        "hh",
                    )
                )
            ).all()

            approved_by_source = {
                str(source or "hh").strip().lower(): int(count or 0)
                for source, count in approved_rows
            }
            approved = sum(approved_by_source.values())

            labels = {
                "hh": "HH",
                "yandex": "Yandex",
                "vk": "VK",
                "tbank": "Т-Банк",
            }
            source_parts = [
                f"{labels[source]}: {approved_by_source.get(source, 0)}"
                for source in ("hh", "yandex", "vk", "tbank")
            ]

            for source in sorted(
                key
                for key in approved_by_source
                if key not in labels
            ):
                source_parts.append(
                    f"{source.upper()}: {approved_by_source[source]}"
                )

            hh_rows = session.execute(
                bot_module.select(
                    bot_module.Application.account_key,
                    bot_module.Application.status,
                    bot_module.func.count(
                        bot_module.Application.id
                    ),
                )
                .join(
                    bot_module.Vacancy,
                    bot_module.Vacancy.id
                    == bot_module.Application.vacancy_id,
                )
                .where(
                    bot_module.func.coalesce(
                        bot_module.Vacancy.source,
                        "hh",
                    )
                    == "hh",
                    bot_module.Application.status.in_(
                        (
                            "notified",
                            "approved",
                            "applying",
                            "manual_required",
                        )
                    ),
                )
                .group_by(
                    bot_module.Application.account_key,
                    bot_module.Application.status,
                )
            ).all()

            by_account: dict[str, dict[str, int]] = {}
            for account_key, status, count in hh_rows:
                key = str(account_key or "old")
                by_account.setdefault(key, {})[str(status)] = int(count or 0)

            applying = session.scalar(
                bot_module.select(
                    bot_module.func.count(bot_module.Application.id)
                )
                .where(
                    bot_module.Application.status
                    == "applying"
                )
            ) or 0

            result = [
                f"вакансий в БД: {vacancies_total}",
                f"не обработано: {unprocessed}",
                (
                    f"approved: {approved} "
                    f"({' · '.join(source_parts)})"
                ),
                f"applying: {applying}",
            ]

            for account in bot_module.all_accounts():
                counts = by_account.get(account.key, {})
                result.append(
                    f"{account.label}: "
                    f"notified {counts.get('notified', 0)} · "
                    f"approved {counts.get('approved', 0)} · "
                    f"applying {counts.get('applying', 0)} · "
                    f"manual {counts.get('manual_required', 0)}"
                )

            return result

        finally:
            session.close()

    bot_module._queue_stats = queue_stats
    bot_module._queue_stats_source_patch_installed = True
