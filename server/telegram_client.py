"""Thin wrapper over the two Telegram Bot API calls this project needs:
sending a message, and checking for new messages since last time.

Deliberately not using a full bot framework (e.g. python-telegram-bot) —
that's built around keeping a live connection open and listening
continuously, which was the wrong shape for the original chase/digest jobs
(single-shot, only check Telegram when an MCP tool is called during a
scheduled run). `agent/pm_bot.py` is the one caller that *does* poll
continuously, via its own TelegramClient instance below with a second bot
token — see its module docstring for why.
"""

from __future__ import annotations

import os

import httpx2

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramNotConfigured(RuntimeError):
    pass


class TelegramClient:
    """One bot's worth of Telegram API access. `server/tools.py` uses the
    module-level singleton below (bound to TELEGRAM_BOT_TOKEN, the
    employee-facing bot); `agent/pm_bot.py` instantiates a second one
    directly with its own token, entirely independent — no shared state
    between the two bots' polling or sending."""

    def __init__(self, token: str):
        self._token = token
        self._bot_username: str | None = None

    def get_bot_username(self) -> str | None:
        """The bot's @username, fetched from Telegram once and cached.

        Read from the API rather than configured, so it can never drift out
        of sync with the token actually in use.
        """
        if self._bot_username is not None:
            return self._bot_username
        try:
            resp = httpx2.get(f"{TELEGRAM_API_BASE}/bot{self._token}/getMe", timeout=10)
            resp.raise_for_status()
            self._bot_username = (resp.json().get("result") or {}).get("username")
        except Exception:
            return None
        return self._bot_username

    def build_link_url(self, code: str) -> str | None:
        """A one-tap deep link that opens the bot and sends `/start <code>`.

        Telegram forbids a bot from messaging anyone who hasn't messaged it
        first, so someone must always make first contact. This at least
        reduces that to tapping a link instead of typing a code into the
        right chat.
        """
        username = self.get_bot_username()
        if not username or not code:
            return None
        return f"https://t.me/{username}?start={code}"

    def send_message(self, chat_id: str, text: str) -> dict:
        """Send a plain text message to a Telegram chat. Returns Telegram's response."""
        url = f"{TELEGRAM_API_BASE}/bot{self._token}/sendMessage"
        resp = httpx2.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_updates(self, offset: int | None = None, timeout: int = 0) -> list[dict]:
        """Fetch new messages since `offset`. Returns Telegram's raw list of updates."""
        url = f"{TELEGRAM_API_BASE}/bot{self._token}/getUpdates"
        params: dict[str, int] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        resp = httpx2.get(url, params=params, timeout=timeout + 10)
        resp.raise_for_status()
        return resp.json().get("result", [])


def _token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise TelegramNotConfigured(
            "TELEGRAM_BOT_TOKEN is not set. Create a bot via @BotFather and "
            "set the token as an environment variable before using Telegram tools."
        )
    return token


_default_client: TelegramClient | None = None


def _default() -> TelegramClient:
    # Lazy, not module-load-time: _token() must only be evaluated once the
    # caller actually needs it, matching the original module's behavior of
    # never requiring TELEGRAM_BOT_TOKEN to be set just to import this file.
    global _default_client
    if _default_client is None:
        _default_client = TelegramClient(_token())
    return _default_client


def get_bot_username() -> str | None:
    # Tolerates a missing token (unlike send_message/get_updates below,
    # which raise immediately) — matches the original module's behavior,
    # relied on by build_link_url degrading to None instead of crashing
    # when no bot is configured (e.g. test_tools.py's local, no-token runs).
    try:
        return _default().get_bot_username()
    except TelegramNotConfigured:
        return None


def build_link_url(code: str) -> str | None:
    # Goes through the module-level get_bot_username() above (not
    # _default().build_link_url) specifically so the missing-token
    # tolerance applies here too.
    username = get_bot_username()
    if not username or not code:
        return None
    return f"https://t.me/{username}?start={code}"


def send_message(chat_id: str, text: str) -> dict:
    return _default().send_message(chat_id, text)


def get_updates(offset: int | None = None, timeout: int = 0) -> list[dict]:
    return _default().get_updates(offset=offset, timeout=timeout)
