"""Thin wrapper over the two Telegram Bot API calls this project needs:
sending a message, and checking for new messages since last time.

Deliberately not using a full bot framework (e.g. python-telegram-bot) —
that's built around keeping a live connection open and listening
continuously, which is the wrong shape here. Telegram is only ever checked
when an MCP tool is called during a scheduled agent run.
"""

from __future__ import annotations

import os

import httpx2

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramNotConfigured(RuntimeError):
    pass


def _token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise TelegramNotConfigured(
            "TELEGRAM_BOT_TOKEN is not set. Create a bot via @BotFather and "
            "set the token as an environment variable before using Telegram tools."
        )
    return token


def send_message(chat_id: str, text: str) -> dict:
    """Send a plain text message to a Telegram chat. Returns Telegram's response."""
    url = f"{TELEGRAM_API_BASE}/bot{_token()}/sendMessage"
    resp = httpx2.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_updates(offset: int | None = None, timeout: int = 0) -> list[dict]:
    """Fetch new messages since `offset`. Returns Telegram's raw list of updates."""
    url = f"{TELEGRAM_API_BASE}/bot{_token()}/getUpdates"
    params: dict[str, int] = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    resp = httpx2.get(url, params=params, timeout=timeout + 10)
    resp.raise_for_status()
    return resp.json().get("result", [])
