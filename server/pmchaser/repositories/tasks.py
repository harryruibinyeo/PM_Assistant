"""Task queries.

Phase 2 fix for the refactor plan's findings #6-7 (N+1 queries; filtering
done in Python instead of SQL): every query below eager-loads `owner`/
`manager` via `selectinload` (2 extra queries total, not one per task -
SQLAlchemy batches selectinload across the whole result set), and the
status/deadline/owner filters that used to be a Python loop over every
task in list_tasks/get_chase_plan/get_digest_data are now WHERE clauses.

The status/deadline push-downs are safe rewrites, not behavior changes:
in the original tools.py, a task failing any of these checks was
excluded from every result bucket (never added to `skipped`, `to_chase`,
or anything else - a bare `continue`), so filtering it out before it's
even fetched produces identical final output, just without ever
constructing rows nothing was going to look at. See tests/test_golden_master.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from pmchaser.db.models import Task
from pmchaser.domain.constants import CLOSED_STATUSES, OPEN_STATUSES


def _with_relations(stmt):
    return stmt.options(selectinload(Task.owner), selectinload(Task.manager))


def list_open(session) -> list[Task]:
    """Every task not in a closed status - what get_digest_data needs
    (it has no deadline requirement, unlike the chase-eligible set)."""
    return list(
        session.execute(
            _with_relations(select(Task).where(Task.status.notin_(CLOSED_STATUSES)))
        ).scalars().all()
    )


def list_chase_eligible(session, now: datetime, due_soon_hours: int) -> list[Task]:
    """Not closed, has a deadline, and is either overdue or due within
    `due_soon_hours` - what get_chase_plan's scheduled sweep considers at
    all, before any of its per-task business rules (floor, escalation,
    blocked, unreachable) run.

    `deadline <= now + due_soon_hours` alone is mathematically equivalent
    to the original "(deadline < now) OR (now <= deadline <= now +
    due_soon_hours)" - overdue-or-due-soon - given due_soon_hours >= 0:
    every deadline before `now` is trivially <= `now + due_soon_hours`
    too, so the separate lower bound in the due-soon half never excludes
    anything the overdue half didn't already admit.
    """
    cutoff = now + timedelta(hours=due_soon_hours)
    return list(
        session.execute(
            _with_relations(
                select(Task).where(
                    Task.status.notin_(CLOSED_STATUSES),
                    Task.deadline.is_not(None),
                    Task.deadline <= cutoff,
                )
            )
        ).scalars().all()
    )


def list_for_owner(session, owner_id: int) -> list[Task]:
    return list(
        session.execute(
            _with_relations(select(Task).where(Task.owner_id == owner_id))
        ).scalars().all()
    )


def list_filtered(
    session, filter: str, owner_id: int | None, due_soon_hours: int, now: datetime,
) -> list[Task]:
    """Backs list_tasks. `owner_id=None` means "no owner filter requested"
    (list_tasks' own owner_name was empty/omitted) - distinct from
    "requested but no such person", which the caller must turn into an
    empty result itself before ever reaching here (see
    pmchaser/services/tasks.py::list_tasks), matching the original
    per-task `if not task.owner or task.owner.name...: continue` exactly:
    an unregistered name matched nothing there either.
    """
    stmt = _with_relations(select(Task))
    if owner_id is not None:
        stmt = stmt.where(Task.owner_id == owner_id)
    if filter in ("active", "overdue", "due_soon"):
        stmt = stmt.where(Task.status.notin_(CLOSED_STATUSES))
    if filter == "overdue":
        stmt = stmt.where(Task.deadline.is_not(None), Task.deadline < now)
    elif filter == "due_soon":
        stmt = stmt.where(
            Task.deadline.is_not(None),
            Task.deadline >= now,
            Task.deadline <= now + timedelta(hours=due_soon_hours),
        )
    return list(session.execute(stmt).scalars().all())


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
