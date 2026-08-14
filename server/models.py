"""SQLAlchemy data model for pm-chaser: Person, Task, CheckIn, UnmatchedMessage,
and a tiny BotState row for tracking Telegram's getUpdates cursor.

This is the only place pm-chaser-mcp's data lives. Everything else in the
service reads/writes through here.

Datetime convention: everything is stored NAIVE but always UTC. SQLite has no
true timezone-aware datetime type — SQLAlchemy round-trips values through it as
plain strings and timezone info does not survive the trip, so comparing a
timezone-aware Python datetime against one loaded from the DB raises
TypeError. Standardizing on naive-UTC sidesteps that entirely. Conversion to
and from the user's local timezone happens at the edges (see tools.py).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


# The timezone the humans using this actually live in. Deadlines typed without
# an explicit offset ("Friday 5pm") are interpreted here, and times are shown
# back in this zone — otherwise a naive deadline silently becomes UTC and lands
# hours off.
LOCAL_TZ = ZoneInfo(os.environ.get("PM_CHASER_TZ", "Asia/Singapore"))


def utcnow() -> datetime:
    """Naive datetime, but always UTC — see the module docstring."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Person(Base):
    __tablename__ = "people"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    role = Column(String, nullable=False, default="team_member")  # "team_member" | "manager"
    telegram_username = Column(String, nullable=True)
    telegram_chat_id = Column(String, nullable=True, unique=True)
    link_code = Column(String, nullable=True, unique=True)
    created_at = Column(DateTime, default=utcnow)

    # Explicit foreign_keys required as of Task.manager_id: with two FK
    # columns from tasks to people, SQLAlchemy can no longer infer which one
    # this side of the owner<->tasks relationship should join on.
    tasks = relationship("Task", back_populates="owner", foreign_keys="Task.owner_id")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    owner_id = Column(Integer, ForeignKey("people.id"), nullable=False)
    # Which manager this task answers to. Deliberately lives here, not on
    # Person — a person can be the manager for one task and just a
    # contributor on another (and an owner can have tasks under different
    # managers), so the relationship only makes sense per-task. Nullable so
    # a task can exist without one (shouldn't happen in practice — create_task
    # always resolves one — but nothing downstream should assume it's set).
    manager_id = Column(Integer, ForeignKey("people.id"), nullable=True)
    deadline = Column(DateTime, nullable=True)
    priority = Column(String, nullable=False, default="medium")  # "low" | "medium" | "high"
    # Required at the tool layer (create_task has no default) — it drives
    # chase's per-task re-ping floor, so a task without a real priority
    # decision would silently get chased on an arbitrary cadence.
    status = Column(String, nullable=False, default="not_started")
    # "not_started" | "in_progress" | "blocked" | "done" | "cancelled"
    progress_pct = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    owner = relationship("Person", back_populates="tasks", foreign_keys=[owner_id])
    manager = relationship("Person", foreign_keys=[manager_id])
    check_ins = relationship("CheckIn", back_populates="task", order_by="CheckIn.id")


class CheckIn(Base):
    """One chase ping and (maybe) the reply it got.

    `telegram_message_id` is the ID Telegram assigned to the outgoing message.
    When someone uses Telegram's reply feature, the incoming update carries
    that ID back, which is how a reply gets matched to the right task even when
    several pings are outstanding.
    """

    __tablename__ = "check_ins"

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    sent_at = Column(DateTime, nullable=True)
    message_sent = Column(String, nullable=True)
    telegram_message_id = Column(Integer, nullable=True)
    reply_text = Column(String, nullable=True)
    reply_received_at = Column(DateTime, nullable=True)
    parsed_status = Column(String, nullable=True)
    parsed_progress_pct = Column(Integer, nullable=True)
    parsed_blockers = Column(String, nullable=True)

    task = relationship("Task", back_populates="check_ins")


class UnmatchedMessage(Base):
    """An incoming message that couldn't be tied to an open check-in.

    Persisted rather than returned-and-forgotten because reading from Telegram
    is destructive: getUpdates permanently advances the cursor, so anything not
    stored here is gone for good if the agent's run fails partway through.

    Covers three cases: a spontaneous update with no ping outstanding, a reply
    to a check-in that was already answered, and a reply that arrived while
    several pings were outstanding with no way to tell which it meant.
    """

    __tablename__ = "unmatched_messages"

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("people.id"), nullable=True)
    chat_id = Column(String, nullable=True)
    text = Column(String, nullable=True)
    received_at = Column(DateTime, default=utcnow)
    reason = Column(String, nullable=True)
    candidate_task_ids = Column(String, nullable=True)  # JSON list, when ambiguous
    handled = Column(Boolean, nullable=False, default=False)

    person = relationship("Person")


class BotState(Base):
    """Single-row table holding Telegram's getUpdates cursor (last_update_id),
    plus small persistent bot-wide settings that don't belong to any one
    Person or Task — currently just the manager-bot's chosen persona name.
    """

    __tablename__ = "bot_state"

    id = Column(Integer, primary_key=True)
    last_update_id = Column(Integer, nullable=True)
    # task-manager-bot's chosen persona name — "Toby" or "Abby", or None if
    # the manager hasn't picked one yet.
    assistant_name = Column(String, nullable=True)


def _default_db_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "pm_chaser.db")


DB_PATH = os.environ.get("PM_CHASER_DB_PATH", _default_db_path())
engine = create_engine(f"sqlite:///{DB_PATH}", future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create any missing tables.

    Note: this creates missing TABLES only — it does not add new COLUMNS to
    tables that already exist. A schema change to an existing table needs the
    database recreated (or a real migration).
    """
    Base.metadata.create_all(engine)


@contextmanager
def session_scope():
    """Provide a transactional scope for a single unit of work."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
