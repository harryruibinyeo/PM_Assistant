#!/usr/bin/env python
"""One-time (and re-run-when-deliberate) generator for
tests/golden/tool_outputs.json.

Run this, review the diff by hand, and commit the result. Never run it to
"make a failing golden-master test pass" without reading exactly what
changed and why - that defeats its entire purpose. See tests/test_golden_master.py
and the plan's "hardest constraint" section.

Usage (from server/):
    .venv/Scripts/python.exe tests/generate_golden_master.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import uuid
from pathlib import Path
from unittest import mock

from freezegun import freeze_time

sys.path.insert(0, str(Path(__file__).parent.parent))

GOLDEN_PATH = Path(__file__).parent / "golden" / "tool_outputs.json"


class _FakeTelegram:
    def __init__(self):
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


def main() -> None:
    import os

    db_path = Path(tempfile.gettempdir()) / f"pm_chaser_golden_{uuid.uuid4().hex}.db"
    os.environ["PM_CHASER_DB_PATH"] = str(db_path)
    os.environ["PM_CHASER_TZ"] = "Asia/Singapore"
    os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    os.environ["TASK_MANAGER_BOT_TOKEN"] = "test-token"

    from pmchaser.db import base as db_base
    from pmchaser.integrations import telegram as telegram_integration
    from pmchaser.mcp import tools

    db_base.configure_for_testing(str(db_path), tz_name="Asia/Singapore")
    db_base.init_db()

    fake = _FakeTelegram()
    telegram_integration.send_message = fake.send_message
    telegram_integration.get_updates = fake.get_updates

    manager_sent: list[dict] = []

    def _fake_manager_send(self, chat_id, text):
        manager_sent.append({"chat_id": chat_id, "text": text})
        return {"ok": True, "result": {"message_id": 9001}}

    with mock.patch.object(telegram_integration.TelegramClient, "send_message", _fake_manager_send):
        # local import: tests/ on sys.path via conftest rootdir
        from golden_scenario import FROZEN_INSTANT, normalize_for_comparison, run_scenario

        with freeze_time(FROZEN_INSTANT):
            result = run_scenario(tools, fake, manager_sent)

    # link_code is deliberately non-deterministic (secrets.choice) - see
    # golden_scenario.py's module docstring. Normalized before writing so
    # the committed file doesn't pin an arbitrary, meaningless value that
    # would just look like a diff on every regeneration.
    result = normalize_for_comparison(result)

    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(
        json.dumps(result, indent=2, sort_keys=True, default=str, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Wrote {GOLDEN_PATH} ({len(result)} scenario steps).")

    db_base.engine.dispose()
    for suffix in ("", "-journal", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()


if __name__ == "__main__":
    main()
