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

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


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


def _build_engine(db_path: str):
    return create_engine(f"sqlite:///{db_path}", future=True)


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
    """Create any missing tables.

    Note: this creates missing TABLES only - it does not add new COLUMNS to
    tables that already exist. A schema change to an existing table needs
    the database recreated (or a real migration - see
    pmchaser/db/migrations/, added in Phase 4).
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
