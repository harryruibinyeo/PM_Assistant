"""Telegram account linking via /start <code>, and stranger/no-op handling."""

from __future__ import annotations

ALICE_CHAT = "555001"
STRANGER_CHAT = "999999"


def test_bare_start_is_discarded(fresh_db, fake_telegram):
    tools = fresh_db
    fake_telegram.push(ALICE_CHAT, "/start")  # Telegram's own auto-message
    result = tools.telegram_get_updates()
    assert not any(u["text"] == "/start" for u in result["unmatched"])


def test_stranger_messages_are_discarded(fresh_db, fake_telegram):
    tools = fresh_db
    fake_telegram.push(STRANGER_CHAT, "hello who is this")
    result = tools.telegram_get_updates()
    assert result["linked"] == []
    assert not any("hello who" in (u["text"] or "") for u in result["unmatched"])


def test_start_with_valid_code_links_the_person(fresh_db, fake_telegram):
    tools = fresh_db
    alice = tools.register_person("Alice")
    fake_telegram.push(ALICE_CHAT, f"/start {alice['link_code']}")

    result = tools.telegram_get_updates()

    assert any(p["name"] == "Alice" for p in result["linked"])
    assert tools.list_people(role="team_member")[0]["is_linked"] is True
