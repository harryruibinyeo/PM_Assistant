"""telegram_send_message atomicity, and telegram_get_updates's reply-matching
logic - the trickiest part of this service: threaded replies, ambiguous
replies, spontaneous updates, and resolve_unmatched's attach/dismiss paths.
"""

from __future__ import annotations

ALICE_CHAT = "555001"


def _link(tools, fake_telegram, name: str, chat_id: str) -> dict:
    person = tools.register_person(name)
    fake_telegram.push(chat_id, f"/start {person['link_code']}")
    tools.telegram_get_updates()
    return person


def test_messaging_an_unlinked_person_is_flagged_not_counted_as_ignored(fresh_db, fake_telegram):
    tools = fresh_db
    tools.register_person("Carol")
    result = tools.telegram_send_message("Carol", "hi")
    assert result.get("needs_linking") is True
    assert "error" in result


def test_send_with_task_id_records_a_checkin_atomically(fresh_db, fake_telegram):
    tools = fresh_db
    _link(tools, fake_telegram, "Alice", ALICE_CHAT)
    task_id = tools.create_task("Task", "Alice", "medium")["task_id"]

    result = tools.telegram_send_message("Alice", "how's it going?", task_id=task_id)

    assert result["sent"] is True
    assert result["checkin_id"] is not None
    assert result["telegram_message_id"] is not None


def test_send_on_someone_elses_task_is_rejected(fresh_db, fake_telegram):
    tools = fresh_db
    _link(tools, fake_telegram, "Alice", ALICE_CHAT)
    _link(tools, fake_telegram, "Bob", "555002")
    task_id = tools.create_task("Alice's task", "Alice", "medium")["task_id"]

    result = tools.telegram_send_message("Bob", "hey", task_id=task_id)

    assert "error" in result


def test_list_tasks_reflects_the_sent_checkin(fresh_db, fake_telegram):
    tools = fresh_db
    _link(tools, fake_telegram, "Alice", ALICE_CHAT)
    task_id = tools.create_task("Task", "Alice", "medium")["task_id"]
    tools.telegram_send_message("Alice", "ping", task_id=task_id)

    task = tools.list_tasks()[0]
    assert task["hours_since_last_checkin"] is not None
    assert task["unanswered_checkin_count"] == 1
    assert task["last_checkin_answered"] is False
    assert task["owner_is_linked"] is True


def test_unambiguous_reply_is_matched_to_the_right_task(fresh_db, fake_telegram):
    tools = fresh_db
    _link(tools, fake_telegram, "Alice", ALICE_CHAT)
    task_id = tools.create_task("Task", "Alice", "medium")["task_id"]
    tools.telegram_send_message("Alice", "ping", task_id=task_id)

    fake_telegram.push(ALICE_CHAT, "almost done")
    result = tools.telegram_get_updates()

    assert len(result["replies"]) == 1
    assert result["replies"][0]["task_id"] == task_id
    assert result["replies"][0]["reply_text"] == "almost done"


def test_ambiguous_reply_is_not_guessed_at(fresh_db, fake_telegram):
    tools = fresh_db
    _link(tools, fake_telegram, "Alice", ALICE_CHAT)
    task_a = tools.create_task("Task A", "Alice", "medium")["task_id"]
    task_b = tools.create_task("Task B", "Alice", "medium")["task_id"]
    tools.telegram_send_message("Alice", "ping A", task_id=task_a)
    tools.telegram_send_message("Alice", "ping B", task_id=task_b)

    fake_telegram.push(ALICE_CHAT, "done")
    result = tools.telegram_get_updates()

    assert result["replies"] == []
    assert len(result["unmatched"]) == 1
    unmatched = result["unmatched"][0]
    assert set(unmatched["candidate_task_ids"]) == {task_a, task_b}


def test_threaded_reply_resolves_ambiguity(fresh_db, fake_telegram):
    tools = fresh_db
    _link(tools, fake_telegram, "Alice", ALICE_CHAT)
    task_a = tools.create_task("Task A", "Alice", "medium")["task_id"]
    task_b = tools.create_task("Task B", "Alice", "medium")["task_id"]
    tools.telegram_send_message("Alice", "ping A", task_id=task_a)
    send_b = tools.telegram_send_message("Alice", "ping B", task_id=task_b)

    fake_telegram.push(ALICE_CHAT, "done with B", reply_to=send_b["telegram_message_id"])
    result = tools.telegram_get_updates()

    assert len(result["replies"]) == 1
    assert result["replies"][0]["task_id"] == task_b
    # The earlier, still-ambiguous ping for task A must remain untouched.
    assert result["unmatched"] == []


def test_spontaneous_message_with_no_ping_outstanding_is_unmatched(fresh_db, fake_telegram):
    tools = fresh_db
    _link(tools, fake_telegram, "Alice", ALICE_CHAT)

    fake_telegram.push(ALICE_CHAT, "starting on the report now")
    result = tools.telegram_get_updates()

    assert result["replies"] == []
    assert len(result["unmatched"]) == 1
    assert "spontaneous" in result["unmatched"][0]["reason"]


def test_resolve_unmatched_attaches_to_a_task(fresh_db, fake_telegram):
    tools = fresh_db
    _link(tools, fake_telegram, "Alice", ALICE_CHAT)
    task_id = tools.create_task("Task", "Alice", "medium")["task_id"]
    fake_telegram.push(ALICE_CHAT, "starting now")
    unmatched_id = tools.telegram_get_updates()["unmatched"][0]["unmatched_id"]

    result = tools.resolve_unmatched(unmatched_id, task_id=task_id)

    assert result["attached"] is True
    # No longer returned as pending.
    fake_telegram.push(ALICE_CHAT, "unrelated noise")
    still_pending = tools.telegram_get_updates()["unmatched"]
    assert not any(u["unmatched_id"] == unmatched_id for u in still_pending)


def test_resolve_unmatched_can_dismiss_with_no_task(fresh_db, fake_telegram):
    tools = fresh_db
    _link(tools, fake_telegram, "Alice", ALICE_CHAT)
    fake_telegram.push(ALICE_CHAT, "lol ok")
    unmatched_id = tools.telegram_get_updates()["unmatched"][0]["unmatched_id"]

    result = tools.resolve_unmatched(unmatched_id)

    assert result["dismissed"] is True


def test_resolving_twice_is_rejected(fresh_db, fake_telegram):
    tools = fresh_db
    _link(tools, fake_telegram, "Alice", ALICE_CHAT)
    fake_telegram.push(ALICE_CHAT, "hi")
    unmatched_id = tools.telegram_get_updates()["unmatched"][0]["unmatched_id"]
    tools.resolve_unmatched(unmatched_id)

    assert "error" in tools.resolve_unmatched(unmatched_id)
