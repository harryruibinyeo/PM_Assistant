"""Golden-master output snapshot: proves "functionality unchanged" as a
fact, not a claim.

Runs the fixed scenario in tests/golden_scenario.py under frozen time and
diffs the exact JSON every one of the 16 tools returned against
tests/golden/tool_outputs.json, captured from the original, untouched
tools.py on this branch (see tests/generate_golden_master.py).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from freezegun import freeze_time

from tests.golden_scenario import FROZEN_INSTANT, run_scenario

GOLDEN_PATH = Path(__file__).parent / "golden" / "tool_outputs.json"


@pytest.fixture()
def fake_manager_send(fresh_db, monkeypatch: pytest.MonkeyPatch):
    import telegram_client

    sent: list[dict] = []

    def _fake_send(self, chat_id, text):
        sent.append({"chat_id": chat_id, "text": text})
        return {"ok": True, "result": {"message_id": 9001}}

    monkeypatch.setattr(telegram_client.TelegramClient, "send_message", _fake_send)
    monkeypatch.setenv("TASK_MANAGER_BOT_TOKEN", "test-token")
    return sent


def test_golden_master_tool_outputs_are_byte_identical(fresh_db, fake_telegram, fake_manager_send):
    tools = fresh_db
    with freeze_time(FROZEN_INSTANT):
        result = run_scenario(tools, fake_telegram, fake_manager_send)

    # Round-trip through JSON so the comparison matches exactly what the
    # committed snapshot file (and, eventually, an MCP client) would see.
    current = json.loads(json.dumps(result, sort_keys=True, default=str))

    assert GOLDEN_PATH.exists(), (
        f"No golden snapshot at {GOLDEN_PATH}. Generate it once with "
        f"`python tests/generate_golden_master.py`, review the output by "
        f"hand, and commit it - it becomes the reference every later phase "
        f"is checked against."
    )
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

    assert current == golden, (
        "Golden-master tool output drifted from the committed snapshot. "
        "Every tool's exact JSON shape is part of what the agent parses "
        "every turn (see the plan's \"hardest constraint\" section). If "
        "this is one of the 5 explicitly flagged bug fixes (Phase 2) or a "
        "deliberate Phase 3 change, regenerate the snapshot with "
        "generate_golden_master.py and show the diff in the commit; "
        "otherwise this is an unintended functional regression."
    )
