"""record_reply_outcome: the Phase 3 composite tool that collapses
update_task + an owner acknowledgment + a manager notification into one
call for pmchaser-bot's reply-interpretation flow. See
pmchaser/services/reply_outcomes.py's module docstring for why.
"""

from __future__ import annotations

import pytest

from tests.helpers import link


@pytest.fixture()
def fake_manager_bot(fresh_db, monkeypatch: pytest.MonkeyPatch):
    """notify_manager instantiates its own TelegramClient(token) directly
    (see pmchaser/services/notifications.py) rather than going through
    the module-level send_message() `fake_telegram` patches - same
    pattern as test_notify_manager.py's fixture of the same name."""
    from pmchaser.integrations import telegram as telegram_integration

    sent: list[dict] = []

    def _fake_send(self, chat_id, text):
        sent.append({"chat_id": chat_id, "text": text})
        return {"ok": True, "result": {"message_id": 4242}}

    monkeypatch.setattr(telegram_integration.TelegramClient, "send_message", _fake_send)
    monkeypatch.setenv("TASK_MANAGER_BOT_TOKEN", "test-token")
    return sent


def test_updates_the_task_acks_the_owner_and_notifies_the_manager(fresh_db, fake_telegram, fake_manager_bot):
    tools = fresh_db
    manager = tools.register_person("Bob", role="manager")
    fake_telegram.push("555099", f"/start {manager['link_code']}")
    link(tools, fake_telegram, "Alice", "555001")
    task_id = tools.create_task("Submit report", "Alice", "medium")["task_id"]

    result = tools.record_reply_outcome(
        task_id,
        ack_text="Got it, marked as done - nice work.",
        manager_note="Alice marked 'Submit report' done.",
        status="done",
        progress_pct=100,
    )

    assert result["task"]["status"] == "done"
    assert result["task"]["progress_pct"] == 100

    assert result["ack"]["sent"] is True
    assert result["ack"]["to"] == "Alice"
    assert result["ack"]["task_id"] is None  # never a chase - no check-in recorded
    assert result["ack"]["telegram_message_id"] is not None

    assert result["manager_notification"]["sent"] is True
    assert result["manager_notification"]["to"] == "Bob"


def test_invalid_task_id_returns_only_the_error_no_ack_or_notification(fresh_db, fake_telegram):
    """If the task lookup itself fails, there is nothing to acknowledge
    or notify about - attempting either would reference a task that was
    never actually updated. Confirmed by checking no message was sent at
    all, not just that the response looks right."""
    tools = fresh_db
    tools.register_person("Bob", role="manager")

    result = tools.record_reply_outcome(
        99999,
        ack_text="This should never be sent.",
        manager_note="This should never be sent either.",
        status="done",
    )

    assert "error" in result["task"]
    assert "ack" not in result
    assert "manager_notification" not in result
    assert fake_telegram.sent == []


def test_ack_never_creates_a_checkin_even_though_a_real_task_id_exists(fresh_db, fake_telegram):
    """The acknowledgment must never be recorded as a chase check-in -
    doing so would create a dangling open ping nobody is waiting on,
    exactly the pitfall task-chaser SKILL.md step 3 warns about."""
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    link(tools, fake_telegram, "Alice", "555001")
    task_id = tools.create_task("Task", "Alice", "medium")["task_id"]

    tools.record_reply_outcome(
        task_id, ack_text="Thanks!", manager_note="Alice replied.", status="in_progress",
    )

    task = tools.list_tasks(owner_name="Alice")[0]
    assert task["total_checkins"] == 0
    assert task["last_checkin_sent_at_local"] is None


def test_can_update_progress_only_without_changing_status(fresh_db, fake_telegram):
    """status and progress_pct are independently optional, matching
    update_task's own flexibility - a reply might report progress
    without a status change."""
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    link(tools, fake_telegram, "Alice", "555001")
    task_id = tools.create_task("Task", "Alice", "medium")["task_id"]
    tools.update_task(task_id, status="in_progress")

    result = tools.record_reply_outcome(
        task_id, ack_text="Thanks for the update!", manager_note="Alice: 50% done.",
        progress_pct=50,
    )

    assert result["task"]["status"] == "in_progress"  # unchanged
    assert result["task"]["progress_pct"] == 50
