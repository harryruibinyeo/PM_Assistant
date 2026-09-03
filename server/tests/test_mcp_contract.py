"""Automates the original server/test_wire.py into pytest: boots a real
`python main.py` subprocess (streamable-http, the real transport both
Hermes profiles use) and asserts the live MCP endpoint advertises exactly
the intended tools, with schemas matching the golden tool contract.

This is the strongest guarantee in the whole Phase 0 safety net: unlike
tests/test_tool_contract.py's pure `inspect.signature()` introspection,
this asks the *actual* MCP protocol layer what it would send an agent -
the real JSON schema that becomes part of the model's prompt.

Phase 3 addition: the same live-server approach, parameterized by
PM_CHASER_TOOL_PROFILE, proves each profile's server genuinely advertises
only its own tool subset (pmchaser/mcp/profiles.py) - the actual,
measurable fix for the Phase 0 finding that pmchaser-bot paid for all 16
tools' schemas despite using 5.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from pmchaser.mcp.profiles import ALL_TOOLS, PMCHASER_BOT_TOOLS, TASK_MANAGER_BOT_TOOLS

SERVER_ROOT = Path(__file__).parent.parent

# Sourced from the real profiles module (not a hand-maintained duplicate
# list) specifically because this file already got bitten once by drift
# risk here - see the Phase 3 per-profile milestone commit.
EXPECTED_TOOLS = set(ALL_TOOLS)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_accepting(host: str, port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    last_err = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError as exc:
            last_err = exc
            time.sleep(0.2)
    raise RuntimeError(f"server on {host}:{port} never started listening: {last_err}")


@contextmanager
def _running_server(tool_profile: str | None = None):
    """Boots the real server as a subprocess against a scratch DB, no
    Telegram token required (tool registration doesn't touch Telegram).
    `tool_profile=None` explicitly unsets PM_CHASER_TOOL_PROFILE (rather
    than merely not setting it), so a value leaked from the outer shell
    environment can never skew the "default, all-tools" case."""
    port = _free_port()
    db_path = Path(tempfile.gettempdir()) / f"pm_chaser_mcp_contract_{uuid.uuid4().hex}.db"

    env = dict(os.environ)
    env["PM_CHASER_DB_PATH"] = str(db_path)
    env["PM_CHASER_HOST"] = "127.0.0.1"
    env["PM_CHASER_PORT"] = str(port)
    env["PM_CHASER_TZ"] = "Asia/Singapore"
    env.pop("TELEGRAM_BOT_TOKEN", None)
    env.pop("TASK_MANAGER_BOT_TOKEN", None)
    if tool_profile is None:
        env.pop("PM_CHASER_TOOL_PROFILE", None)
    else:
        env["PM_CHASER_TOOL_PROFILE"] = tool_profile

    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(SERVER_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_until_accepting("127.0.0.1", port)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        # Best-effort cleanup only: on Windows, SQLite can hold the file
        # open briefly after the process that used it exits (no POSIX
        # unlink-while-open semantics), so a lingering temp file here is a
        # cosmetic leak in the OS temp dir, not a test-correctness issue -
        # never fail the test suite over it.
        for suffix in ("", "-journal", "-wal", "-shm"):
            p = Path(str(db_path) + suffix)
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass


@pytest.fixture(scope="module")
def running_server():
    with _running_server(tool_profile=None) as url:
        yield url


@pytest.fixture(scope="module")
def pmchaser_bot_server():
    with _running_server(tool_profile="pmchaser-bot") as url:
        yield url


@pytest.fixture(scope="module")
def task_manager_bot_server():
    with _running_server(tool_profile="task-manager-bot") as url:
        yield url


async def _list_tools(url: str):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()
            return result.tools


def test_live_server_advertises_exactly_the_expected_tools(running_server):
    tools = asyncio.run(_list_tools(running_server))
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS, (
        f"live MCP endpoint advertises {sorted(names)}, expected "
        f"{sorted(EXPECTED_TOOLS)} - a tool was added, removed, or renamed "
        f"without updating the contract."
    )


def test_live_server_tool_schemas_have_stable_parameter_names(running_server):
    """Not a full JSON-schema diff (that lives in the golden snapshot at
    the Python level) - this specifically confirms the MCP layer itself
    surfaces the same parameter names tools.py declares, since that's the
    layer that actually builds the schema the agent sees."""
    tools = {t.name: t for t in asyncio.run(_list_tools(running_server))}

    # A spot check on the two tools whose exact argument names are most
    # load-bearing in SOUL.md/SKILL.md (see the plan's "hardest constraint").
    # Note: this installed mcp SDK version (2.1.1) exposes this as the
    # snake_case `input_schema` attribute, not the wire-JSON `inputSchema`
    # key server/test_wire.py's era might suggest - confirmed by inspecting
    # mcp.types.Tool.model_fields directly.
    chase_plan_props = set(tools["get_chase_plan"].input_schema.get("properties", {}))
    assert chase_plan_props == {"due_soon_hours", "max_unanswered"}

    send_props = set(tools["telegram_send_message"].input_schema.get("properties", {}))
    assert send_props == {"owner_name", "text", "task_id"}


async def _round_trip(url: str) -> list[dict]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            await session.call_tool("register_person", {"name": "WireTestUser"})
            result = await session.call_tool(
                "create_task",
                {"title": "Wire protocol test task", "owner_name": "WireTestUser", "priority": "medium"},
            )
            payload = [json.loads(b.text) for b in result.content][0]
            assert "task_id" in payload

            listed = await session.call_tool("list_tasks", {"filter": "all"})
            return [json.loads(b.text) for b in listed.content]


def test_a_real_mcp_client_can_round_trip_create_and_list(running_server):
    """End-to-end proof the transport, not just the schema, still works -
    same assertion server/test_wire.py made manually."""
    tasks = asyncio.run(_round_trip(running_server))
    assert any(t["title"] == "Wire protocol test task" for t in tasks)


def test_pmchaser_bot_profile_server_advertises_only_its_5_tools(pmchaser_bot_server):
    """The actual, measurable fix for the Phase 0 finding: booted with
    PM_CHASER_TOOL_PROFILE=pmchaser-bot, the live server must advertise
    exactly PMCHASER_BOT_TOOLS - not 16, not some other subset. This is
    what an agent's MCP client genuinely sees; a source-level check of
    pmchaser/mcp/profiles.py alone couldn't prove main.py actually wires
    the env var through to registration."""
    tools = asyncio.run(_list_tools(pmchaser_bot_server))
    names = {t.name for t in tools}
    assert names == set(PMCHASER_BOT_TOOLS)


def test_task_manager_bot_profile_server_advertises_only_its_14_tools(task_manager_bot_server):
    tools = asyncio.run(_list_tools(task_manager_bot_server))
    names = {t.name for t in tools}
    assert names == set(TASK_MANAGER_BOT_TOOLS)


def test_pmchaser_bot_profile_server_still_serves_internal_peek():
    """The /internal/peek route (not an MCP tool - see main.py) must stay
    reachable on the pmchaser-bot profile, since chase_listener.py's
    hardcoded PEEK_URL targets pmchaser-bot's port specifically.

    This test's env has no TELEGRAM_BOT_TOKEN, so the route correctly
    responds 503 (TelegramNotConfigured) rather than 200 - what matters
    here is that the route is *registered at all* (any response other
    than 404), not what it answers without a token configured; a 404
    would mean this profile's server never registered the route in the
    first place, which is the actual regression this guards against.
    """
    import urllib.error
    import urllib.request

    with _running_server(tool_profile="pmchaser-bot") as url:
        peek_url = url.replace("/mcp", "/internal/peek") + "?timeout=1"
        try:
            with urllib.request.urlopen(peek_url, timeout=15) as resp:
                status = resp.status
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            status = exc.code
            body = json.loads(exc.read())

    assert status != 404, "expected /internal/peek to be registered on the pmchaser-bot profile"
    assert status == 503
    assert "error" in body  # TelegramNotConfigured, expected with no token in this test's env


def test_task_manager_bot_profile_server_has_no_internal_peek():
    """chase_listener.py only ever hits pmchaser-bot's port - a
    task-manager-bot-only instance has no reason to expose this route,
    and not exposing it makes it obvious if something is misconfigured to
    poll the wrong server."""
    import urllib.error
    import urllib.request

    with _running_server(tool_profile="task-manager-bot") as url:
        peek_url = url.replace("/mcp", "/internal/peek") + "?timeout=1"
        try:
            urllib.request.urlopen(peek_url, timeout=15)
            raised = False
        except urllib.error.HTTPError as exc:
            raised = True
            status = exc.code
    assert raised, "expected /internal/peek to 404 on a task-manager-bot-only instance"
    assert status == 404
