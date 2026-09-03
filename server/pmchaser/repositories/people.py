"""Person queries."""

from __future__ import annotations

from sqlalchemy import func, select

from pmchaser.db.models import Person, Task
from pmchaser.domain.constants import OPEN_STATUSES


def find_by_name(session, name: str) -> Person | None:
    """Look up a person by name, case-insensitively.

    Case-insensitive because the agent types these names from context and
    will eventually send "alice" where the record says "Alice".
    """
    stmt = select(Person).where(func.lower(Person.name) == (name or "").strip().lower())
    return session.execute(stmt).scalar_one_or_none()


def list_people(session, role: str | None = None) -> list[Person]:
    stmt = select(Person).order_by(Person.name)
    if role:
        stmt = stmt.where(Person.role == role)
    return list(session.execute(stmt).scalars().all())


def list_managers(session) -> list[Person]:
    return list(
        session.execute(
            select(Person).where(Person.role == "manager").order_by(Person.id)
        ).scalars().all()
    )


def get_single_manager(session) -> Person | None:
    """The sole registered manager, if exactly one exists - used to
    auto-resolve `manager_name` when it's omitted (today's single-manager
    setup). None if there are zero or more than one."""
    candidates = list_managers(session)
    return candidates[0] if len(candidates) == 1 else None


def count_open_tasks(session, person_id: int) -> int:
    return session.execute(
        select(func.count(Task.id)).where(
            Task.owner_id == person_id, Task.status.in_(OPEN_STATUSES)
        )
    ).scalar_one()


def count_all_tasks(session, person_id: int) -> int:
    return session.execute(
        select(func.count(Task.id)).where(Task.owner_id == person_id)
    ).scalar_one()
