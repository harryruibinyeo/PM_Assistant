"""get_chase_plan (the scheduled sweep) and chase_now (the manual
override) - business logic called by the thin adapters in
pmchaser/mcp/tools.py, which carry the full public docstrings.

See pmchaser/domain/chase_policy.py's module docstring for why these two
functions share only their genuinely-identical pieces (overdue-hours
arithmetic, urgency sort) rather than being merged into one
over-parameterized function - they differ in eligibility windows, skip
wording, and per-task output shape in ways that would make a forced merge
riskier than the two separate, explicit implementations below.
"""

from __future__ import annotations

from pmchaser.db import base as db_base
from pmchaser.domain import chase_policy
from pmchaser.domain.constants import CLOSED_STATUSES
from pmchaser.domain.time_utils import hours_between, iso_local
from pmchaser.integrations import telegram as telegram_integration
from pmchaser.repositories import people as people_repo
from pmchaser.repositories import tasks as tasks_repo
from pmchaser.serializers import task_summary
from pmchaser.services.messaging import telegram_get_updates


def get_chase_plan(due_soon_hours: int = 24, max_unanswered: int = 3) -> dict:
    updates = telegram_get_updates()
    telegram_error = updates.get("error")

    with db_base.session_scope() as session:
        now = db_base.utcnow()
        local_tz = db_base.LOCAL_TZ

        # The first registered manager by id, regardless of how many exist
        # - NOT the same "exactly one, else None" resolution create_task
        # uses for its manager auto-resolve (people_repo.get_single_manager).
        manager_candidates = people_repo.list_managers(session)
        manager = manager_candidates[0] if manager_candidates else None
        tasks = tasks_repo.list_all(session)

        candidates: list[dict] = []
        skipped: list[dict] = []
        to_escalate: list[dict] = []
        unreachable: dict[str, dict] = {}

        for task in tasks:
            if task.status in CLOSED_STATUSES:
                continue
            if not task.deadline:
                continue

            hours_left = hours_between(task.deadline, now)
            overdue = chase_policy.is_overdue(hours_left)
            due_soon = chase_policy.is_due_soon(hours_left, due_soon_hours)
            if not (overdue or due_soon):
                continue

            info = task_summary(session, task, now)

            if not info["owner_is_linked"]:
                owner = task.owner
                if owner is not None:
                    entry = unreachable.setdefault(
                        owner.name,
                        {
                            "name": owner.name,
                            "link_code": owner.link_code,
                            "link_url": telegram_integration.build_link_url(owner.link_code),
                            "task_count": 0,
                        },
                    )
                    entry["task_count"] += 1
                skipped.append({
                    "task_id": task.id,
                    "title": task.title,
                    "owner_name": info["owner_name"],
                    "reason": "owner has not linked Telegram and cannot receive messages",
                })
                continue

            # A blocked task is waiting on something the owner cannot fix, and
            # they have usually already said so. Chasing them again is noise -
            # it needs the manager to unblock it or move the date, so it goes
            # to the digest instead.
            if task.status == "blocked":
                skipped.append({
                    "task_id": task.id,
                    "title": task.title,
                    "owner_name": info["owner_name"],
                    "reason": "blocked — needs the manager to unblock or reschedule, "
                              "not the owner to be chased again",
                })
                continue

            if info["unanswered_checkin_count"] >= max_unanswered:
                to_escalate.append({
                    "task_id": task.id,
                    "title": task.title,
                    "owner_name": info["owner_name"],
                    "manager_name": info["manager_name"],
                    "unanswered_checkin_count": info["unanswered_checkin_count"],
                    "hours_overdue": chase_policy.hours_overdue(hours_left),
                    "deadline_local": info["deadline_local"],
                })
                continue

            since = info["hours_since_last_checkin"]
            floor = chase_policy.reping_floor_hours(task.priority)
            if since is not None and since < floor:
                skipped.append({
                    "task_id": task.id,
                    "title": task.title,
                    "owner_name": info["owner_name"],
                    "reason": f"pinged {since}h ago, under the {floor}h floor for {task.priority} priority",
                })
                continue

            candidates.append({
                "task_id": task.id,
                "title": task.title,
                "owner_name": info["owner_name"],
                "deadline_local": info["deadline_local"],
                "hours_overdue": chase_policy.hours_overdue(hours_left),
                "hours_until_deadline": hours_left,
                "priority": task.priority,
                "status": task.status,
                "progress_pct": task.progress_pct,
                "unanswered_checkin_count": info["unanswered_checkin_count"],
                "previously_chased": info["total_checkins"] > 0,
            })

        # Most overdue first, then by priority.
        candidates = chase_policy.sort_by_urgency(candidates)

        to_chase: list[dict] = []
        claimed: set[str] = set()
        for cand in candidates:
            owner = cand["owner_name"]
            if owner in claimed:
                skipped.append({
                    "task_id": cand["task_id"],
                    "title": cand["title"],
                    "owner_name": owner,
                    "reason": "a more urgent task for this person is being chased this run",
                })
                continue
            claimed.add(owner)
            to_chase.append(cand)

        # Grouped by (owner, manager) - not left as a flat per-task list -
        # mirrors how `to_chase` is already deduplicated to one entry per
        # person. Keying on manager too (not just owner) means an owner
        # with escalating tasks under two different managers correctly
        # produces two entries - one per manager to notify - instead of
        # silently merging into a single entry with only one manager visible.
        escalations_by_owner: dict[tuple[str, str | None], list[dict]] = {}
        for entry in to_escalate:
            key = (entry["owner_name"], entry["manager_name"])
            escalations_by_owner.setdefault(key, []).append({
                "task_id": entry["task_id"],
                "title": entry["title"],
                "unanswered_checkin_count": entry["unanswered_checkin_count"],
                "hours_overdue": entry["hours_overdue"],
                "deadline_local": entry["deadline_local"],
            })
        to_escalate = [
            {"owner_name": owner, "manager_name": manager_name, "tasks": owner_tasks}
            for (owner, manager_name), owner_tasks in escalations_by_owner.items()
        ]

        plan = {
            "replies_to_interpret": updates.get("replies", []),
            "unmatched_to_resolve": updates.get("unmatched", []),
            "newly_linked": updates.get("linked", []),
            "to_chase": to_chase,
            "to_escalate": to_escalate,
            "unreachable": list(unreachable.values()),
            "skipped": skipped,
            "manager_name": manager.name if manager else None,
            "checked_at_local": iso_local(now, local_tz),
        }
        if telegram_error:
            plan["telegram_error"] = telegram_error

        bits = []
        if plan["replies_to_interpret"]:
            bits.append(f"{len(plan['replies_to_interpret'])} reply/replies to interpret")
        if plan["unmatched_to_resolve"]:
            bits.append(f"{len(plan['unmatched_to_resolve'])} unmatched message(s)")
        if to_chase:
            bits.append(f"{len(to_chase)} to chase")
        if to_escalate:
            escalated_tasks = sum(len(e["tasks"]) for e in to_escalate)
            bits.append(f"{escalated_tasks} task(s) to escalate across {len(to_escalate)} owner(s)")
        if plan["unreachable"]:
            bits.append(f"{len(plan['unreachable'])} unreachable owner(s)")
        plan["summary"] = "; ".join(bits) if bits else "nothing to do this run"
        return plan


def chase_now(owner_name: str) -> dict:
    updates = telegram_get_updates()
    telegram_error = updates.get("error")

    with db_base.session_scope() as session:
        owner = people_repo.find_by_name(session, owner_name)
        if owner is None:
            return {"error": f"No registered person named '{owner_name}'."}

        if not owner.telegram_chat_id:
            return {
                "owner_name": owner.name,
                "reachable": False,
                "tasks": [],
                "link_code": owner.link_code,
                "link_url": telegram_integration.build_link_url(owner.link_code),
                "message": f"'{owner.name}' has not linked Telegram and cannot be messaged.",
            }

        now = db_base.utcnow()
        tasks = tasks_repo.list_for_owner(session, owner.id)

        matched: list[dict] = []
        skipped: list[dict] = []
        for task in tasks:
            if task.status in CLOSED_STATUSES:
                continue

            if task.status == "blocked":
                skipped.append({
                    "task_id": task.id,
                    "title": task.title,
                    "reason": "blocked — needs the manager to unblock or reschedule, "
                              "not another ping",
                })
                continue

            hours_left = hours_between(task.deadline, now)

            info = task_summary(session, task, now)
            matched.append({
                "task_id": task.id,
                "title": task.title,
                "manager_name": info["manager_name"],
                "deadline_local": info["deadline_local"],
                "hours_overdue": chase_policy.hours_overdue(hours_left),
                "hours_until_deadline": hours_left,
                "priority": task.priority,
                "status": task.status,
                "progress_pct": task.progress_pct,
                "unanswered_checkin_count": info["unanswered_checkin_count"],
                "previously_chased": info["total_checkins"] > 0,
            })

        matched = chase_policy.sort_by_urgency(matched)

        result = {
            "owner_name": owner.name,
            "reachable": True,
            "tasks": matched,
            "skipped": skipped,
            # This call's own telegram_get_updates() above may have picked up
            # replies or unmatched messages (from anyone, not just this
            # owner) - surface them rather than silently discarding them, or
            # they are never seen or interpreted by anyone.
            "replies_to_interpret": updates.get("replies", []),
            "unmatched_to_resolve": updates.get("unmatched", []),
            "newly_linked": updates.get("linked", []),
        }
        if telegram_error:
            result["telegram_error"] = telegram_error
        if not matched:
            result["message"] = (
                f"'{owner.name}' has no open task to chase right now."
                if not skipped else
                f"'{owner.name}'s only open task(s) are blocked — that needs the manager, not a ping."
            )
        return result
