"""CheckIn queries."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select

from pmchaser.db.models import CheckIn, Task


def checkins_for_task(session, task_id: int) -> list[CheckIn]:
    """Single-task lookup - still used by callers that only ever serialize
    one task (create_task, update_task, reassign_task) where a batch query
    would just be one query pretending to be a batch. See
    checkins_by_task_ids for the N+1 fix used by list-shaped callers."""
    return list(
        session.execute(
            select(CheckIn).where(CheckIn.task_id == task_id).order_by(CheckIn.sent_at.desc())
        ).scalars().all()
    )


def checkins_by_task_ids(session, task_ids: list[int]) -> dict[int, list[CheckIn]]:
    """One query for every task's check-ins, grouped by task_id - the
    Phase 2 fix for the refactor plan's finding #6. The original
    `pmchaser/serializers.py::task_summary` ran `checkins_for_task` once
    per task inside a loop (list_tasks/get_chase_plan/chase_now/
    get_digest_data all serialize N tasks), so a 200-task list cost ~200
    extra queries just for this. `ORDER BY task_id, sent_at DESC` means
    rows for the same task arrive contiguously and already in the
    per-task order task_summary expects, so grouping in Python is a
    single linear pass with no re-sorting needed.

    Returns an empty list (not a KeyError) for a task_id with no
    check-ins - callers should use `.get(task_id, [])`.
    """
    if not task_ids:
        return {}
    rows = session.execute(
        select(CheckIn)
        .where(CheckIn.task_id.in_(task_ids))
        .order_by(CheckIn.task_id, CheckIn.sent_at.desc())
    ).scalars().all()
    grouped: dict[int, list[CheckIn]] = defaultdict(list)
    for row in rows:
        grouped[row.task_id].append(row)
    return dict(grouped)


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
