"""Standalone manager-facing Telegram listener.

The manager's (Jeffrey's) replacement for chatting with Odysseus to
create/edit tasks — a second, dedicated Telegram bot he talks to directly,
with real multi-turn conversation memory, unlike agent/run.py's chase/digest
jobs which fire once, act, and exit.

Two things are deliberately different from run.py here:

1. Telegram send/receive for this bot never goes through pm-chaser-mcp (the
   pod). It talks to Telegram directly using its own TelegramClient instance
   and its own token (PM_BOT_TELEGRAM_TOKEN). Only the actual task tools
   (create_task, list_tasks, update_task, register_person, list_people) go
   through MCP. This means listening for new messages has zero dependency
   on Kubernetes/kubectl being healthy — only *acting* on one does, and a
   failure there is surfaced in-channel rather than just going silent.

2. This process runs continuously (long-polling via Telegram's native
   `timeout` param on getUpdates), not a single-shot run triggered by
   launchd's StartInterval/StartCalendarInterval. It's a launchd KeepAlive
   service instead.

No /start-with-code linking flow is needed: in Telegram, a private chat's
chat.id is the user's own numeric Telegram ID, not bot-scoped, so Jeffrey's
already-known chat_id (captured on the employee-facing bot) is the same
here — confirmed for real before this script was written. It's supplied via
MANAGER_CHAT_ID in agent/.env rather than resolved through list_people(),
which deliberately never exposes the raw telegram_chat_id.

Usage:
  .venv/bin/python pm_bot.py
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx2 as httpx
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from common import (
    PORT_FORWARD_LOCAL_PORT,
    REPO_ROOT,
    call_lm_studio,
    log,
    notify_failure,
    parse_skill,
    parse_tool_args,
    start_port_forward,
)

sys.path.insert(0, str(REPO_ROOT / "server"))
from telegram_client import TelegramClient  # noqa: E402

POLL_TIMEOUT = 30
INACTIVITY_RESET_MINUTES = 30
MAX_TURNS_KEPT = 8
MAX_ROUNDS = 5
MANAGER_BOT_TOKEN_ENV = "PM_BOT_TELEGRAM_TOKEN"
MANAGER_CHAT_ID_ENV = "MANAGER_CHAT_ID"
LOCAL_TZ = ZoneInfo(os.environ.get("PM_CHASER_TZ", "Asia/Singapore"))

# ---------------------------------------------------------------------------
# Tool schemas — the six task/people-management tools this bot may call.
# Mirrors server/tools.py signatures. Deliberately excludes delete_task:
# this project has already found real judgment mistakes from the current
# fast model under ambiguity, and pairing that with an irreversible, no-
# confirmation delete inside a freeform chat surface is the wrong risk
# combination. update_task(status="cancelled") is the reversible equivalent.
# delete_person exists (server/tools.py already refuses it for anyone who
# owns a task) but the skill requires an explicit human "yes" before the
# model may call it — the tool layer enforces what's *safe*, the skill
# enforces that a human actually agreed.
# ---------------------------------------------------------------------------

TOOLS = {
    "create_task": {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": (
                "Create a new task assigned to a person who has already "
                "been registered (case-insensitive name match)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "owner_name": {"type": "string"},
                    "deadline": {
                        "type": "string",
                        "description": (
                            "ISO 8601 datetime, naive = local time, e.g. "
                            "'2026-08-14T17:00:00' means 5pm local."
                        ),
                    },
                    "priority": {"type": "string", "enum": ["low", "normal", "high"]},
                    "description": {"type": "string"},
                },
                "required": ["title", "owner_name"],
            },
        },
    },
    "list_tasks": {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List tasks with their full chase state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "enum": ["all", "active", "overdue", "due_soon"],
                    },
                    "owner_name": {"type": "string"},
                    "due_soon_hours": {"type": "integer"},
                },
            },
        },
    },
    "update_task": {
        "type": "function",
        "function": {
            "name": "update_task",
            "description": (
                "Update a task's fields. Any argument left out is left "
                "unchanged. Use status='cancelled' instead of deleting."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "status": {
                        "type": "string",
                        "enum": ["not_started", "in_progress", "blocked", "done", "cancelled"],
                    },
                    "progress_pct": {"type": "integer"},
                    "deadline": {"type": "string"},
                    "priority": {"type": "string", "enum": ["low", "normal", "high"]},
                    "title": {"type": "string"},
                    "owner_name": {"type": "string"},
                },
                "required": ["task_id"],
            },
        },
    },
    "register_person": {
        "type": "function",
        "function": {
            "name": "register_person",
            "description": (
                "Register a new team member and get their Telegram linking "
                "code/link."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "telegram_username": {"type": "string"},
                    "role": {"type": "string", "enum": ["team_member", "manager"]},
                },
                "required": ["name"],
            },
        },
    },
    "delete_person": {
        "type": "function",
        "function": {
            "name": "delete_person",
            "description": (
                "Permanently remove a person registered by mistake. Refuses "
                "if they own any task. ALWAYS get an explicit yes from the "
                "manager before calling this — never call it on the first ask."
            ),
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    "list_people": {
        "type": "function",
        "function": {
            "name": "list_people",
            "description": "List registered people, their role, and whether they've linked Telegram.",
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": ["team_member", "manager"]},
                },
            },
        },
    },
}


class PortForwardManager:
    """Keeps one kubectl port-forward subprocess alive for the process's
    whole lifetime, restarting it if it dies. Unlike run.py's per-run
    start/stop, this process runs for days, so a dead tunnel has to be
    noticed and recovered rather than just torn down at the end."""

    def __init__(self):
        self.proc = start_port_forward()

    def ensure_alive(self) -> None:
        if self.proc.poll() is not None:
            log("[pmbot] port-forward died, restarting")
            self.proc = start_port_forward()

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


class ConversationState:
    """Turn-based, in-process only — lost on crash/restart, which is fine
    since losing an in-flight exchange just means the manager re-sends it."""

    def __init__(self):
        self.turns: list[list[dict]] = []
        self.last_activity = datetime.now()

    def flat_messages(self) -> list[dict]:
        out: list[dict] = []
        for turn in self.turns:
            out.extend(turn)
        return out

    def add_turn(self, turn_messages: list[dict]) -> None:
        self.turns.append(turn_messages)
        # Trim whole turns from the front only — never split a `tool`
        # message from its preceding `assistant` tool_call, which would
        # break the next LM Studio call.
        if len(self.turns) > MAX_TURNS_KEPT:
            self.turns = self.turns[-MAX_TURNS_KEPT:]
        self.last_activity = datetime.now()


_conversations: dict[str, ConversationState] = {}


def _get_conversation(chat_id: str) -> ConversationState:
    conv = _conversations.get(chat_id)
    now = datetime.now()
    if conv is not None and (now - conv.last_activity) > timedelta(minutes=INACTIVITY_RESET_MINUTES):
        log(f"[pmbot] conversation with {chat_id} reset after {INACTIVITY_RESET_MINUTES}min inactivity")
        conv = None
    if conv is None:
        conv = ConversationState()
        _conversations[chat_id] = conv
    return conv


async def _converse(
    http_client: httpx.AsyncClient,
    session: ClientSession,
    system_prompt: str,
    history: list[dict],
    user_text: str,
    tool_schemas: list[dict],
) -> tuple[str, list[dict]]:
    """Runs the round-based tool-calling loop for one incoming message.
    Unlike run.py's jobs there is no first_tool/"round 0" direct call —
    every message is a real judgment call, not a mechanical opening fetch.
    Returns (final_text, turn_log) where turn_log is just this turn's own
    messages (starting with the user's), ready to hand to
    ConversationState.add_turn."""
    turn_log: list[dict] = [{"role": "user", "content": user_text}]
    messages = [{"role": "system", "content": system_prompt}] + history + list(turn_log)

    for round_num in range(1, MAX_ROUNDS + 1):
        response = await call_lm_studio(http_client, messages, tool_schemas)

        choice = response["choices"][0]["message"]
        finish_reason = response["choices"][0].get("finish_reason")
        tool_calls = choice.get("tool_calls") or []
        reasoning_len = len(choice.get("reasoning_content") or "")
        log(
            f"[pmbot] round {round_num}: {len(tool_calls)} tool call(s), "
            f"reasoning_chars={reasoning_len}, finish_reason={finish_reason}"
        )

        assistant_msg = {"role": "assistant", "content": choice.get("content") or ""}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)
        turn_log.append(assistant_msg)

        if not tool_calls:
            final_text = (choice.get("content") or "").strip() or "Done."
            log(f"[pmbot] reply: {final_text}")
            return final_text, turn_log

        for tc in tool_calls:
            tool_name = tc["function"]["name"]
            tool_args = parse_tool_args(tc["function"].get("arguments", "{}"))
            try:
                result = await session.call_tool(tool_name, tool_args)
                result_text = "\n".join(b.text for b in result.content)
            except Exception as exc:
                result_text = json.dumps({"error": str(exc)})
                log(f"[pmbot] tool {tool_name} FAILED: {exc}")
            else:
                log(f"[pmbot] tool {tool_name}({tool_args}) -> {result_text[:200]}")
            tool_msg = {"role": "tool", "tool_call_id": tc["id"], "content": result_text}
            messages.append(tool_msg)
            turn_log.append(tool_msg)

    raise RuntimeError(f"conversation did not conclude within {MAX_ROUNDS} rounds")


async def handle_message(
    http_client: httpx.AsyncClient,
    pf_mgr: PortForwardManager,
    mcp_url: str,
    system_prompt: str,
    tool_schemas: list[dict],
    manager_bot: TelegramClient,
    chat_id: str,
    text: str,
) -> None:
    conv = _get_conversation(chat_id)
    history = conv.flat_messages()

    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            async with streamable_http_client(mcp_url) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    final_text, turn_log = await _converse(
                        http_client, session, system_prompt, history, text, tool_schemas
                    )
            manager_bot.send_message(chat_id, final_text)
            conv.add_turn(turn_log)
            return
        except Exception as exc:
            last_exc = exc
            log(f"[pmbot] attempt {attempt} failed: {exc}")
            if attempt == 1:
                # A dead port-forward is the one failure worth actively
                # recovering from before retrying; anything else (a slow
                # pod, a bad tool call) gets one plain retry.
                pf_mgr.ensure_alive()
                await asyncio.sleep(1)

    manager_bot.send_message(
        chat_id, "Having trouble reaching the task system right now — try again in a bit."
    )
    raise RuntimeError(f"handle_message failed after retry: {last_exc}")


async def run() -> None:
    load_dotenv(REPO_ROOT / "agent" / ".env")

    token = os.environ.get(MANAGER_BOT_TOKEN_ENV)
    if not token:
        raise RuntimeError(f"{MANAGER_BOT_TOKEN_ENV} is not set in agent/.env")
    managers_chat_id = os.environ.get(MANAGER_CHAT_ID_ENV)
    if not managers_chat_id:
        raise RuntimeError(f"{MANAGER_CHAT_ID_ENV} is not set in agent/.env")

    manager_bot = TelegramClient(token=token)
    mcp_url = f"http://localhost:{PORT_FORWARD_LOCAL_PORT}/mcp"
    pf_mgr = PortForwardManager()
    log(f"[pmbot] port-forward up, using pm-chaser-mcp at {mcp_url}")

    base_system_prompt = parse_skill("task-manager")
    tool_schemas = [
        TOOLS[t] for t in (
            "create_task", "list_tasks", "update_task",
            "register_person", "delete_person", "list_people",
        )
    ]

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)

    offset: int | None = None
    consecutive_failures = 0

    log(f"[pmbot] listening for messages from chat_id={managers_chat_id}")
    try:
        async with httpx.AsyncClient() as http_client:
            while not shutdown.is_set():
                pf_mgr.ensure_alive()
                try:
                    updates = await asyncio.to_thread(manager_bot.get_updates, offset, POLL_TIMEOUT)
                except Exception as exc:
                    log(f"[pmbot] get_updates failed: {exc}")
                    await asyncio.sleep(5)
                    continue

                for update in updates:
                    offset = max(offset or 0, update["update_id"] + 1)
                    message = update.get("message") or {}
                    chat_id = str((message.get("chat") or {}).get("id") or "")
                    text = (message.get("text") or "").strip()
                    if not text:
                        continue
                    if chat_id != managers_chat_id:
                        log(f"[pmbot] ignoring message from unrecognized chat_id={chat_id}")
                        continue

                    log(f"[pmbot] received: {text}")
                    system_prompt = (
                        f"{base_system_prompt}\n\nCurrent date/time: "
                        f"{datetime.now(LOCAL_TZ).strftime('%A, %Y-%m-%d %H:%M %Z')}"
                    )
                    try:
                        await handle_message(
                            http_client, pf_mgr, mcp_url, system_prompt, tool_schemas,
                            manager_bot, chat_id, text,
                        )
                        consecutive_failures = 0
                    except Exception:
                        consecutive_failures += 1
                        if consecutive_failures == 3:
                            await notify_failure("pmbot", "3 consecutive message-handling failures")
                            consecutive_failures = 0
    finally:
        pf_mgr.stop()
        log("[pmbot] shut down")


def main() -> None:
    try:
        asyncio.run(run())
    except Exception as exc:
        log(f"[pmbot] FATAL at startup: {exc}")
        # Via the EXISTING employee bot, not the new one — alerting through
        # the new bot about the new bot's own failure to start would be
        # circular, and Jeffrey already reliably receives messages on the
        # employee bot today (digests/escalations).
        asyncio.run(notify_failure("pmbot", f"failed to start: {exc}"))
        sys.exit(1)


if __name__ == "__main__":
    main()
