"""Shared pytest fixtures for the pm-chaser test suite.

Isolation strategy
-------------------
`models.py` binds its SQLAlchemy engine to a module-level `DB_PATH` that is
resolved from `PM_CHASER_DB_PATH` at *import* time (see models.py's own
docstring/comments). The original test_tools.py worked around this by
setting the env var before the interpreter even started and running one
long linear script against a single DB for the whole run.

For real per-test isolation without touching a single line of production
code, `fresh_db` below points that env var at a brand-new temp file and
reloads `models` / `telegram_client` / `tools` so a fresh engine + session
factory is created for each test. This is zero-production-code-change by
construction — it is purely a test-harness technique.

`fake_telegram` builds on `fresh_db` and swaps `telegram_client.send_message`
/ `telegram_client.get_updates` for an in-memory double, exactly as
test_tools.py already did — no real network, no bot token needed.
"""

from __future__ import annotations

import importlib
import tempfile
import uuid
from pathlib import Path
from typing import Iterator

import pytest


class FakeTelegram:
    """In-memory Telegram double: no network, deterministic IDs.

    Mirrors the shape of `server/test_tools.py`'s original `FakeTelegram`
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

    Yields the freshly-reloaded `tools` module — import it via this
    fixture's return value, not via a bare top-level `import tools`, or
    you'll get the *previous* test's module state.
    """
    db_path = Path(tempfile.gettempdir()) / f"pm_chaser_test_{uuid.uuid4().hex}.db"
    monkeypatch.setenv("PM_CHASER_DB_PATH", str(db_path))
    monkeypatch.setenv("PM_CHASER_TZ", "Asia/Singapore")
    # Deliberately unset so a dev's real .env (if one leaked into the shell)
    # can never cause a test to hit real Telegram or a real DB path.
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TASK_MANAGER_BOT_TOKEN", raising=False)

    import models
    import telegram_client
    import tools

    # Reload in dependency order so each gets a fresh module-level engine /
    # _default_client bound to the env vars just set above.
    importlib.reload(models)
    importlib.reload(telegram_client)
    importlib.reload(tools)

    models.init_db()

    yield tools

    models.engine.dispose()
    for suffix in ("", "-journal", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()


@pytest.fixture()
def fake_telegram(fresh_db, monkeypatch: pytest.MonkeyPatch) -> FakeTelegram:
    """Wires a `FakeTelegram` into the *same* reloaded `telegram_client`
    module that `fresh_db`'s `tools` module is calling into.

    Depends on `fresh_db` (not the other way around) so reload order is
    guaranteed: `fresh_db` reloads telegram_client first, then this fixture
    patches attributes on that already-reloaded module object.
    """
    import telegram_client  # noqa: WPS433 - intentional post-reload import

    fake = FakeTelegram()
    monkeypatch.setattr(telegram_client, "send_message", fake.send_message)
    monkeypatch.setattr(telegram_client, "get_updates", fake.get_updates)
    return fake


@pytest.fixture()
def tools_mod(fresh_db):
    """Readability alias for tests that touch the DB but never Telegram."""
    return fresh_db
