"""Database plumbing: engine, session factory, and schema initialization.

Split out of the original server/models.py (which combined this with the
ORM entity classes - see pmchaser/db/models.py for those) as part of the
production-grade refactor. Behavior is unchanged: DB_PATH/LOCAL_TZ still
resolve from PM_CHASER_DB_PATH/PM_CHASER_TZ at import time in production,
exactly as before.

Access convention (load-bearing for testing - read this before adding a
new module that needs the DB): every consumer reaches `engine`,
`session_scope`, and `LOCAL_TZ` through a *module-attribute* lookup -
`from pmchaser.db import base as db_base` then `db_base.session_scope()`
/ `db_base.LOCAL_TZ` - never a bare `from pmchaser.db.base import engine`
used directly. Python resolves a name inside a function body against the
module's live `__dict__` at call time, so `configure_for_testing()` below
can repoint every already-imported caller at a fresh test database just
by reassigning these module-level names - no `importlib.reload()` needed
anywhere in the codebase, and no risk of some far-off module silently
holding onto a stale engine bound to a previous test's temp file.

Datetime convention: everything is stored NAIVE but always UTC. SQLite has
no true timezone-aware datetime type - SQLAlchemy round-trips values
through it as plain strings and timezone info does not survive the trip,
so comparing a timezone-aware Python datetime against one loaded from the
DB raises TypeError. Standardizing on naive-UTC sidesteps that entirely.
Conversion to and from the user's local timezone happens at the edges
(see pmchaser/domain/time_utils.py).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Milliseconds SQLite will silently retry an operation against a lock
# before raising "database is locked", instead of failing immediately.
# Matters once more than one process touches the same file concurrently -
# the chase_listener-triggered sweep, the manager's live chat, and (from
# Phase 5) the dashboard. 5s comfortably covers a single write transaction
# without making a genuinely stuck writer hang a caller indefinitely.
_BUSY_TIMEOUT_MS = 5000

# One row per (table, columns) pair actually queried by a WHERE/JOIN/ORDER
# BY somewhere in pmchaser/repositories/ - see the refactor plan's finding
# #8. `unique=True` columns (Person.telegram_chat_id, Person.link_code)
# already get an index implicitly from their UNIQUE constraint and are
# deliberately not repeated here.
_INDEXES = [
    ("idx_tasks_owner_id", "tasks", "owner_id"),
    ("idx_tasks_status", "tasks", "status"),
    ("idx_tasks_deadline", "tasks", "deadline"),
    ("idx_checkins_task_id", "check_ins", "task_id"),
    ("idx_checkins_telegram_message_id", "check_ins", "telegram_message_id"),
    ("idx_unmatched_handled", "unmatched_messages", "handled"),
]


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Naive datetime, but always UTC - see the module docstring."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _default_db_path() -> str:
    # This file lives at server/pmchaser/db/base.py - three directories
    # below server/, so parents[2] is server/ itself. The original
    # single-file models.py computed this as its own directory (also
    # server/) - this MUST resolve to the identical default path after the
    # module split. Docker always sets PM_CHASER_DB_PATH explicitly (see
    # server/Dockerfile), so this fallback only matters for an ad-hoc local
    # run with no .env - but it must still match exactly, or that scenario
    # silently starts writing to a different file with no error. Covered
    # by tests/test_db_path.py.
    server_dir = Path(__file__).resolve().parents[2]
    return str(server_dir / "pm_chaser.db")


def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:
    """Applied to every new DBAPI connection this engine opens - PRAGMAs
    other than journal_mode are per-connection in SQLite, not persisted in
    the file, so this must run on `connect`, not once at startup.

    WAL (write-ahead logging) lets readers and a writer proceed
    concurrently instead of the default rollback-journal mode's
    whole-database write lock - the concrete fix for finding #16 (SQLite
    without WAL, multiple processes hitting the same file). busy_timeout
    is the safety net for the brief moments two writers still do collide.

    Deliberately NOT also setting `PRAGMA foreign_keys=ON` here, tempting
    as that looks next to two other PRAGMAs: SQLite defaults it OFF, and
    pmchaser/services/people.py's delete_person only checks tasks a person
    *owns*, not tasks that reference them via Task.manager_id - turning on
    enforcement would make deleting a manager who owns no tasks but is
    referenced as someone else's manager_id start raising IntegrityError
    where it previously succeeded. That's a real, separate finding, not
    something to fix as a side effect of a WAL/busy_timeout change; out of
    scope for this phase.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    cursor.close()


def _build_engine(db_path: str):
    new_engine = create_engine(f"sqlite:///{db_path}", future=True)
    event.listen(new_engine, "connect", _set_sqlite_pragmas)
    return new_engine


def _ensure_indexes(engine) -> None:
    """Idempotent, additive-only index creation - safe to run on every
    startup against both a brand-new database (init_db's create_all just
    created these tables) and an existing production one with real
    history (create_all itself is a no-op there; CREATE INDEX IF NOT
    EXISTS is not - see finding #8). This is deliberately NOT done via
    Base.metadata's own `index=True` column option: that only takes effect
    on a table create_all() actually creates, which would silently skip
    every already-deployed database - the exact case this needs to reach.
    A real migration tool (Phase 4) is still required for anything that
    changes column layout; adding an index is non-destructive and
    reversible, so it doesn't need to wait for that.
    """
    with engine.begin() as conn:
        for index_name, table_name, column_name in _INDEXES:
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name}({column_name})"
            ))


LOCAL_TZ = ZoneInfo(os.environ.get("PM_CHASER_TZ", "Asia/Singapore"))
DB_PATH = os.environ.get("PM_CHASER_DB_PATH", _default_db_path())
engine = _build_engine(DB_PATH)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def configure_for_testing(db_path: str, tz_name: str | None = None) -> None:
    """Test-only: repoint the module at a fresh SQLite file (and,
    optionally, a different LOCAL_TZ) without a process restart. See this
    module's docstring for why reassigning these names is sufficient for
    every already-imported caller to pick up the change on its next call.

    Disposes the previous engine first so SQLite doesn't hold a stale file
    handle open - matters on Windows, where an open file can't be deleted.
    """
    global DB_PATH, engine, SessionLocal, LOCAL_TZ
    engine.dispose()
    DB_PATH = db_path
    engine = _build_engine(db_path)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    if tz_name is not None:
        LOCAL_TZ = ZoneInfo(tz_name)


def init_db() -> None:
    """Create any missing tables, then ensure every index in _INDEXES
    exists.

    Note: create_all() creates missing TABLES only - it does not add new
    COLUMNS to tables that already exist. A schema change to an existing
    table needs the database recreated (or a real migration - see
    pmchaser/db/migrations/, added in Phase 4). Index creation
    (_ensure_indexes) is a separate, safe exception to that limitation -
    see its own docstring for why.

    The local import below matters: splitting the original single-file
    models.py into this module (engine/session plumbing) and
    pmchaser/db/models.py (the ORM entity classes) means Base.metadata is
    only populated once something has imported db.models - previously
    that was impossible to get wrong, since defining a table and creating
    it lived in the same file by construction. In practice main.py and
    every test fixture already import the full pmchaser.mcp.tools chain
    (which pulls in db.models) before ever calling init_db(), so this
    hasn't caused a real failure - but calling init_db() from anything
    that hasn't already done so would otherwise create_all() against an
    empty Base.metadata (zero tables, no error) and then fail confusingly
    in _ensure_indexes with "no such table". A local import (not
    module-level, which would be circular - db.models itself imports
    Base/utcnow from this module) makes init_db() self-sufficient.
    """
    from pmchaser.db import models as _models  # noqa: F401

    Base.metadata.create_all(engine)
    _ensure_indexes(engine)


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
