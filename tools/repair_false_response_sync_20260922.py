from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select

from app.db import Application, ApplicationEvent, SessionLocal


BAD_RUN_START = datetime(2026, 9, 21, 22, 33, 0)
BAD_RUN_END = datetime(2026, 9, 21, 22, 37, 0)
BAD_SOURCE = "hh_negotiations"
BAD_EVENT = "career_rejected"


def main() -> int:
    session = SessionLocal()
    try:
        bad_events = session.execute(
            select(ApplicationEvent.id, ApplicationEvent.application_id)
            .where(
                ApplicationEvent.source == BAD_SOURCE,
                ApplicationEvent.event_type == BAD_EVENT,
                ApplicationEvent.observed_at >= BAD_RUN_START,
                ApplicationEvent.observed_at <= BAD_RUN_END,
            )
        ).all()

        application_ids = sorted(
            {int(application_id) for _, application_id in bad_events}
        )

        repaired = 0
        if application_ids:
            applications = session.scalars(
                select(Application).where(
                    Application.id.in_(application_ids)
                )
            ).all()

            for application in applications:
                if application.career_status != "rejected":
                    continue
                application.career_status = "submitted"
                application.response_checked_at = None
                repaired += 1

            session.execute(
                delete(ApplicationEvent).where(
                    ApplicationEvent.source == BAD_SOURCE,
                    ApplicationEvent.event_type == BAD_EVENT,
                    ApplicationEvent.observed_at >= BAD_RUN_START,
                    ApplicationEvent.observed_at <= BAD_RUN_END,
                )
            )

        session.commit()
        print(
            "[REPAIR RESPONSE SYNC 20260922] "
            f"bad_events={len(bad_events)} "
            f"applications={len(application_ids)} repaired={repaired}"
        )
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
