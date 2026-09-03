"""Automates the original server/test_wire.py into pytest: boots a real
`python main.py` subprocess (streamable-http, the real transport both
Hermes profiles use) and asserts the live MCP endpoint advertises exactly
the intended 16 tools, with schemas matching the golden tool contract.

This is the strongest guarantee in the whole Phase 0 safety net: unlike
tests/test_tool_contract.py's pure `inspect.signature()` introspection,
this asks the *actual* MCP protocol layer what it would send an agent -
the real JSON schema that becomes part of the model's prompt.
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

SERVER_ROOT = Path(__file__).parent.parent

EXPECTED_TOOLS = {
    "get_chase_plan", "chase_now", "get_digest_data",
    "create_task", "create_tasks_bulk", "list_tasks", "update_task",
    "reassign_task", "delete_task", "register_person", "delete_person",
    "list_people", "telegram_send_message", "telegram_get_updates",
    "resolve_unmatched", "notify_manager",
}


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


@pytest.fixture(scope="module")
def running_server():
    """Boots the real server as a subprocess against a scratch DB, no
    Telegram token required (tool registration doesn't touch Telegram)."""
    port = _free_port()
    db_path = Path(tempfile.gettempdir()) / f"pm_chaser_mcp_contract_{uuid.uuid4().hex}.db"

    env = dict(os.environ)
    env["PM_CHASER_DB_PATH"] = str(db_path)
    env["PM_CHASER_HOST"] = "127.0.0.1"
    env["PM_CHASER_PORT"] = str(port)
    env["PM_CHASER_TZ"] = "Asia/Singapore"
    env.pop("TELEGRAM_BOT_TOKEN", None)
    env.pop("TASK_MANAGER_BOT_TOKEN", None)

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


@contextmanager
def _running_server_with_token(token: str):
    """Same as the `running_server` fixture, but with PM_CHASER_MCP_TOKEN
    set - a plain contextmanager (not a fixture) since only one test needs
    a token-armed server and every other test needs it absent, matching
    today's default deployment."""
    port = _free_port()
    db_path = Path(tempfile.gettempdir()) / f"pm_chaser_mcp_contract_{uuid.uuid4().hex}.db"

    env = dict(os.environ)
    env["PM_CHASER_DB_PATH"] = str(db_path)
    env["PM_CHASER_HOST"] = "127.0.0.1"
    env["PM_CHASER_PORT"] = str(port)
    env["PM_CHASER_TZ"] = "Asia/Singapore"
    env["PM_CHASER_MCP_TOKEN"] = token
    env.pop("TELEGRAM_BOT_TOKEN", None)
    env.pop("TASK_MANAGER_BOT_TOKEN", None)

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
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        for suffix in ("", "-journal", "-wal", "-shm"):
            p = Path(str(db_path) + suffix)
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass


def test_bearer_auth_gates_peek_but_exempts_healthz_when_token_configured():
    """PM_CHASER_MCP_TOKEN (pmchaser/mcp/auth.py) is off by default - every
    other test in this file boots with it unset and expects open access,
    matching today's real deployment. This is the one test proving the
    opt-in gate actually works when it IS configured: /internal/peek
    requires the exact bearer token, /healthz never does (Docker's own
    HEALTHCHECK sends no Authorization header at all)."""
    import urllib.error
    import urllib.request

    token = "test-bearer-token-do-not-use-in-prod"

    with _running_server_with_token(token) as base_url:
        with urllib.request.urlopen(f"{base_url}/healthz", timeout=15) as resp:
            assert resp.status == 200

        peek_url = f"{base_url}/internal/peek?timeout=1"

        try:
            urllib.request.urlopen(peek_url, timeout=15)
            raised = False
        except urllib.error.HTTPError as exc:
            raised = True
            no_token_status = exc.code
        assert raised and no_token_status == 401

        req = urllib.request.Request(peek_url, headers={"Authorization": "Bearer wrong-token"})
        try:
            urllib.request.urlopen(req, timeout=15)
            raised = False
        except urllib.error.HTTPError as exc:
            raised = True
            wrong_token_status = exc.code
        assert raised and wrong_token_status == 401

        req = urllib.request.Request(peek_url, headers={"Authorization": f"Bearer {token}"})
        try:
            urllib.request.urlopen(req, timeout=15)
            correct_token_status = 200
        except urllib.error.HTTPError as exc:
            # 503 (TelegramNotConfigured, no token in this test's env) is
            # fine here - what matters is it got PAST the auth gate, not
            # what peek_for_new_replies itself returns.
            correct_token_status = exc.code
        assert correct_token_status == 503


async def _list_tools(url: str):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()
            return result.tools


def test_live_server_advertises_exactly_the_16_expected_tools(running_server):
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


def test_healthz_is_reachable_and_ok(running_server):
    """Phase 4 addition: main.py's /healthz route (Docker HEALTHCHECK / a
    future orchestrator's liveness probe), proving its own DB connection
    actually works - not just that the HTTP server is accepting
    connections."""
    import urllib.request

    healthz_url = running_server.replace("/mcp", "/healthz")
    with urllib.request.urlopen(healthz_url, timeout=15) as resp:
        status = resp.status
        body = json.loads(resp.read())

    assert status == 200
    assert body == {"status": "ok"}
