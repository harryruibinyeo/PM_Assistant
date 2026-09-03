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


def delete_referencing_task(session, task_id: int) -> None:
    """Delete every unmatched message that genuinely lists `task_id` among
    its JSON-encoded candidates.

    Phase 2 fix for the refactor plan's finding #1: the original query was
    `candidate_task_ids.like(f"%{task_id}%")` - a substring match against
    the JSON text, so deleting task 5 also deleted messages whose real
    candidates were e.g. [15, 25, 51] (both contain the substring "5").
    This does an exact membership check by actually parsing the JSON
    instead of pattern-matching its serialized text. Only rows with a
    non-null candidate_task_ids are fetched at all, so this stays cheap
    (that column is null for the common case - a single unambiguous
    candidate resolved a different way, or none).
    """
    candidates = session.execute(
        select(UnmatchedMessage).where(UnmatchedMessage.candidate_task_ids.is_not(None))
    ).scalars().all()
    to_delete = [
        row for row in candidates
        if task_id in json.loads(row.candidate_task_ids)
    ]
    for row in to_delete:
        session.delete(row)
