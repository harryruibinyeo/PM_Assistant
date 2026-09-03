"""get_digest_data business logic - called by the thin adapter in
pmchaser/mcp/tools.py, which carries the full public docstring."""

from __future__ import annotations

from pmchaser.db import base as db_base
from pmchaser.domain.constants import PRIORITY_RANK
from pmchaser.domain.time_utils import iso_local
from pmchaser.repositories import checkins as checkins_repo
from pmchaser.repositories import people as people_repo
from pmchaser.repositories import tasks as tasks_repo
from pmchaser.repositories import unmatched as unmatched_repo
from pmchaser.serializers import person_summary, task_summary


def get_digest_data(at_risk_hours: int = 24) -> dict:
    with db_base.session_scope() as session:
        now = db_base.utcnow()
        local_tz = db_base.LOCAL_TZ

        # Fetched once and reused for both `managers` (ordered by id - see
        # list_managers) and the unreachable-people list below (which
        # needs every person, ordered by name - see list_people): managers
        # are always a subset of all_people, so one grouped open-task-count
        # query covers both instead of querying per person in either list
        # - the person-level N+1 found while benchmarking finding #6's
        # task-level fix (same shape, see
        # people_repo.count_open_tasks_by_owner_ids's docstring).
        all_people = people_repo.list_people(session)
        open_counts = people_repo.count_open_tasks_by_owner_ids(session, [p.id for p in all_people])

        managers = [
            person_summary(session, p, open_task_count=open_counts.get(p.id, 0))
            for p in people_repo.list_managers(session)
        ]

        # Pre-filtered to non-closed statuses in SQL - see
        # tasks_repo.list_open's docstring.
        tasks = tasks_repo.list_open(session)
        checkins_by_task = checkins_repo.checkins_by_task_ids(session, [t.id for t in tasks])

        active: list[dict] = []
        overdue = 0
        unresponsive: list[dict] = []
        blocked: list[dict] = []
        for task in tasks:
            info = task_summary(session, task, now, checkins=checkins_by_task.get(task.id, []))

            if task.status == "blocked":
                # These are deliberately not chased any more (see
                # pmchaser/services/chase.py's get_chase_plan), so the
                # digest is the only place they surface. Carry the owner's
                # own words and whether the deadline has already gone,
                # since the manager's likely action is to unblock it or
                # move the date.
                blocked.append({
                    "task_id": info["task_id"],
                    "title": info["title"],
                    "owner_name": info["owner_name"],
                    "manager_name": info["manager_name"],
                    "deadline_local": info["deadline_local"],
                    "hours_until_deadline": info["hours_until_deadline"],
                    "deadline_already_passed": (
                        info["hours_until_deadline"] is not None
                        and info["hours_until_deadline"] < 0
                    ),
                    "reason_given": info["last_reply_text"],
                    "said_at_local": info["last_reply_at_local"],
                })
            hours_left = info["hours_until_deadline"]
            info["is_overdue"] = hours_left is not None and hours_left < 0
            info["due_within_window"] = (
                hours_left is not None and 0 <= hours_left <= at_risk_hours
            )
            if info["is_overdue"]:
                overdue += 1
            if info["unanswered_checkin_count"] >= 2:
                unresponsive.append({
                    "owner_name": info["owner_name"],
                    "manager_name": info["manager_name"],
                    "task_id": info["task_id"],
                    "title": info["title"],
                    "unanswered_checkin_count": info["unanswered_checkin_count"],
                })
            active.append(info)

        active.sort(
            key=lambda t: (
                t["hours_until_deadline"] if t["hours_until_deadline"] is not None else 1e9,
                PRIORITY_RANK.get(t["priority"], 1),
            )
        )

        unreachable = [
            person_summary(session, p, open_task_count=open_counts.get(p.id, 0))
            for p in all_people
            if not p.telegram_chat_id
        ]
        unreachable = [p for p in unreachable if p["open_task_count"] > 0]

        pending_unmatched = unmatched_repo.count_pending(session)

        return {
            "manager_name": managers[0]["name"] if managers else None,
            "managers": managers,
            "generated_at_local": iso_local(now, local_tz),
            "active_tasks": active,
            "active_count": len(active),
            "overdue_count": overdue,
            "blocked_needing_decision": blocked,
            "unresponsive": unresponsive,
            "unreachable_people": unreachable,
            "pending_unmatched_count": pending_unmatched,
        }
