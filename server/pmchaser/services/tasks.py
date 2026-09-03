"""Task CRUD business logic - called by the thin adapters in
pmchaser/mcp/tools.py, which carry the full public docstrings (the actual
prompt content - see that module's docstring)."""

from __future__ import annotations

from datetime import timedelta

from pmchaser.db import base as db_base
from pmchaser.db.models import CheckIn, Person, Task
from pmchaser.domain.constants import DUPLICATE_TASK_WINDOW_MINUTES
from pmchaser.domain.time_utils import parse_dt
from pmchaser.domain.validation import validate_priority
from pmchaser.repositories import checkins as checkins_repo
from pmchaser.repositories import people as people_repo
from pmchaser.repositories import tasks as tasks_repo
from pmchaser.repositories import unmatched as unmatched_repo
from pmchaser.serializers import task_summary


def _create_task_row(
    session,
    title: str,
    owner_name: str,
    priority: str,
    deadline: str | None = None,
    description: str | None = None,
    manager_name: str | None = None,
) -> dict:
    """Shared row-creation logic for create_task and create_tasks_bulk.

    Takes an already-open session so create_tasks_bulk can create many rows
    in one transaction instead of one per tool call.
    """
    priority_error = validate_priority(priority)
    if priority_error:
        return {"error": priority_error}

    owner = people_repo.find_by_name(session, owner_name)
    if owner is None:
        return {
            "error": f"No registered person named '{owner_name}'. "
            "Register them with register_person first."
        }

    if manager_name:
        manager = people_repo.find_by_name(session, manager_name)
        if manager is None:
            return {"error": f"No registered person named '{manager_name}'."}
    else:
        manager = people_repo.get_single_manager(session)

    parsed_deadline = parse_dt(deadline, db_base.LOCAL_TZ)

    duplicate_cutoff = db_base.utcnow() - timedelta(minutes=DUPLICATE_TASK_WINDOW_MINUTES)
    existing = tasks_repo.find_duplicate(session, title, owner.id, parsed_deadline, duplicate_cutoff)
    if existing is not None:
        result = task_summary(session, existing)
        result["duplicate_of_existing"] = True
        result["warning"] = (
            f"An open task titled '{existing.title}' for {owner.name} with the same "
            f"deadline was already created {DUPLICATE_TASK_WINDOW_MINUTES} minutes "
            "ago or less — returning that existing task instead of creating another. "
            "If this really is meant to be a separate task, vary the title or deadline."
        )
        return result

    task = Task(
        title=title,
        description=description,
        owner_id=owner.id,
        manager_id=manager.id if manager else None,
        deadline=parsed_deadline,
        priority=priority,
    )
    session.add(task)
    session.flush()
    result = task_summary(session, task)
    if not owner.telegram_chat_id:
        result["warning"] = (
            f"'{owner.name}' has not linked Telegram yet and cannot be "
            f"messaged. Their link code is {owner.link_code}."
        )
    return result


def create_task(
    title: str,
    owner_name: str,
    priority: str,
    deadline: str | None = None,
    description: str | None = None,
    manager_name: str | None = None,
) -> dict:
    with db_base.session_scope() as session:
        return _create_task_row(
            session,
            title=title,
            owner_name=owner_name,
            priority=priority,
            deadline=deadline,
            description=description,
            manager_name=manager_name,
        )


def create_tasks_bulk(tasks: list[dict]) -> dict:
    created: list[dict] = []
    failed: list[dict] = []
    with db_base.session_scope() as session:
        for row in tasks:
            result = _create_task_row(
                session,
                title=row.get("title", ""),
                owner_name=row.get("owner_name", ""),
                priority=row.get("priority", ""),
                deadline=row.get("deadline"),
                description=row.get("description"),
                manager_name=row.get("manager_name"),
            )
            if "error" in result:
                failed.append({"input": row, "error": result["error"]})
            else:
                created.append(result)

    return {
        "created": created,
        "failed": failed,
        "summary": f"{len(created)} created, {len(failed)} failed" if failed
        else f"{len(created)} created",
    }


def list_tasks(
    filter: str = "all",
    owner_name: str | None = None,
    due_soon_hours: int = 24,
) -> list[dict]:
    with db_base.session_scope() as session:
        now = db_base.utcnow()

        wanted_owner = (owner_name or "").strip()
        owner_id = None
        if wanted_owner:
            owner = people_repo.find_by_name(session, wanted_owner)
            if owner is None:
                # No such person - nothing can match. Matches the
                # original per-task owner-name check, which also matched
                # zero tasks for an unregistered name.
                return []
            owner_id = owner.id

        tasks = tasks_repo.list_filtered(session, filter, owner_id, due_soon_hours, now)
        checkins_by_task = checkins_repo.checkins_by_task_ids(session, [t.id for t in tasks])
        return [
            task_summary(session, t, now, checkins=checkins_by_task.get(t.id, []))
            for t in tasks
        ]


def update_task(
    task_id: int,
    status: str | None = None,
    progress_pct: int | None = None,
    deadline: str | None = None,
    priority: str | None = None,
    title: str | None = None,
    owner_name: str | None = None,
) -> dict:
    if priority is not None:
        priority_error = validate_priority(priority)
        if priority_error:
            return {"error": priority_error}

    with db_base.session_scope() as session:
        task = session.get(Task, task_id)
        if task is None:
            return {"error": f"No task with id {task_id}"}

        if owner_name is not None:
            new_owner = people_repo.find_by_name(session, owner_name)
            if new_owner is None:
                return {"error": f"No registered person named '{owner_name}'."}
            task.owner_id = new_owner.id

        if status is not None:
            task.status = status
        if progress_pct is not None:
            task.progress_pct = max(0, min(100, progress_pct))
        if deadline is not None:
            task.deadline = parse_dt(deadline, db_base.LOCAL_TZ)
        if priority is not None:
            task.priority = priority
        if title is not None:
            task.title = title

        # A task can't be done and partially complete at the same time. Only
        # enforced in this direction: 100% with status "in_progress" is a
        # legitimate state (finished but awaiting review), so it's left alone.
        if task.status == "done":
            task.progress_pct = 100

        session.flush()
        return task_summary(session, task)


def reassign_task(task_id: int, new_owner_name: str) -> dict:
    with db_base.session_scope() as session:
        task = session.get(Task, task_id)
        if task is None:
            return {"error": f"No task with id {task_id}"}
        new_owner = people_repo.find_by_name(session, new_owner_name)
        if new_owner is None:
            return {"error": f"No registered person named '{new_owner_name}'."}
        # Looked up independently of the task.owner relationship (not via
        # task.owner.name) - touching that relationship here would cache the
        # old Person on the ORM identity map, and setting owner_id directly
        # afterward doesn't invalidate that cache, leaving task_summary's own
        # task.owner.name read stale.
        old_owner = session.get(Person, task.owner_id)
        old_owner_name = old_owner.name if old_owner else None
        task.owner_id = new_owner.id
        session.flush()
        result = task_summary(session, task)
        result["reassigned_from"] = old_owner_name
        return result


def delete_task(task_id: int) -> dict:
    with db_base.session_scope() as session:
        task = session.get(Task, task_id)
        if task is None:
            return {"error": f"No task with id {task_id}"}
        title = task.title
        # Phase 2 fix (refactor plan finding #1): exact JSON-membership
        # match instead of a substring LIKE - see
        # pmchaser/repositories/unmatched.py's delete_referencing_task for
        # why the original was wrong (it deleted unrelated messages whose
        # candidate list merely contained this task_id as a substring,
        # e.g. deleting task 5 also deleted a message with candidates
        # [15, 25, 51]).
        unmatched_repo.delete_referencing_task(session, task_id)
        session.execute(CheckIn.__table__.delete().where(CheckIn.task_id == task_id))
        session.delete(task)
        return {"deleted": True, "task_id": task_id, "title": title}
