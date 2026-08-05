"""End-to-end wire-protocol test: connects to a *running* pm-chaser-mcp
server over streamable-http (the same transport Odysseus will use) and
calls real tools through the actual MCP client library — not just calling
the Python functions directly, as test_tools.py does.

Requires the server to already be running, e.g.:
  PM_CHASER_PORT=8010 .venv/bin/python main.py

Run with:
  .venv/bin/python test_wire.py http://127.0.0.1:8010/mcp
"""

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def parse_blocks(result):
    """A tool returning a list comes back as one content block per item
    (confirmed by testing against a running server), not one JSON array —
    so always parse every block and let the caller decide list vs. single."""
    return [json.loads(block.text) for block in result.content]


async def main(url: str) -> None:
    async with streamable_http_client(url) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print("Tools advertised by the server:", names)
            expected = {
                "create_task",
                "list_tasks",
                "update_task",
                "delete_task",
                "register_person",
                "list_people",
                "telegram_send_message",
                "telegram_get_updates",
                "resolve_unmatched",
            }
            assert expected.issubset(set(names)), f"missing tools: {expected - set(names)}"

            result = await session.call_tool("register_person", {"name": "WireTestUser"})
            payload = parse_blocks(result)[0]
            print("register_person ->", payload)
            assert "link_code" in payload, "expected a link_code in the response"

            result = await session.call_tool(
                "create_task",
                {"title": "Wire protocol test task", "owner_name": "WireTestUser"},
            )
            payload = parse_blocks(result)[0]
            print("create_task ->", payload)
            assert "task_id" in payload

            result = await session.call_tool("list_tasks", {"filter": "all"})
            payload = parse_blocks(result)
            print("list_tasks ->", payload)
            assert any(t["title"] == "Wire protocol test task" for t in payload)

            print()
            print("Wire protocol test passed: a real MCP client connected, listed tools,")
            print("and called tools successfully over streamable-http.")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010/mcp"
    asyncio.run(main(url))
