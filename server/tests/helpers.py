"""Small shared helpers used across test modules - not a test file itself
(no test_ prefix, so pytest won't try to collect it)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

LOCAL = ZoneInfo("Asia/Singapore")  # matches PM_CHASER_TZ set by fresh_db


def local_iso(delta: timedelta) -> str:
    """A naive local-time ISO string offset from now by `delta`."""
    return (datetime.now(LOCAL) + delta).replace(tzinfo=None).isoformat()


def backdate_last_checkin(task_id: int, hours_ago: float) -> None:
    """Directly rewrite the most recent check-in's sent_at, simulating
    elapsed time without sleeping (same technique the original
    server/test_tools.py used)."""
    from pmchaser.db import base as db_base
    from pmchaser.db.models import CheckIn

    with db_base.session_scope() as session:
        checkin = session.execute(
            select(CheckIn)
            .where(CheckIn.task_id == task_id)
            .order_by(CheckIn.id.desc())
        ).scalars().first()
        checkin.sent_at = db_base.utcnow() - timedelta(hours=hours_ago)


def link(tools, fake_telegram, name: str, chat_id: str) -> dict:
    """Register + simulate the /start <code> linking handshake."""
    person = tools.register_person(name)
    fake_telegram.push(chat_id, f"/start {person['link_code']}")
    tools.telegram_get_updates()
    return person
