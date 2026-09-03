"""Shared pytest fixtures for the pm-chaser test suite.

Isolation strategy
-------------------
As of the Phase 1 structural refactor, `pmchaser.db.base` exposes
`configure_for_testing(db_path)`, which repoints the module's engine/
session factory at a fresh SQLite file by reassigning module-level names -
no `importlib.reload()` needed anywhere. Every consumer in this codebase
reaches the DB through a live module-attribute lookup
(`db_base.session_scope()`, `db_base.LOCAL_TZ`) rather than capturing a
value at its own import time, so reassigning those names in
`pmchaser/db/base.py` is enough for every already-imported module - down
through repositories, services, and pmchaser.mcp.tools - to pick up the
change on its very next call. See pmchaser/db/base.py's module docstring
for the full reasoning; this replaced an earlier, more fragile
`importlib.reload()`-cascade approach used before the single-file
tools.py/models.py were split into this package.

`fake_telegram` swaps `pmchaser.integrations.telegram`'s module-level
`send_message`/`get_updates` for an in-memory double, exactly as the
original server/test_tools.py did - no real network, no bot token needed.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest


class FakeTelegram:
    """In-memory Telegram double: no network, deterministic IDs.

    Mirrors the shape of the original server/test_tools.py's FakeTelegram
    so ported test scenarios need no behavioral changes.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.queue: list[dict] = []
        self._message_id = 100
        self._update_id = 1000

    def send_message(self, chat_id, text):
        self._message_id += 1
        self.sent.append({"chat_id": chat_id, "text": text, "message_id": self._message_id})
        return {"ok": True, "result": {"message_id": self._message_id}}

    def get_updates(self, offset=None, timeout=0):
        return [u for u in self.queue if offset is None or u["update_id"] >= offset]

    def push(self, chat_id, text, reply_to=None):
        self._update_id += 1
        msg = {"chat": {"id": int(chat_id)}, "text": text}
        if reply_to is not None:
            msg["reply_to_message"] = {"message_id": reply_to}
        self.queue.append({"update_id": self._update_id, "message": msg})

    def last_message_id(self):
        return self.sent[-1]["message_id"]


@pytest.fixture()
def fresh_db(monkeypatch: pytest.MonkeyPatch):
    """A brand-new, empty SQLite DB, scoped to exactly one test.

    Yields the `pmchaser.mcp.tools` module - imported once, normally,
    like any other module; per-test isolation comes from
    `db_base.configure_for_testing()`, not from reloading this module.
    """
    db_path = Path(tempfile.gettempdir()) / f"pm_chaser_test_{uuid.uuid4().hex}.db"
    monkeypatch.setenv("PM_CHASER_DB_PATH", str(db_path))
    monkeypatch.setenv("PM_CHASER_TZ", "Asia/Singapore")
    # Deliberately unset so a dev's real .env (if one leaked into the shell)
    # can never cause a test to hit real Telegram or a real DB path.
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TASK_MANAGER_BOT_TOKEN", raising=False)

    from pmchaser.db import base as db_base
    from pmchaser.mcp import tools

    db_base.configure_for_testing(str(db_path), tz_name="Asia/Singapore")
    db_base.init_db()

    yield tools

    db_base.engine.dispose()
    for suffix in ("", "-journal", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()


@pytest.fixture()
def fake_telegram(fresh_db, monkeypatch: pytest.MonkeyPatch) -> FakeTelegram:
    """Wires a `FakeTelegram` into `pmchaser.integrations.telegram`, the
    module every service in this package calls into for Telegram I/O."""
    from pmchaser.integrations import telegram as telegram_integration

    fake = FakeTelegram()
    monkeypatch.setattr(telegram_integration, "send_message", fake.send_message)
    monkeypatch.setattr(telegram_integration, "get_updates", fake.get_updates)
    return fake


@pytest.fixture()
def tools_mod(fresh_db):
    """Readability alias for tests that touch the DB but never Telegram."""
    return fresh_db
