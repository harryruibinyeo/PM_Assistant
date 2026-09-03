"""UnmatchedMessage queries."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func, select

from pmchaser.db.models import UnmatchedMessage


def store(
    session,
    person_id: int | None,
    chat_id: str | None,
    text: str | None,
    reason: str | None,
    received_at: datetime,
    candidate_task_ids: list[int] | None = None,
) -> UnmatchedMessage:
    row = UnmatchedMessage(
        person_id=person_id,
        chat_id=chat_id,
        text=text,
        received_at=received_at,
        reason=reason,
        candidate_task_ids=json.dumps(candidate_task_ids) if candidate_task_ids else None,
    )
    session.add(row)
    session.flush()
    return row


def list_pending(session) -> list[UnmatchedMessage]:
    return list(
        session.execute(
            select(UnmatchedMessage).where(UnmatchedMessage.handled.is_(False))
        ).scalars().all()
    )


def count_pending(session) -> int:
    return session.execute(
        select(func.count(UnmatchedMessage.id)).where(UnmatchedMessage.handled.is_(False))
    ).scalar_one()
