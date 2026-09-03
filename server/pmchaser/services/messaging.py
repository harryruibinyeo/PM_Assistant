"""Telegram send/receive and reply-matching business logic - called by the
thin adapters in pmchaser/mcp/tools.py.

Two intentional Phase-2 fix targets are preserved unchanged here (see the
refactor plan's findings #2 and the "hardest constraint" section): sending
a message and recording its check-in are two separate transactions, and
the BotState.last_update_id cursor advance happens inside
telegram_get_updates only - peek_for_new_replies below never touches it.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from pmchaser.db import base as db_base
from pmchaser.db.models import BotState, CheckIn, Person, Task, UnmatchedMessage
from pmchaser.domain.time_utils import iso_local
from pmchaser.integrations import telegram as telegram_integration
from pmchaser.integrations.telegram import TelegramNotConfigured
from pmchaser.repositories import checkins as checkins_repo
from pmchaser.repositories import people as people_repo
from pmchaser.repositories import unmatched as unmatched_repo


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
    if task_id is not None:
        with db_base.session_scope() as session:
            checkin = CheckIn(
                task_id=task_id,
                sent_at=db_base.utcnow(),
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
