"""Task queries."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from pmchaser.db.models import Task
from pmchaser.domain.constants import OPEN_STATUSES


def list_all(session) -> list[Task]:
    return list(session.execute(select(Task)).scalars().all())


def list_for_owner(session, owner_id: int) -> list[Task]:
    return list(session.execute(select(Task).where(Task.owner_id == owner_id)).scalars().all())


def find_duplicate(
    session, title: str, owner_id: int, deadline: datetime | None, created_after: datetime,
) -> Task | None:
    """An open task with the same (title, owner, deadline) created after
    `created_after` - the guard against a repeated create_task call being
    an accidental resubmission rather than a deliberate new task."""
    return session.execute(
        select(Task).where(
            func.lower(Task.title) == title.strip().lower(),
            Task.owner_id == owner_id,
            Task.deadline == deadline,
            Task.status.in_(OPEN_STATUSES),
            Task.created_at >= created_after,
        )
    ).scalars().first()
