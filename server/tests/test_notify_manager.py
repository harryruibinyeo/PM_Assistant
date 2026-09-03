"""notify_manager(): sends to the manager via S.A.M.'s own bot identity.

Previously entirely untested (confirmed by exploration of the original
server/test_tools.py - no reference to notify_manager anywhere in it).

Notable finding while writing this test: notify_manager instantiates its
own `telegram_client.TelegramClient(token)` directly, rather than going
through the module-level send_message()/get_updates() functions that the
`fake_telegram` fixture patches. So it needed its own fake here, patching
the class method instead. This asymmetry is itself one of the "duplicate
logic" findings (#12) in the refactor plan - two different ways of sending
a Telegram message from the same file.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def fake_manager_bot(fresh_db, monkeypatch: pytest.MonkeyPatch):
    import telegram_client

    sent: list[dict] = []

    def _fake_send(self, chat_id, text):
        sent.append({"chat_id": chat_id, "text": text})
        return {"ok": True, "result": {"message_id": 4242}}

    monkeypatch.setattr(telegram_client.TelegramClient, "send_message", _fake_send)
    monkeypatch.setenv("TASK_MANAGER_BOT_TOKEN", "test-token")
    return sent


def test_notify_manager_without_token_configured_errors(fresh_db):
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    # fresh_db explicitly unsets TASK_MANAGER_BOT_TOKEN for every test.
    result = tools.notify_manager("hello")
    assert result["sent"] is False
    assert "error" in result


def test_notify_manager_requires_exactly_one_manager(fresh_db, fake_manager_bot):
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    tools.register_person("Priya", role="manager")

    result = tools.notify_manager("hello")

    assert result["sent"] is False
    assert "error" in result


def test_notify_manager_requires_the_manager_to_be_linked(fresh_db, fake_manager_bot):
    tools = fresh_db
    tools.register_person("Bob", role="manager")  # never linked

    result = tools.notify_manager("hello")

    assert result["sent"] is False
    assert result.get("needs_linking") is True


def test_notify_manager_sends_to_the_single_registered_manager(fresh_db, fake_manager_bot, fake_telegram):
    tools = fresh_db
    manager = tools.register_person("Priya", role="manager")
    # Linking (setting telegram_chat_id) happens through the one shared
    # /start flow that telegram_get_updates() handles, regardless of which
    # bot eventually sends to that chat_id - see this file's module
    # docstring for the notify_manager/send_message split this exposes.
    fake_telegram.push("555100", f"/start {manager['link_code']}")
    tools.telegram_get_updates()

    outcome = tools.notify_manager("Daniel's task is now blocked.")

    assert outcome["sent"] is True
    assert outcome["to"] == "Priya"
    assert outcome["telegram_message_id"] == 4242
    assert fake_manager_bot[0]["text"] == "Daniel's task is now blocked."
