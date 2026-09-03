"""The tools pm-chaser-mcp exposes to the agent runtime.

This service does data storage, Telegram I/O, and the mechanical filtering
that decides WHICH tasks are candidates for chasing. It never writes a
message and never interprets one - composing every outbound message and
reading every reply stays with the agent.

That split was not the original design. The first version left the filtering
to the agent as well, which meant five to eight tool calls of date arithmetic
and list-winnowing before it reached the one call that actually did anything.
The local 27B model reliably got lost in that bookkeeping: across twelve
rounds it announced "sending the chase message now" six times and never once
emitted the send. Rules that must always hold ("never re-ping within four
hours") also can't live in prose the model may skip. So the mechanical part
moved into get_chase_plan() below, which does it in one call and guarantees
the rules - while still returning everything it filtered out, with reasons,
so the agent can knowingly override.

Two conventions worth knowing when reading this file:

* Datetimes are stored naive-UTC (see pmchaser/db/base.py). Input is
  converted from local time on the way in, and output carries both a UTC
  and a local rendering plus pre-computed hour deltas - the agent should
  never have to do date arithmetic itself, because LLMs are unreliable at
  it.
* Anything the Skill needs to make a decision about (has this been pinged
  recently? how many pings went unanswered? has this person linked Telegram?)
  is returned as an explicit field rather than left to be inferred.

Every function below is a thin adapter over pmchaser/services/ - real
implementations live there. This module exists to be exactly what
main.py registers with the MCP server, so its function names, signatures,
and docstrings ARE the tool schema/prompt content an agent sees (see the
refactor plan's "hardest constraint" section) - kept byte-identical to
the pre-refactor tools.py by tests/test_tool_contract.py and
tests/test_golden_master.py. If you're tempted to "clean up" a docstring
here, don't - that's a live prompt change, not a comment edit.
"""

from __future__ import annotations

from pmchaser.integrations.telegram import TelegramNotConfigured
from pmchaser.logging import log_tool_call
from pmchaser.services.chase import chase_now as _chase_now
from pmchaser.services.chase import get_chase_plan as _get_chase_plan
from pmchaser.services.digest import get_digest_data as _get_digest_data
from pmchaser.services.messaging import peek_for_new_replies as _peek_for_new_replies
from pmchaser.services.messaging import resolve_unmatched as _resolve_unmatched
from pmchaser.services.messaging import telegram_get_updates as _telegram_get_updates
from pmchaser.services.messaging import telegram_send_message as _telegram_send_message
from pmchaser.services.notifications import notify_manager as _notify_manager
from pmchaser.services.people import delete_person as _delete_person
from pmchaser.services.people import list_people as _list_people
from pmchaser.services.people import register_person as _register_person
from pmchaser.services.reply_outcomes import record_reply_outcome as _record_reply_outcome
from pmchaser.services.tasks import create_task as _create_task
from pmchaser.services.tasks import create_tasks_bulk as _create_tasks_bulk
from pmchaser.services.tasks import delete_task as _delete_task
from pmchaser.services.tasks import list_tasks as _list_tasks
from pmchaser.services.tasks import reassign_task as _reassign_task
from pmchaser.services.tasks import update_task as _update_task

__all__ = [
    "get_chase_plan", "chase_now", "get_digest_data",
    "create_task", "create_tasks_bulk", "list_tasks", "update_task",
    "reassign_task", "delete_task", "register_person", "delete_person",
    "list_people", "telegram_send_message", "telegram_get_updates",
    "resolve_unmatched", "notify_manager", "record_reply_outcome",
    "peek_for_new_replies", "TelegramNotConfigured",
]


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

    For creating several tasks at once (a spreadsheet upload, meeting-minutes
    action items), use create_tasks_bulk instead of calling this in a loop —
    a loop of individual calls is exactly the pattern that has previously
    caused the same row to get created more than once.

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
    return log_tool_call(
        "create_task", _create_task,
        title=title,
        owner_name=owner_name,
        priority=priority,
        deadline=deadline,
        description=description,
        manager_name=manager_name,
    )


def create_tasks_bulk(tasks: list[dict]) -> dict:
    """Create several tasks in one call — the batch path for a spreadsheet
    upload, meeting-minutes action items, or any other multi-row source.

    Use this instead of calling create_task once per row. Looping create_task
    yourself means tracking "which rows have I already created" purely in
    your own reasoning across several separate turns — that bookkeeping has
    concretely failed before (the same row created 4 times in one batch,
    noticed only partway through). This call does the iteration in code,
    once, reliably, and tells you exactly what happened to every row.

    Args:
        tasks: One dict per task, each with the same fields as create_task's
            arguments — title, owner_name, priority (required), and
            optionally deadline, description, manager_name. Build this list
            from the rows you already showed the manager in your preview and
            got a yes on — do not add, drop, or re-order rows here.

    Returns:
        created: one entry per row that was created successfully (or, if it
            exactly matched an already-created row from the last few
            minutes, the existing task instead of a fresh duplicate —
            see `duplicate_of_existing` on that entry).
        failed: one entry per row that could not be created, each with the
            original row (`input`) and an `error` explaining why (bad
            priority, unregistered owner, etc.) — fix and retry only these,
            never the whole batch.
        summary: counts, for a one-line report back to the manager.
    """
    return log_tool_call("create_tasks_bulk", _create_tasks_bulk, tasks)


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
    return log_tool_call(
        "list_tasks", _list_tasks,
        filter=filter, owner_name=owner_name, due_soon_hours=due_soon_hours,
    )


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
    return log_tool_call(
        "update_task", _update_task,
        task_id=task_id,
        status=status,
        progress_pct=progress_pct,
        deadline=deadline,
        priority=priority,
        title=title,
        owner_name=owner_name,
    )


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
    return log_tool_call("reassign_task", _reassign_task, task_id, new_owner_name)


# ---------------------------------------------------------------------------
# 5. delete_task
# ---------------------------------------------------------------------------
def delete_task(task_id: int) -> dict:
    """Permanently delete a task and its check-in history.

    Prefer update_task(status="cancelled") for work that was dropped — that
    keeps the record. Use this only for tasks created by mistake.
    """
    return log_tool_call("delete_task", _delete_task, task_id)


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
    return log_tool_call(
        "register_person", _register_person,
        name, telegram_username=telegram_username, role=role,
    )


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
    return log_tool_call("list_people", _list_people, role=role)


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
    return log_tool_call(
        "telegram_send_message", _telegram_send_message,
        owner_name, text, task_id=task_id,
    )


# ---------------------------------------------------------------------------
# 9. telegram_get_updates
# ---------------------------------------------------------------------------
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
    return log_tool_call("telegram_get_updates", _telegram_get_updates)


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
    return log_tool_call(
        "resolve_unmatched", _resolve_unmatched,
        unmatched_id, task_id=task_id,
    )


# ---------------------------------------------------------------------------
# 11. get_chase_plan
# ---------------------------------------------------------------------------
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
        action_required: present only when to_chase or to_escalate is
            non-empty. States plainly that nothing has been sent or
            escalated yet and exactly which tool call each entry still
            needs. Never report a task as chased or escalated without
            that call actually returning first — a real ID from its
            result is what backs up a confirmation, not this field.

    Args:
        due_soon_hours: How far ahead counts as "due soon".
        max_unanswered: Escalate instead of chasing at this many unanswered pings.

    The re-ping floor is no longer a flat window — it now depends on each
    task's priority (high: 1h, medium: 6h, low: 24h), so an urgent task gets
    followed up on far sooner than a low-priority one. See chase_now for a
    manual, floor-bypassing chase of one named person on demand.
    """
    return log_tool_call(
        "get_chase_plan", _get_chase_plan,
        due_soon_hours=due_soon_hours, max_unanswered=max_unanswered,
    )


# ---------------------------------------------------------------------------
# 11b. chase_now
# ---------------------------------------------------------------------------
def chase_now(owner_name: str) -> dict:
    """Force an immediate chase of every open task for one named person right
    now — a full manual override (e.g. the manager typing "chase Daniel
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

    When `tasks` is non-empty, the response also carries an
    `action_required` field stating plainly that nothing has been sent
    yet and exactly which telegram_send_message call to make. Never
    report a message as sent without that call actually returning a real
    checkin_id/telegram_message_id first — describing what you're about
    to send is not the same as sending it.

    Args:
        owner_name: The person to chase. Must already be registered — never
            guess or infer this from earlier conversation context; ask if
            it wasn't given explicitly in the request.
    """
    return log_tool_call("chase_now", _chase_now, owner_name)


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
    return log_tool_call("get_digest_data", _get_digest_data, at_risk_hours=at_risk_hours)


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
    return log_tool_call("delete_person", _delete_person, name)


# ---------------------------------------------------------------------------
# 14. notify_manager
# ---------------------------------------------------------------------------
def notify_manager(text: str) -> dict:
    """Send the manager a short message from S.A.M.'s own bot, not the
    employee-facing chase bot.

    Use this after interpreting a reply that produced a real status/progress
    change on a task — not for pure chatter that left the task untouched.
    Also used for escalations (unanswered-checkin threshold) — both go out
    as S.A.M., since the manager only ever talks to S.A.M. directly and never
    to the employee-facing chase bot. This call always resolves the manager
    itself; callers never pass a chat id or bot token.

    Args:
        text: The message body. Say who replied, on which task, and what
            changed — not a raw status dump.
    """
    return log_tool_call("notify_manager", _notify_manager, text)


# ---------------------------------------------------------------------------
# 17. record_reply_outcome (Phase 3 composite tool - pmchaser-bot only,
# not registered for task-manager-bot - see pmchaser/mcp/profiles.py)
# ---------------------------------------------------------------------------
def record_reply_outcome(
    task_id: int,
    ack_text: str,
    manager_note: str,
    status: str | None = None,
    progress_pct: int | None = None,
) -> dict:
    """Update a task from an interpreted reply, acknowledge the owner, and
    notify the manager, in one call — collapsing the three separate calls
    (update_task, then telegram_send_message for the acknowledgment, then
    notify_manager) a real status update from a reply always requires
    into one.

    Only call this when the reply genuinely was a status update. If it
    was not, don't call this at all — leave the task alone, same as
    before this tool existed. Don't call update_task/telegram_send_message/
    notify_manager separately for the same reply instead of this — those
    three tools still exist and are unchanged, but for this specific
    situation (recording what a reply meant, acknowledging the owner, and
    telling the manager) this one call does all three reliably, in the
    right order, every time.

    Args:
        task_id: The task the reply was about.
        ack_text: A brief acknowledgment sent back to the task's owner —
            no more than one short line ("Got it, marked as done — nice
            work." / "Thanks — flagging that for Marcus to review, he'll
            follow up on the new deadline."). This is the only signal the
            owner ever gets that their reply was actually read. Never
            reference a task_id here — this is not a chase, and doing so
            would create a new open check-in nobody is waiting on.
        manager_note: A short line naming who replied, on which task, and
            what changed ("Daniel marked 'Submit vendor report' done." /
            "Priya's 'Clean up room' is now blocked — she said she's
            waiting on the key.") — not a raw status dump.
        status: "not_started", "in_progress", "blocked", "done", or
            "cancelled" — same values as update_task. If the reply asks
            for more time or a later deadline, never pass a status other
            than "blocked" here — the owner cannot grant themselves an
            extension; only the manager can, via a separate update_task
            call with a new deadline.
        progress_pct: 0-100, same as update_task.

    Returns:
        task: the updated task (update_task's own return shape) — or, if
            task_id was invalid, just this key with the error, and
            neither the acknowledgment nor the manager notification is
            attempted.
        ack: telegram_send_message's real return value for the
            acknowledgment — check for a real checkin_id/telegram_message_id
            before treating it as sent, same proof-of-send discipline as
            every other send in this system.
        manager_notification: notify_manager's real return value.
    """
    return log_tool_call(
        "record_reply_outcome", _record_reply_outcome,
        task_id, ack_text, manager_note, status=status, progress_pct=progress_pct,
    )


# ---------------------------------------------------------------------------
# Not an MCP tool - see main.py's /internal/peek route.
# ---------------------------------------------------------------------------
async def peek_for_new_replies(timeout: int = 25) -> bool:
    """Read-only long-poll: True if a new Telegram message has arrived for
    the employee bot, without consuming it.

    Deliberately NOT an MCP tool — this is infrastructure signaling for
    chase_listener.py (a plain host-side process, not an LLM) to wait on via
    the /internal/peek route in main.py, so it knows the instant a reply
    worth processing has arrived instead of only finding out on the next
    15-minute cron tick. Registering this as a tool would put its schema in
    every profile's prompt for something the model should never call itself.

    Never advances BotState.last_update_id — only telegram_get_updates()
    does that, when a real chase run consumes and processes the result. Two
    callers can safely peek at the same offset; only the one that actually
    processes gets to move it forward.

    Async, and must stay that way: this runs on the same event loop as
    every other request the server handles, called back-to-back forever by
    chase_listener.py. A blocking 25s long-poll here starves every other
    concurrent request (real MCP tool calls included) for the duration —
    a real incident, traced live to `list_tasks` taking 50s+ despite
    finishing in milliseconds once actually dispatched.
    """
    return await _peek_for_new_replies(timeout=timeout)
