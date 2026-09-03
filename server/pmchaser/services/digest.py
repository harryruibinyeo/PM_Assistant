"""get_digest_data business logic - called by the thin adapter in
pmchaser/mcp/tools.py, which carries the full public docstring."""

from __future__ import annotations

from pmchaser.db import base as db_base
from pmchaser.domain.constants import CLOSED_STATUSES, PRIORITY_RANK
from pmchaser.domain.time_utils import iso_local
from pmchaser.repositories import people as people_repo
from pmchaser.repositories import tasks as tasks_repo
from pmchaser.repositories import unmatched as unmatched_repo
from pmchaser.serializers import person_summary, task_summary


def get_digest_data(at_risk_hours: int = 24) -> dict:
    with db_base.session_scope() as session:
        now = db_base.utcnow()
        local_tz = db_base.LOCAL_TZ

        managers = [person_summary(session, p) for p in people_repo.list_managers(session)]

        tasks = tasks_repo.list_all(session)

        active: list[dict] = []
        overdue = 0
        unresponsive: list[dict] = []
        blocked: list[dict] = []
        for task in tasks:
            if task.status in CLOSED_STATUSES:
                continue
            info = task_summary(session, task, now)

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
            person_summary(session, p)
            for p in people_repo.list_people(session)
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
