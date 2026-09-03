"""pmchaser.integrations.telegram's async path (get_updates_async /
peek_for_new_replies) - previously entirely untested. Covers the Phase 2
fix: one AsyncClient reused across calls instead of a fresh one (and a
fresh TLS handshake) every time - see telegram.py's module docstring.
"""

from __future__ import annotations

import asyncio

import pytest


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Counts how many times it was constructed - the actual thing the
    Phase 2 fix reduces from "once per call" to "once per process"."""

    instances_created = 0

    def __init__(self, *args, **kwargs):
        type(self).instances_created += 1
        self.get_calls = 0

    async def get(self, url, params=None, timeout=None):
        self.get_calls += 1
        return _FakeResponse({"result": [{"update_id": 1}]})


def test_get_updates_async_reuses_one_client_across_calls(fresh_db, monkeypatch: pytest.MonkeyPatch):
    from pmchaser.integrations import telegram as telegram_integration

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    # Force a fresh default client for this test, independent of whatever
    # earlier tests in the same process may have already constructed.
    monkeypatch.setattr(telegram_integration, "_default_client", None)

    _FakeAsyncClient.instances_created = 0
    monkeypatch.setattr(telegram_integration.httpx2, "AsyncClient", _FakeAsyncClient)

    async def _run():
        await telegram_integration.get_updates_async(timeout=1)
        await telegram_integration.get_updates_async(timeout=1)
        await telegram_integration.get_updates_async(timeout=1)

    asyncio.run(_run())

    assert _FakeAsyncClient.instances_created == 1, (
        "expected exactly one AsyncClient for all 3 calls - a fresh "
        "instance per call is the exact regression this test guards"
    )


def test_peek_for_new_replies_reports_new_updates(fresh_db, monkeypatch: pytest.MonkeyPatch):
    from pmchaser.integrations import telegram as telegram_integration

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(telegram_integration, "_default_client", None)
    monkeypatch.setattr(telegram_integration.httpx2, "AsyncClient", _FakeAsyncClient)

    tools = fresh_db
    result = asyncio.run(tools.peek_for_new_replies(timeout=1))

    assert result is True
