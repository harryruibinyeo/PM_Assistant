"""Builds the dicts every tool actually returns to the agent.

Anything the Skill needs to make a decision about (has this been pinged
recently? how many pings went unanswered? has this person linked
Telegram?) is returned as an explicit field rather than left to be
inferred - see task_summary/person_summary below. Renamed from tools.py's
_task_dict/_person_dict as part of the Phase 1 structural refactor; output
shape is unchanged (guarded by tests/test_golden_master.py).
"""

from __future__ import annotations

from datetime import datetime

from pmchaser.db import base as db_base
from pmchaser.db.models import Person, Task
from pmchaser.domain.time_utils import hours_between, iso_local, iso_utc
from pmchaser.integrations import telegram as telegram_integration
from pmchaser.repositories import checkins as checkins_repo
from pmchaser.repositories import people as people_repo


def person_summary(session, person: Person, open_task_count: int | None = None) -> dict:
    """`open_task_count`, when given, must be exactly what
    pmchaser.repositories.people.count_open_tasks(session, person.id)
    would have returned. Batch callers (list_people,
    get_digest_data's unreachable-people list) pass in a pre-fetched
    dict from count_open_tasks_by_owner_ids so this function never issues
    its own query - same pattern, and same reasoning, as
    task_summary's `checkins` parameter."""
    open_count = (
        open_task_count if open_task_count is not None
        else people_repo.count_open_tasks(session, person.id)
    )
    return {
        "person_id": person.id,
        "name": person.name,
        "role": person.role,
        "telegram_username": person.telegram_username,
        "is_linked": bool(person.telegram_chat_id),
        # Only useful while still unlinked - it's how they complete linking.
        "link_code": person.link_code if not person.telegram_chat_id else None,
        "link_url": (
            telegram_integration.build_link_url(person.link_code)
            if not person.telegram_chat_id
            else None
        ),
        "open_task_count": open_count,
    }


def task_summary(session, task: Task, now: datetime | None = None, checkins: list | None = None) -> dict:
    """`checkins`, when given, must be exactly what
    pmchaser.repositories.checkins.checkins_for_task(session, task.id)
    would have returned (all check-ins for this task, any order - this
    function re-derives "most recent" itself). Batch callers
    (list_tasks/get_chase_plan/chase_now/get_digest_data) pass in a
    pre-fetched, grouped-by-task-id list from checkins_by_task_ids so this
    function never issues its own query - the Phase 2 fix for the
    refactor plan's finding #6 (N+1: one query per task, per call, for
    every list-shaped tool). Single-task callers (create_task,
    update_task, reassign_task) omit it and get the single-query fallback.
    """
    now = now or db_base.utcnow()
    local_tz = db_base.LOCAL_TZ

    check_ins = checkins if checkins is not None else checkins_repo.checkins_for_task(session, task.id)
    sent = [c for c in check_ins if c.sent_at is not None]
    last = sent[0] if sent else None

    # How many pings in a row have gone unanswered, most recent first. This
    # is what an "escalate after N ignored pings" rule actually keys off.
    unanswered_streak = 0
    for c in sent:
        if c.reply_text:
            break
        unanswered_streak += 1

    # The most recent thing this person actually said about the task,
    # whether it answered a ping or arrived unprompted. The digest needs
    # this to quote real blockers instead of reporting "some tasks are
    # blocked".
    answered = [c for c in check_ins if c.reply_text]
    answered.sort(key=lambda c: c.reply_received_at or datetime.min, reverse=True)
    last_reply = answered[0] if answered else None

    return {
        "task_id": task.id,
        "title": task.title,
        "description": task.description,
        "owner_name": task.owner.name if task.owner else None,
        "owner_is_linked": bool(task.owner and task.owner.telegram_chat_id),
        "manager_name": task.manager.name if task.manager else None,
        "deadline_utc": iso_utc(task.deadline),
        "deadline_local": iso_local(task.deadline, local_tz),
        # Negative means overdue. Pre-computed so the agent never does date math.
        "hours_until_deadline": hours_between(task.deadline, now),
        "priority": task.priority,
        "status": task.status,
        "progress_pct": task.progress_pct,
        "updated_at_local": iso_local(task.updated_at, local_tz),
        "last_checkin_sent_at_local": iso_local(last.sent_at, local_tz) if last else None,
        "hours_since_last_checkin": hours_between(now, last.sent_at) if last else None,
        "last_checkin_answered": bool(last.reply_text) if last else None,
        "unanswered_checkin_count": unanswered_streak,
        "total_checkins": len(sent),
        "last_reply_text": last_reply.reply_text if last_reply else None,
        "last_reply_at_local": iso_local(last_reply.reply_received_at, local_tz) if last_reply else None,
    }
