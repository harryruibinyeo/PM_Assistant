"""notify_manager business logic - sends via S.A.M.'s own bot identity
(TASK_MANAGER_BOT_TOKEN), a separate Telegram bot/token from the
employee-facing chase bot the rest of this package uses.

Deliberately instantiates its own TelegramClient(token) rather than going
through pmchaser.integrations.telegram's module-level send_message() -
this is the duplicate-logic finding noted in tests/test_notify_manager.py
and the refactor plan (#12); left exactly as-is for Phase 1's behavior-
identical move.
"""

from __future__ import annotations

import os

from pmchaser.db import base as db_base
from pmchaser.integrations.telegram import TelegramClient
from pmchaser.repositories import people as people_repo


def notify_manager(text: str) -> dict:
    token = os.environ.get("TASK_MANAGER_BOT_TOKEN")
    if not token:
        return {
            "error": "TASK_MANAGER_BOT_TOKEN not configured — cannot send as S.A.M.",
            "sent": False,
        }

    with db_base.session_scope() as session:
        candidate_managers = people_repo.list_managers(session)
        if len(candidate_managers) != 1:
            return {
                "error": f"Expected exactly one registered manager, found {len(candidate_managers)}.",
                "sent": False,
            }
        manager = candidate_managers[0]
        if not manager.telegram_chat_id:
            return {
                "error": f"'{manager.name}' hasn't linked Telegram yet — cannot be messaged.",
                "sent": False,
                "needs_linking": True,
            }
        chat_id = manager.telegram_chat_id
        manager_name = manager.name

    try:
        result = TelegramClient(token).send_message(chat_id, text)
    except Exception as exc:
        return {"error": str(exc), "sent": False}

    return {
        "sent": True,
        "to": manager_name,
        "telegram_message_id": (result.get("result") or {}).get("message_id"),
    }
