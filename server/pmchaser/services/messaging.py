"""Telegram send/receive and reply-matching business logic - called by the
thin adapters in pmchaser/mcp/tools.py.

One intentional Phase-2-preserved design point (see the "hardest
constraint" section): the BotState.last_update_id cursor advance happens
inside telegram_get_updates only - peek_for_new_replies below never
touches it.

telegram_send_message's send-then-record split (refactor plan finding #2)
is addressed below, not eliminated: there is no way to make an external
HTTP call and a local DB write one atomic operation - no distributed
transaction spans Telegram's API and SQLite. See
_record_checkin_with_retry's docstring for what "fixed" actually means
here: the original failure mode was this failing *silently* (a real
`SKILL.md`-documented symptom - "chased again within the hour, forever"),
not that it could fail at all. What's fixed is the silence.
"""

from __future__ import annotations

import json
import time

from sqlalchemy import select

from pmchaser.db import base as db_base
from pmchaser.db.models import BotState, CheckIn, Person, Task, UnmatchedMessage
from pmchaser.domain.time_utils import iso_local
from pmchaser.integrations import telegram as telegram_integration
from pmchaser.integrations.telegram import TelegramNotConfigured
from pmchaser.repositories import checkins as checkins_repo
from pmchaser.repositories import people as people_repo
from pmchaser.repositories import unmatched as unmatched_repo


_CHECKIN_WRITE_MAX_ATTEMPTS = 3
_CHECKIN_WRITE_RETRY_BASE_DELAY_SECONDS = 0.05


def _record_checkin_with_retry(
    task_id: int, text: str, message_id: int | None,
) -> tuple[int | None, Exception | None]:
    """Phase 2 fix for the refactor plan's finding #2. There is no way to
    make the already-completed Telegram send and this DB write one atomic
    operation - no distributed transaction spans an external HTTP API and
    a local SQLite file, so "atomic" was never achievable here. What this
    closes is the *silent* half of the original failure: WAL +
    busy_timeout (pmchaser/db/base.py) already absorb the most likely
    transient cause (lock contention) at the driver level, so this retry
    is a shallow extra safety net on top of that - and if the write still
    fails after it, the caller gets `checkin_recorded: False` and a
    warning back, instead of a plain `sent: True` that quietly implies
    the check-in exists when it doesn't. The original, undetected symptom
    (see SKILL.md) was the person being "chased again within the hour,
    forever" with no record anyone could see explaining why - failing
    loudly here is what actually fixes that, since the retry alone
    cannot guarantee the write succeeds.

    Catches bare Exception deliberately: by this point the message has
    already been delivered, so raising here would crash the whole tool
    call over a state Telegram itself doesn't know or care about - a
    controlled, visible failure response is strictly better than an
    unhandled exception for something that already, unavoidably, happened.
    """
    last_error: Exception | None = None
    for attempt in range(_CHECKIN_WRITE_MAX_ATTEMPTS):
        try:
            with db_base.session_scope() as session:
                checkin = CheckIn(
                    task_id=task_id,
                    sent_at=db_base.utcnow(),
                    message_sent=text,
                    telegram_message_id=message_id,
                )
                session.add(checkin)
                session.flush()
                return checkin.id, None
        except Exception as exc:  # noqa: BLE001 - see docstring
            last_error = exc
            if attempt < _CHECKIN_WRITE_MAX_ATTEMPTS - 1:
                time.sleep(_CHECKIN_WRITE_RETRY_BASE_DELAY_SECONDS * (attempt + 1))
    return None, last_error


def telegram_send_message(owner_name: str, text: str, task_id: int | None = None) -> dict:
    with db_base.session_scope() as session:
        person = people_repo.find_by_name(session, owner_name)
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
        result = telegram_integration.send_message(chat_id, text)
    except TelegramNotConfigured as exc:
        return {"error": str(exc)}

    message_id = (result.get("result") or {}).get("message_id")

    checkin_id = None
    checkin_error = None
    if task_id is not None:
        checkin_id, checkin_error = _record_checkin_with_retry(task_id, text, message_id)

    response = {
        "sent": True,
        "to": owner_name,
        "task_id": task_id,
        "checkin_id": checkin_id,
        "telegram_message_id": message_id,
    }
    if checkin_error is not None:
        response["checkin_recorded"] = False
        response["warning"] = (
            f"The message was delivered, but recording the check-in failed "
            f"after {_CHECKIN_WRITE_MAX_ATTEMPTS} attempts ({checkin_error}) - "
            f"{owner_name} may be re-chased even though they already received "
            f"this message. Worth checking manually."
        )
    return response


def _store_unmatched(session, person, chat_id, text, reason, candidates=None) -> dict:
    row = unmatched_repo.store(
        session,
        person_id=person.id if person else None,
        chat_id=chat_id,
        text=text,
        reason=reason,
        received_at=db_base.utcnow(),
        candidate_task_ids=candidates,
    )
    return {
        "unmatched_id": row.id,
        "person_name": person.name if person else None,
        "text": text,
        "reason": reason,
        "candidate_task_ids": candidates or [],
    }


async def peek_for_new_replies(timeout: int = 25) -> bool:
    with db_base.session_scope() as session:
        state = session.get(BotState, 1)
        offset = state.last_update_id + 1 if state and state.last_update_id is not None else None
    updates = await telegram_integration.get_updates_async(offset=offset, timeout=timeout)
    return bool(updates)


def telegram_get_updates() -> dict:
    with db_base.session_scope() as session:
        state = session.get(BotState, 1)
        if state is None:
            state = BotState(id=1, last_update_id=None)
            session.add(state)
            session.flush()

        offset = state.last_update_id + 1 if state.last_update_id is not None else None
        try:
            updates = telegram_integration.get_updates(offset=offset)
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
                    # linking attempt and not an update - discard it.
                    continue
                person = people_repo.find_by_link_code(session, code)
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
                target = checkins_repo.find_by_telegram_message_id(session, replied_to, person.id)
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
                        target.reply_received_at = db_base.utcnow()
                        replies.append({
                            "task_id": target.task_id,
                            "task_title": target.task.title,
                            "owner_name": person.name,
                            "reply_text": text,
                        })
                    continue

            open_checkins = checkins_repo.find_open_for_owner(session, person.id)

            if len(open_checkins) == 1:
                target = open_checkins[0]
                target.reply_text = text
                target.reply_received_at = db_base.utcnow()
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

        pending = unmatched_repo.list_pending(session)
        unmatched = [
            {
                "unmatched_id": row.id,
                "person_name": row.person.name if row.person else None,
                "text": row.text,
                "reason": row.reason,
                "candidate_task_ids": json.loads(row.candidate_task_ids or "[]"),
                "received_at_local": iso_local(row.received_at, db_base.LOCAL_TZ),
            }
            for row in pending
        ]

        return {"linked": linked, "replies": replies, "unmatched": unmatched}


def resolve_unmatched(unmatched_id: int, task_id: int | None = None) -> dict:
    with db_base.session_scope() as session:
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

        # Recorded with no sent_at/message_sent: this is an update that
        # arrived without us having asked for it, which is worth being
        # able to tell apart from a genuine ping-and-reply pair later.
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
