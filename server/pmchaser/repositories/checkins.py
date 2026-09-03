"""CheckIn queries.

`checkins_for_task` here is deliberately unchanged - one query per task,
called in a loop by pmchaser/serializers.py for every task in a list. This
is the N+1 pattern flagged as a Phase 2 performance finding; Phase 1 is a
behavior-identical structural move only, so the fix (eager loading /
grouped aggregation) is deferred there rather than mixed into this pass.
"""

from __future__ import annotations

from sqlalchemy import select

from pmchaser.db.models import CheckIn, Task


def checkins_for_task(session, task_id: int) -> list[CheckIn]:
    return list(
        session.execute(
            select(CheckIn).where(CheckIn.task_id == task_id).order_by(CheckIn.sent_at.desc())
        ).scalars().all()
    )


def find_by_telegram_message_id(session, telegram_message_id: int, owner_id: int) -> CheckIn | None:
    """The check-in a threaded Telegram reply answers, if the reply-to
    message ID matches one of this owner's outstanding pings."""
    return session.execute(
        select(CheckIn)
        .join(Task)
        .where(
            CheckIn.telegram_message_id == telegram_message_id,
            Task.owner_id == owner_id,
        )
    ).scalars().first()


def find_open_for_owner(session, owner_id: int) -> list[CheckIn]:
    """Every unanswered, already-sent check-in for this owner, most
    recent first - used to resolve an un-threaded reply when exactly one
    is outstanding (or to report ambiguity when more than one is)."""
    return list(
        session.execute(
            select(CheckIn)
            .join(Task)
            .where(
                Task.owner_id == owner_id,
                CheckIn.reply_text.is_(None),
                CheckIn.sent_at.is_not(None),
            )
            .order_by(CheckIn.sent_at.desc())
        ).scalars().all()
    )
