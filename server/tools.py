"""The tools pm-chaser-mcp exposes to Odysseus's agent.

This service does data storage, Telegram I/O, and the mechanical filtering
that decides WHICH tasks are candidates for chasing. It never writes a
message and never interprets one — composing every outbound message and
reading every reply stays with the agent.

That split was not the original design. The first version left the filtering
to the agent as well, which meant five to eight tool calls of date arithmetic
and list-winnowing before it reached the one call that actually did anything.
The local 27B model reliably got lost in that bookkeeping: across twelve
rounds it announced "sending the chase message now" six times and never once
emitted the send. Rules that must always hold ("never re-ping within four
hours") also can't live in prose the model may skip. So the mechanical part
moved into get_chase_plan() below, which does it in one call and guarantees
the rules — while still returning everything it filtered out, with reasons,
so the agent can knowingly override.

Two conventions worth knowing when reading this file:

* Datetimes are stored naive-UTC (see models.py). Input is converted from
  local time on the way in, and output carries both a UTC and a local
  rendering plus pre-computed hour deltas — the agent should never have to
  do date arithmetic itself, because LLMs are unreliable at it.
* Anything the Skill needs to make a decision about (has this been pinged
  recently? how many pings went unanswered? has this person linked Telegram?)
  is returned as an explicit field rather than left to be inferred.
"""

from __future__ import annotations

import json
import os
import random
import string
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

import telegram_client
from models import (
    LOCAL_TZ,
    BotState,
    CheckIn,
    Person,
    Task,
    UnmatchedMessage,
    session_scope,
)
from telegram_client import TelegramNotConfigured

_LINK_CODE_ALPHABET = string.ascii_uppercase + string.digits

OPEN_STATUSES = ("not_started", "in_progress", "blocked")
CLOSED_STATUSES = ("done", "cancelled")

# Priority now drives how often a task gets re-chased (get_chase_plan below),
# not just display ordering — so it's validated on the way in rather than
# accepting any string silently.
VALID_PRIORITIES = ("low", "medium", "high")
_PRIORITY_CHASE_FLOOR_HOURS = {"high": 1, "medium": 6, "low": 24}


def _validate_priority(priority: str) -> str | None:
    """None if valid, else an error message."""
    if priority not in VALID_PRIORITIES:
        return f"priority must be one of {VALID_PRIORITIES}, got '{priority}'."
    return None


def _generate_link_code(length: int = 6) -> str:
    return "".join(random.choices(_LINK_CODE_ALPHABET, k=length))


def _now() -> datetime:
    """Naive datetime, but always UTC — see models.py."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to naive UTC.

    A string carrying an explicit offset is converted from it. A naive string
    is interpreted in the configured local timezone, NOT as UTC — otherwise
    "Friday 5pm" typed by someone in UTC+8 would silently land at 1am Saturday
    their time.
    """
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _iso_utc(dt: datetime | None) -> str | None:
    return (dt.isoformat() + "Z") if dt else None


def _iso_local(dt: datetime | None) -> str | None:
    """Render a stored naive-UTC datetime in the configured local timezone."""
    if not dt:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ).isoformat()


def _hours_between(later: datetime | None, earlier: datetime | None) -> float | None:
    if not later or not earlier:
        return None
    return round((later - earlier).total_seconds() / 3600.0, 2)


def _find_person_by_name(session, name: str) -> Person | None:
    """Look up a person by name, case-insensitively.

    Case-insensitive because the agent types these names from context and will
    eventually send "alice" where the record says "Alice".
    """
    stmt = select(Person).where(func.lower(Person.name) == (name or "").strip().lower())
    return session.execute(stmt).scalar_one_or_none()


def _person_dict(session, person: Person) -> dict:
    open_count = session.execute(
        select(func.count(Task.id)).where(
            Task.owner_id == person.id, Task.status.in_(OPEN_STATUSES)
        )
    ).scalar_one()
    return {
        "person_id": person.id,
        "name": person.name,
        "role": person.role,
        "telegram_username": person.telegram_username,
        "is_linked": bool(person.telegram_chat_id),
        # Only useful while still unlinked — it's how they complete linking.
        "link_code": person.link_code if not person.telegram_chat_id else None,
        "link_url": (
            telegram_client.build_link_url(person.link_code)
            if not person.telegram_chat_id
            else None
        ),
        "open_task_count": open_count,
    }


def _task_dict(session, task: Task, now: datetime | None = None) -> dict:
    now = now or _now()

    check_ins = session.execute(
        select(CheckIn).where(CheckIn.task_id == task.id).order_by(CheckIn.sent_at.desc())
    ).scalars().all()
    sent = [c for c in check_ins if c.sent_at is not None]
    last = sent[0] if sent else None

    # How many pings in a row have gone unanswered, most recent first. This is
    # what an "escalate after N ignored pings" rule actually keys off.
    unanswered_streak = 0
    for c in sent:
        if c.reply_text:
            break
        unanswered_streak += 1

    # The most recent thing this person actually said about the task, whether
    # it answered a ping or arrived unprompted. The digest needs this to quote
    # real blockers instead of reporting "some tasks are blocked".
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
        "deadline_utc": _iso_utc(task.deadline),
        "deadline_local": _iso_local(task.deadline),
        # Negative means overdue. Pre-computed so the agent never does date math.
        "hours_until_deadline": _hours_between(task.deadline, now),
        "priority": task.priority,
        "status": task.status,
        "progress_pct": task.progress_pct,
        "updated_at_local": _iso_local(task.updated_at),
        "last_checkin_sent_at_local": _iso_local(last.sent_at) if last else None,
        "hours_since_last_checkin": _hours_between(now, last.sent_at) if last else None,
        "last_checkin_answered": bool(last.reply_text) if last else None,
        "unanswered_checkin_count": unanswered_streak,
        "total_checkins": len(sent),
        "last_reply_text": last_reply.reply_text if last_reply else None,
        "last_reply_at_local": _iso_local(last_reply.reply_received_at) if last_reply else None,
    }


# ---------------------------------------------------------------------------
# 1. create_task
# ---------------------------------------------------------------------------
def create_task(
    title: str,
    owner_name: str,
    priority: str,
    deadline: str | None = None,
    description: str | None = None,
    manager_name: str | None = None,
) -> dict:
    """Create a new task assigned to a person who has already been registered.

    Args:
        title: Short task title.
        owner_name: Name of the person who owns this task (must already be
            registered via register_person). Case-insensitive.
        priority: "low", "medium", or "high" — required, not optional. Drives
            how often get_chase_plan will re-chase this task (high: every 1h,
            medium: every 6h, low: every 24h), so it must be a deliberate
            decision, not a default guessed on the caller's behalf.
        deadline: ISO 8601 datetime. If it has no timezone offset it is read as
            LOCAL time, e.g. "2026-08-10T17:00:00" means 5pm local.
        description: Optional longer description.
        manager_name: Which manager this task answers to. Only needed once
            more than one registered person has role="manager" — with a
            single manager in the system (today's setup) it is resolved
            automatically and this can be omitted.
    """
    priority_error = _validate_priority(priority)
    if priority_error:
        return {"error": priority_error}

    with session_scope() as session:
        owner = _find_person_by_name(session, owner_name)
        if owner is None:
            return {
                "error": f"No registered person named '{owner_name}'. "
                "Register them with register_person first."
            }

        if manager_name:
            manager = _find_person_by_name(session, manager_name)
            if manager is None:
                return {"error": f"No registered person named '{manager_name}'."}
        else:
            candidate_managers = session.execute(
                select(Person).where(Person.role == "manager")
            ).scalars().all()
            manager = candidate_managers[0] if len(candidate_managers) == 1 else None

        task = Task(
            title=title,
            description=description,
            owner_id=owner.id,
            manager_id=manager.id if manager else None,
            deadline=_parse_dt(deadline),
            priority=priority,
        )
        session.add(task)
        session.flush()
        result = _task_dict(session, task)
        if not owner.telegram_chat_id:
            result["warning"] = (
                f"'{owner.name}' has not linked Telegram yet and cannot be "
                f"messaged. Their link code is {owner.link_code}."
            )
        return result


# ---------------------------------------------------------------------------
# 2. list_tasks
# ---------------------------------------------------------------------------
def list_tasks(
    filter: str = "all",
    owner_name: str | None = None,
    due_soon_hours: int = 24,
) -> list[dict]:
    """List tasks with their full chase state.

    Each task includes everything needed to decide whether to chase it:
    hours_until_deadline (negative = overdue), hours_since_last_checkin,
    unanswered_checkin_count, and owner_is_linked.

    Args:
        filter: "all", "active" (not done/cancelled), "overdue" (past deadline
            and still active), or "due_soon" (deadline within due_soon_hours
            and still active).
        owner_name: If set, only tasks owned by this person (case-insensitive).
        due_soon_hours: Window size used by the "due_soon" filter.
    """
    with session_scope() as session:
        tasks = session.execute(select(Task)).scalars().all()
        now = _now()
        wanted_owner = (owner_name or "").strip().lower()

        results = []
        for task in tasks:
            if wanted_owner:
                if not task.owner or task.owner.name.strip().lower() != wanted_owner:
                    continue
            if filter in ("active", "overdue", "due_soon") and task.status in CLOSED_STATUSES:
                continue
            if filter == "overdue":
                if not task.deadline or task.deadline >= now:
                    continue
            elif filter == "due_soon":
                if not task.deadline:
                    continue
                if not (now <= task.deadline <= now + timedelta(hours=due_soon_hours)):
                    continue
            results.append(_task_dict(session, task, now))
        return results


# ---------------------------------------------------------------------------
# 3. update_task
# ---------------------------------------------------------------------------
def update_task(
    task_id: int,
    status: str | None = None,
    progress_pct: int | None = None,
    deadline: str | None = None,
    priority: str | None = None,
    title: str | None = None,
    owner_name: str | None = None,
) -> dict:
    """Update a task. Any argument left out is left unchanged.

    Args:
        task_id: The task to update.
        status: "not_started", "in_progress", "blocked", "done", or "cancelled".
            Setting "done" also forces progress_pct to 100.
        progress_pct: 0-100.
        deadline: New ISO 8601 deadline (naive = local time, as in create_task).
        priority: "low", "medium", or "high" — changes this task's chase
            re-ping floor (see create_task).
        title: New title.
        owner_name: Reassign to a different registered person.
    """
    if priority is not None:
        priority_error = _validate_priority(priority)
        if priority_error:
            return {"error": priority_error}

    with session_scope() as session:
        task = session.get(Task, task_id)
        if task is None:
            return {"error": f"No task with id {task_id}"}

        if owner_name is not None:
            new_owner = _find_person_by_name(session, owner_name)
            if new_owner is None:
                return {"error": f"No registered person named '{owner_name}'."}
            task.owner_id = new_owner.id

        if status is not None:
            task.status = status
        if progress_pct is not None:
            task.progress_pct = max(0, min(100, progress_pct))
        if deadline is not None:
            task.deadline = _parse_dt(deadline)
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
        return _task_dict(session, task)


# ---------------------------------------------------------------------------
# 4. reassign_task
# ---------------------------------------------------------------------------
def reassign_task(task_id: int, new_owner_name: str) -> dict:
    """Move a task to a different registered person.

    A narrower, explicit alternative to update_task(owner_name=...): it only
    ever touches ownership, so a reassignment can't happen silently as a side
    effect of an unrelated field edit in the same call. Use this whenever the
    intent is specifically "move this task to someone else."
    """
    with session_scope() as session:
        task = session.get(Task, task_id)
        if task is None:
            return {"error": f"No task with id {task_id}"}
        new_owner = _find_person_by_name(session, new_owner_name)
        if new_owner is None:
            return {"error": f"No registered person named '{new_owner_name}'."}
        # Looked up independently of the task.owner relationship (not via
        # task.owner.name) — touching that relationship here would cache the
        # old Person on the ORM identity map, and setting owner_id directly
        # afterward doesn't invalidate that cache, leaving _task_dict's own
        # task.owner.name read stale.
        old_owner = session.get(Person, task.owner_id)
        old_owner_name = old_owner.name if old_owner else None
        task.owner_id = new_owner.id
        session.flush()
        result = _task_dict(session, task)
        result["reassigned_from"] = old_owner_name
        return result


# ---------------------------------------------------------------------------
# 5. delete_task
# ---------------------------------------------------------------------------
def delete_task(task_id: int) -> dict:
    """Permanently delete a task and its check-in history.

    Prefer update_task(status="cancelled") for work that was dropped — that
    keeps the record. Use this only for tasks created by mistake.
    """
    with session_scope() as session:
        task = session.get(Task, task_id)
        if task is None:
            return {"error": f"No task with id {task_id}"}
        title = task.title
        session.execute(
            UnmatchedMessage.__table__.delete().where(
                UnmatchedMessage.candidate_task_ids.like(f"%{task_id}%")
            )
        )
        session.execute(CheckIn.__table__.delete().where(CheckIn.task_id == task_id))
        session.delete(task)
        return {"deleted": True, "task_id": task_id, "title": title}


# ---------------------------------------------------------------------------
# 6. register_person
# ---------------------------------------------------------------------------
def register_person(
    name: str,
    telegram_username: str | None = None,
    role: str = "team_member",
) -> dict:
    """Register a person (team member or manager) and get their linking code.

    The person must send `/start <link_code>` to the bot from their own
    Telegram account to complete linking — only then can they be messaged.

    Args:
        name: The person's name.
        telegram_username: Their @username, for reference only (not required
            for linking to work).
        role: "team_member" or "manager".
    """
    with session_scope() as session:
        existing = _find_person_by_name(session, name)
        if existing is not None:
            return {
                "error": f"'{name}' is already registered (person_id={existing.id})."
            }

        link_code = _generate_link_code()
        person = Person(
            name=name.strip(),
            telegram_username=telegram_username,
            role=role,
            link_code=link_code,
        )
        session.add(person)
        session.flush()
        link_url = telegram_client.build_link_url(link_code)
        return {
            "person_id": person.id,
            "name": person.name,
            "role": person.role,
            "link_code": link_code,
            "link_url": link_url,
            "instructions": (
                f"Send {person.name} this link and ask them to tap it: {link_url}"
                if link_url
                else f"Ask {person.name} to send /start {link_code} to the bot."
            ),
        }


# ---------------------------------------------------------------------------
# 7. list_people
# ---------------------------------------------------------------------------
def list_people(role: str | None = None) -> list[dict]:
    """List registered people, their role, whether they've linked Telegram,
    and how many open tasks they own.

    Use this to find who the manager is (role="manager") before sending a
    digest, and to spot people who never completed Telegram linking.

    Args:
        role: Optionally filter to "manager" or "team_member".
    """
    with session_scope() as session:
        stmt = select(Person).order_by(Person.name)
        if role:
            stmt = stmt.where(Person.role == role)
        return [_person_dict(session, p) for p in session.execute(stmt).scalars().all()]


# ---------------------------------------------------------------------------
# 8. telegram_send_message
# ---------------------------------------------------------------------------
def telegram_send_message(owner_name: str, text: str, task_id: int | None = None) -> dict:
    """Send a Telegram message to a registered, linked person.

    When chasing a specific task, ALWAYS pass task_id. Doing so records the
    check-in in the same call — which is what stops the same person being
    pinged again on the next run, and what lets their reply be matched back to
    this exact task.

    Args:
        owner_name: Name of the registered person to message (case-insensitive).
        text: Message body.
        task_id: The task being chased, if this is a chase message.
    """
    with session_scope() as session:
        person = _find_person_by_name(session, owner_name)
        if person is None:
            return {"error": f"No registered person named '{owner_name}'."}
        if not person.telegram_chat_id:
            return {
                "error": f"'{person.name}' hasn't linked their Telegram yet, so "
                f"they cannot be messaged. Their link code is {person.link_code}. "
                "This is not an ignored message — do not count it as one.",
                "needs_linking": True,
            }
        chat_id = person.telegram_chat_id

        if task_id is not None:
            task = session.get(Task, task_id)
            if task is None:
                return {"error": f"No task with id {task_id}"}
            if task.owner_id != person.id:
                return {
                    "error": f"Task {task_id} belongs to "
                    f"{task.owner.name if task.owner else 'someone else'}, not {person.name}."
                }

    try:
        result = telegram_client.send_message(chat_id, text)
    except TelegramNotConfigured as exc:
        return {"error": str(exc)}

    message_id = (result.get("result") or {}).get("message_id")

    checkin_id = None
    if task_id is not None:
        with session_scope() as session:
            checkin = CheckIn(
                task_id=task_id,
                sent_at=_now(),
                message_sent=text,
                telegram_message_id=message_id,
            )
            session.add(checkin)
            session.flush()
            checkin_id = checkin.id

    return {
        "sent": True,
        "to": owner_name,
        "task_id": task_id,
        "checkin_id": checkin_id,
        "telegram_message_id": message_id,
    }


# ---------------------------------------------------------------------------
# 9. telegram_get_updates
# ---------------------------------------------------------------------------
def _store_unmatched(session, person, chat_id, text, reason, candidates=None) -> dict:
    row = UnmatchedMessage(
        person_id=person.id if person else None,
        chat_id=chat_id,
        text=text,
        received_at=_now(),
        reason=reason,
        candidate_task_ids=json.dumps(candidates) if candidates else None,
    )
    session.add(row)
    session.flush()
    return {
        "unmatched_id": row.id,
        "person_name": person.name if person else None,
        "text": text,
        "reason": reason,
        "candidate_task_ids": candidates or [],
    }


def telegram_get_updates() -> dict:
    """Check Telegram for new messages since the last check.

    Returns three lists:
      * linked   — people who just completed Telegram linking.
      * replies  — messages confidently matched to a specific task. Interpret
                   each one and call update_task accordingly.
      * unmatched — messages that could not be matched confidently: a
                   spontaneous update with no ping outstanding, a reply to an
                   already-answered ping, or an ambiguous reply sent while
                   several pings were outstanding. Each has an unmatched_id;
                   work out which task it means (list_tasks helps) and call
                   resolve_unmatched. These persist across runs until resolved,
                   so nothing is lost — but they are never resolved
                   automatically, so do not ignore them.

    Messages from people who aren't registered are discarded.
    """
    with session_scope() as session:
        state = session.get(BotState, 1)
        if state is None:
            state = BotState(id=1, last_update_id=None)
            session.add(state)
            session.flush()

        offset = state.last_update_id + 1 if state.last_update_id is not None else None
        try:
            updates = telegram_client.get_updates(offset=offset)
        except TelegramNotConfigured as exc:
            return {"error": str(exc)}

        linked: list[dict] = []
        replies: list[dict] = []
        new_unmatched: list[dict] = []
        max_update_id = state.last_update_id

        for update in updates:
            update_id = update.get("update_id")
            if max_update_id is None or (update_id is not None and update_id > max_update_id):
                max_update_id = update_id

            message = update.get("message") or {}
            chat = message.get("chat") or {}
            chat_id = str(chat.get("id")) if chat.get("id") is not None else None
            text = (message.get("text") or "").strip()
            replied_to = (message.get("reply_to_message") or {}).get("message_id")

            if chat_id is None or not text:
                continue

            if text.startswith("/start"):
                code = text[len("/start"):].strip()
                if not code:
                    # Telegram's own auto-sent first-contact message. Not a
                    # linking attempt and not an update — discard it.
                    continue
                person = session.execute(
                    select(Person).where(Person.link_code == code)
                ).scalar_one_or_none()
                if person is not None:
                    person.telegram_chat_id = chat_id
                    person.link_code = None
                    linked.append({"person_id": person.id, "name": person.name})
                continue

            person = session.execute(
                select(Person).where(Person.telegram_chat_id == chat_id)
            ).scalar_one_or_none()
            if person is None:
                # A stranger who found the bot. Not our business.
                continue

            # Best case: they used Telegram's reply feature, so we know exactly
            # which ping this answers regardless of how many are outstanding.
            if replied_to is not None:
                target = session.execute(
                    select(CheckIn)
                    .join(Task)
                    .where(
                        CheckIn.telegram_message_id == replied_to,
                        Task.owner_id == person.id,
                    )
                ).scalars().first()
                if target is not None:
                    if target.reply_text:
                        new_unmatched.append(
                            _store_unmatched(
                                session, person, chat_id, text,
                                "follow-up to an already-answered check-in",
                                [target.task_id],
                            )
                        )
                    else:
                        target.reply_text = text
                        target.reply_received_at = _now()
                        replies.append({
                            "task_id": target.task_id,
                            "task_title": target.task.title,
                            "owner_name": person.name,
                            "reply_text": text,
                        })
                    continue

            open_checkins = session.execute(
                select(CheckIn)
                .join(Task)
                .where(
                    Task.owner_id == person.id,
                    CheckIn.reply_text.is_(None),
                    CheckIn.sent_at.is_not(None),
                )
                .order_by(CheckIn.sent_at.desc())
            ).scalars().all()

            if len(open_checkins) == 1:
                target = open_checkins[0]
                target.reply_text = text
                target.reply_received_at = _now()
                replies.append({
                    "task_id": target.task_id,
                    "task_title": target.task.title,
                    "owner_name": person.name,
                    "reply_text": text,
                })
            elif len(open_checkins) > 1:
                # Guessing here would silently attach the reply to the wrong
                # task, so hand the ambiguity to the agent instead.
                new_unmatched.append(
                    _store_unmatched(
                        session, person, chat_id, text,
                        f"{len(open_checkins)} pings were outstanding — cannot tell which this answers",
                        [c.task_id for c in open_checkins],
                    )
                )
            else:
                new_unmatched.append(
                    _store_unmatched(
                        session, person, chat_id, text,
                        "spontaneous update — no ping was outstanding",
                    )
                )

        state.last_update_id = max_update_id

        pending = session.execute(
            select(UnmatchedMessage).where(UnmatchedMessage.handled.is_(False))
        ).scalars().all()
        unmatched = [
            {
                "unmatched_id": row.id,
                "person_name": row.person.name if row.person else None,
                "text": row.text,
                "reason": row.reason,
                "candidate_task_ids": json.loads(row.candidate_task_ids or "[]"),
                "received_at_local": _iso_local(row.received_at),
            }
            for row in pending
        ]

        return {"linked": linked, "replies": replies, "unmatched": unmatched}


# ---------------------------------------------------------------------------
# 10. resolve_unmatched
# ---------------------------------------------------------------------------
def resolve_unmatched(unmatched_id: int, task_id: int | None = None) -> dict:
    """Resolve a message that couldn't be matched to a task automatically.

    Args:
        unmatched_id: From telegram_get_updates.
        task_id: The task this message was actually about — its text is
            recorded against that task. Leave empty to dismiss the message as
            not being a status update at all (chit-chat, a question, noise).

    After attaching a message to a task, call update_task separately if the
    status or progress changed.
    """
    with session_scope() as session:
        row = session.get(UnmatchedMessage, unmatched_id)
        if row is None:
            return {"error": f"No unmatched message with id {unmatched_id}"}
        if row.handled:
            return {"error": f"Unmatched message {unmatched_id} was already resolved."}

        if task_id is None:
            row.handled = True
            return {"dismissed": True, "unmatched_id": unmatched_id}

        task = session.get(Task, task_id)
        if task is None:
            return {"error": f"No task with id {task_id}"}

        # Recorded with no sent_at/message_sent: this is an update that arrived
        # without us having asked for it, which is worth being able to tell
        # apart from a genuine ping-and-reply pair later.
        checkin = CheckIn(
            task_id=task_id,
            reply_text=row.text,
            reply_received_at=row.received_at,
        )
        session.add(checkin)
        row.handled = True
        session.flush()
        return {
            "attached": True,
            "unmatched_id": unmatched_id,
            "task_id": task_id,
            "task_title": task.title,
            "checkin_id": checkin.id,
        }


# ---------------------------------------------------------------------------
# 11. get_chase_plan
# ---------------------------------------------------------------------------
_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


def get_chase_plan(
    due_soon_hours: int = 24,
    max_unanswered: int = 3,
) -> dict:
    """Do a whole chase check's worth of gathering and filtering in one call.

    Polls Telegram, files anything that arrived, then works out who should be
    chased right now — applying the re-ping floor, the escalation threshold,
    the unreachable check, and one-task-per-person — and returns the result
    already sorted by urgency.

    Everything filtered out is returned in `skipped` with the reason, so a
    judgement call the rules got wrong can still be overridden deliberately.

    Returns:
        replies_to_interpret: replies matched to a task, awaiting your reading.
            Decide what each means and call update_task.
        unmatched_to_resolve: messages that could not be matched. Work out
            which task each means and call resolve_unmatched.
        newly_linked: people who just connected their Telegram.
        to_chase: chase these now — one per person, most urgent first. Write a
            message for each and send it with telegram_send_message(task_id=...).
        to_escalate: too many unanswered pings, grouped one entry per owner
            (each carrying every overdue task of theirs) — send exactly one
            message per entry, covering all of that person's tasks, never one
            message per task.
        unreachable: people who own work but never linked Telegram, with their
            link codes. Not their fault and never an ignored ping.
        skipped: filtered out this run, each with a reason.
        manager_name: who to send escalations to.

    Args:
        due_soon_hours: How far ahead counts as "due soon".
        max_unanswered: Escalate instead of chasing at this many unanswered pings.

    The re-ping floor is no longer a flat window — it now depends on each
    task's priority (high: 1h, medium: 6h, low: 24h), so an urgent task gets
    followed up on far sooner than a low-priority one. See chase_now for a
    manual, floor-bypassing chase of one named person on demand.
    """
    updates = telegram_get_updates()
    telegram_error = updates.get("error")

    with session_scope() as session:
        now = _now()

        manager = session.execute(
            select(Person).where(Person.role == "manager").order_by(Person.id)
        ).scalars().first()

        tasks = session.execute(select(Task)).scalars().all()

        candidates: list[dict] = []
        skipped: list[dict] = []
        to_escalate: list[dict] = []
        unreachable: dict[str, dict] = {}

        for task in tasks:
            if task.status in CLOSED_STATUSES:
                continue
            if not task.deadline:
                continue

            hours_left = _hours_between(task.deadline, now)
            is_overdue = hours_left is not None and hours_left < 0
            is_due_soon = hours_left is not None and 0 <= hours_left <= due_soon_hours
            if not (is_overdue or is_due_soon):
                continue

            info = _task_dict(session, task, now)

            if not info["owner_is_linked"]:
                owner = task.owner
                if owner is not None:
                    entry = unreachable.setdefault(
                        owner.name,
                        {
                            "name": owner.name,
                            "link_code": owner.link_code,
                            "link_url": telegram_client.build_link_url(owner.link_code),
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
            # they have usually already said so. Chasing them again is noise —
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
                    "hours_overdue": round(-hours_left, 2) if is_overdue else 0,
                    "deadline_local": info["deadline_local"],
                })
                continue

            since = info["hours_since_last_checkin"]
            floor = _PRIORITY_CHASE_FLOOR_HOURS.get(task.priority, 6)
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
                "hours_overdue": round(-hours_left, 2) if is_overdue else 0,
                "hours_until_deadline": hours_left,
                "priority": task.priority,
                "status": task.status,
                "progress_pct": task.progress_pct,
                "unanswered_checkin_count": info["unanswered_checkin_count"],
                "previously_chased": info["total_checkins"] > 0,
            })

        # Most overdue first, then by priority.
        candidates.sort(key=lambda c: (-c["hours_overdue"], _PRIORITY_RANK.get(c["priority"], 1)))

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

        # Grouped by (owner, manager) — not left as a flat per-task list —
        # mirrors how `to_chase` is already deduplicated to one entry per
        # person. Without this, a model composing the escalation message has
        # to notice on its own that several tasks belong to the same person
        # and combine them; tested for real and found wanting (a faster,
        # smaller model sent three separate messages instead of one).
        # Grouping here makes the mistake structurally impossible rather than
        # just discouraged. Keying on manager too (not just owner) means an
        # owner with escalating tasks under two different managers correctly
        # produces two entries — one per manager to notify — instead of
        # silently merging into a single entry with only one manager visible.
        _escalations_by_owner: dict[tuple[str, str | None], list[dict]] = {}
        for entry in to_escalate:
            key = (entry["owner_name"], entry["manager_name"])
            _escalations_by_owner.setdefault(key, []).append({
                "task_id": entry["task_id"],
                "title": entry["title"],
                "unanswered_checkin_count": entry["unanswered_checkin_count"],
                "hours_overdue": entry["hours_overdue"],
                "deadline_local": entry["deadline_local"],
            })
        to_escalate = [
            {"owner_name": owner, "manager_name": manager_name, "tasks": owner_tasks}
            for (owner, manager_name), owner_tasks in _escalations_by_owner.items()
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
            "checked_at_local": _iso_local(now),
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
            _escalated_tasks = sum(len(e["tasks"]) for e in to_escalate)
            bits.append(f"{_escalated_tasks} task(s) to escalate across {len(to_escalate)} owner(s)")
        if plan["unreachable"]:
            bits.append(f"{len(plan['unreachable'])} unreachable owner(s)")
        plan["summary"] = "; ".join(bits) if bits else "nothing to do this run"
        return plan


# ---------------------------------------------------------------------------
# 11b. chase_now
# ---------------------------------------------------------------------------
def chase_now(owner_name: str) -> dict:
    """Force an immediate chase of every open task for one named person right
    now — a full manual override (e.g. the manager typing "chase Henry
    now"), distinct from get_chase_plan's scheduled sweep.

    Unlike get_chase_plan, this bypasses BOTH the re-ping floor AND the
    overdue/due-soon eligibility window — that window exists so the
    *automated* sweep doesn't nag someone about a task due next month, but an
    explicit manual request already means the manager has decided now is the
    right time, deadline-window logic or not. A task with no deadline at all
    is included too, for the same reason. get_chase_plan (the scheduled
    sweep) is unaffected by any of this — it still respects the floor and
    the window exactly as before.

    Still respects: a blocked task is excluded (it needs the manager, not
    another ping — same reasoning as get_chase_plan), a closed (done/
    cancelled) task has nothing to chase, and an owner who hasn't linked
    Telegram can't be reached regardless of what's requested. Unlike
    get_chase_plan, this returns every open task for this one person rather
    than just their single most urgent one — write ONE message covering all
    of them, the same way an escalation entry covers every task for one
    person in a single message.

    Like get_chase_plan, this polls Telegram first, so it may return
    replies_to_interpret / unmatched_to_resolve / newly_linked from anyone,
    not just this owner. Handle those the same way get_chase_plan's caller
    does (interpret and call update_task, or resolve_unmatched) before
    sending the new chase message — they are not picked up again later.

    Args:
        owner_name: The person to chase. Must already be registered — never
            guess or infer this from earlier conversation context; ask if
            it wasn't given explicitly in the request.
    """
    updates = telegram_get_updates()
    telegram_error = updates.get("error")

    with session_scope() as session:
        owner = _find_person_by_name(session, owner_name)
        if owner is None:
            return {"error": f"No registered person named '{owner_name}'."}

        if not owner.telegram_chat_id:
            return {
                "owner_name": owner.name,
                "reachable": False,
                "tasks": [],
                "link_code": owner.link_code,
                "link_url": telegram_client.build_link_url(owner.link_code),
                "message": f"'{owner.name}' has not linked Telegram and cannot be messaged.",
            }

        now = _now()
        tasks = session.execute(select(Task).where(Task.owner_id == owner.id)).scalars().all()

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

            hours_left = _hours_between(task.deadline, now)
            is_overdue = hours_left is not None and hours_left < 0

            info = _task_dict(session, task, now)
            matched.append({
                "task_id": task.id,
                "title": task.title,
                "manager_name": info["manager_name"],
                "deadline_local": info["deadline_local"],
                "hours_overdue": round(-hours_left, 2) if is_overdue else 0,
                "hours_until_deadline": hours_left,
                "priority": task.priority,
                "status": task.status,
                "progress_pct": task.progress_pct,
                "unanswered_checkin_count": info["unanswered_checkin_count"],
                "previously_chased": info["total_checkins"] > 0,
            })

        matched.sort(key=lambda c: (-c["hours_overdue"], _PRIORITY_RANK.get(c["priority"], 1)))

        result = {
            "owner_name": owner.name,
            "reachable": True,
            "tasks": matched,
            "skipped": skipped,
            # This call's own telegram_get_updates() above may have picked up
            # replies or unmatched messages (from anyone, not just this
            # owner) — surface them rather than silently discarding them, or
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


# ---------------------------------------------------------------------------
# 12. get_digest_data
# ---------------------------------------------------------------------------
def get_digest_data(at_risk_hours: int = 24) -> dict:
    """Gather everything the manager's digest needs, in one call.

    Deliberately does NOT decide what is "at risk" or what deserves the
    manager's attention — that judgement is yours. It only guarantees the
    gathering happens and that the manager is looked up rather than guessed.

    Returns the manager to send to, every active task with its computed state,
    plus counts and the people worth calling out: those who have stopped
    replying, and those who never linked Telegram and cannot be reached.

    `blocked_needing_decision` matters most. Blocked tasks are deliberately no
    longer chased — the owner cannot fix them and has usually already
    explained why — so the digest is the only place they surface. Each carries
    the owner's own words and whether the deadline has already passed, because
    the manager's likely next move is to unblock it or move the date.

    Args:
        at_risk_hours: Tasks due within this many hours are flagged
            `due_within_window` for your consideration.
    """
    with session_scope() as session:
        now = _now()

        managers = [
            _person_dict(session, p)
            for p in session.execute(
                select(Person).where(Person.role == "manager").order_by(Person.id)
            ).scalars().all()
        ]

        tasks = session.execute(select(Task)).scalars().all()

        active: list[dict] = []
        overdue = 0
        unresponsive: list[dict] = []
        blocked: list[dict] = []
        for task in tasks:
            if task.status in CLOSED_STATUSES:
                continue
            info = _task_dict(session, task, now)

            if task.status == "blocked":
                # These are deliberately not chased any more (see
                # get_chase_plan), so the digest is the only place they
                # surface. Carry the owner's own words and whether the
                # deadline has already gone, since the manager's likely
                # action is to unblock it or move the date.
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
                _PRIORITY_RANK.get(t["priority"], 1),
            )
        )

        unreachable = [
            _person_dict(session, p)
            for p in session.execute(select(Person)).scalars().all()
            if not p.telegram_chat_id
        ]
        unreachable = [p for p in unreachable if p["open_task_count"] > 0]

        pending_unmatched = session.execute(
            select(func.count(UnmatchedMessage.id)).where(UnmatchedMessage.handled.is_(False))
        ).scalar_one()

        return {
            "manager_name": managers[0]["name"] if managers else None,
            "managers": managers,
            "generated_at_local": _iso_local(now),
            "active_tasks": active,
            "active_count": len(active),
            "overdue_count": overdue,
            "blocked_needing_decision": blocked,
            "unresponsive": unresponsive,
            "unreachable_people": unreachable,
            "pending_unmatched_count": pending_unmatched,
        }


# ---------------------------------------------------------------------------
# 13. delete_person
# ---------------------------------------------------------------------------
def delete_person(name: str) -> dict:
    """Permanently remove a person who was registered by mistake.

    Refuses if they own any task at all, open or closed — reassign those
    tasks with update_task(owner_name=...) or remove them with delete_task
    first. Keeps this limited to genuine "wrong person, nothing built on
    them yet" cleanup, never a way to silently lose task/check-in history.
    Callers (e.g. the manager-bot skill) are expected to confirm with a
    human before calling this — it is not enforced here, since this layer
    has no concept of "the human already agreed," only of what's safe to
    allow if asked.
    """
    with session_scope() as session:
        person = _find_person_by_name(session, name)
        if person is None:
            return {"error": f"No registered person named '{name}'."}
        task_count = session.execute(
            select(func.count(Task.id)).where(Task.owner_id == person.id)
        ).scalar_one()
        if task_count > 0:
            return {
                "error": f"'{person.name}' owns {task_count} task(s) — reassign them "
                "with update_task(owner_name=...) or remove them with delete_task "
                "before removing this person."
            }
        person_id = person.id
        person_name = person.name
        session.delete(person)
        return {"deleted": True, "person_id": person_id, "name": person_name}


# ---------------------------------------------------------------------------
# 14. get_assistant_name / set_assistant_name
# ---------------------------------------------------------------------------
_ASSISTANT_NAMES = {"toby": "Toby", "abby": "Abby"}


def _get_bot_state(session) -> BotState:
    state = session.get(BotState, 1)
    if state is None:
        state = BotState(id=1, last_update_id=None)
        session.add(state)
        session.flush()
    return state


def get_assistant_name() -> dict:
    """Check whether the manager has already chosen this bot's persona name.

    Call this at the start of a new conversation to decide whether to
    introduce yourself and ask, or just proceed using the name already on
    file. The choice persists across restarts — no need to ask again once
    `chosen` is true, unless the manager explicitly asks to change it.
    """
    with session_scope() as session:
        state = _get_bot_state(session)
        return {"name": state.assistant_name, "chosen": state.assistant_name is not None}


def set_assistant_name(name: str) -> dict:
    """Set the bot's persona name, chosen by the manager.

    Args:
        name: "Toby" (male) or "Abby" (female) — case-insensitive, no other
            values accepted. Ask the manager to pick one of these two rather
            than inventing or accepting a different name.

    Also updates the bot's actual Telegram display name (what shows up in
    the Telegram app itself) via the Bot API, best-effort — if that part
    fails (e.g. no token configured), the persona choice is still saved and
    the response says so, rather than losing the whole change over the
    cosmetic half.
    """
    normalized = _ASSISTANT_NAMES.get((name or "").strip().lower())
    if normalized is None:
        return {"error": "Name must be 'Toby' or 'Abby' — ask the manager to pick one of those two."}

    with session_scope() as session:
        state = _get_bot_state(session)
        state.assistant_name = normalized

    result = {"name": normalized, "saved": True}
    token = os.environ.get("TASK_MANAGER_BOT_TOKEN")
    if not token:
        result["telegram_display_name_updated"] = False
        result["telegram_note"] = "TASK_MANAGER_BOT_TOKEN not configured — persona name saved, but the Telegram app display name was not changed."
        return result
    try:
        telegram_client.set_bot_display_name(token, normalized)
        result["telegram_display_name_updated"] = True
    except Exception as exc:
        result["telegram_display_name_updated"] = False
        result["telegram_note"] = f"Persona name saved, but updating the Telegram display name failed: {exc}"
    return result
