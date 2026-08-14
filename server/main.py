"""Entrypoint: registers the tools with MCPServer and starts it.

Run locally with:  python main.py
Talks over the "streamable-http" transport so it can run in its own
container/pod, reachable from Odysseus over the network, rather than being
bolted into Odysseus's own process.
"""

import os

from dotenv import load_dotenv
from mcp.server import MCPServer

import tools
from models import init_db

# Loads server/.env if present (local dev). In Kubernetes, TELEGRAM_BOT_TOKEN
# will instead come from a mounted Secret as a real environment variable, so
# this is a no-op there — os.environ already has it either way.
load_dotenv()

mcp = MCPServer(name="pm-chaser")

# The two planning tools come first deliberately: they are the entry point for
# the scheduled runs, and each replaces a chain of five-plus calls that the
# local model could not reliably complete.
mcp.add_tool(tools.get_chase_plan)
mcp.add_tool(tools.chase_now)
mcp.add_tool(tools.get_digest_data)

mcp.add_tool(tools.create_task)
mcp.add_tool(tools.list_tasks)
mcp.add_tool(tools.update_task)
mcp.add_tool(tools.reassign_task)
mcp.add_tool(tools.delete_task)
mcp.add_tool(tools.register_person)
mcp.add_tool(tools.delete_person)
mcp.add_tool(tools.list_people)
mcp.add_tool(tools.telegram_send_message)
mcp.add_tool(tools.telegram_get_updates)
mcp.add_tool(tools.resolve_unmatched)
mcp.add_tool(tools.get_assistant_name)
mcp.add_tool(tools.set_assistant_name)


if __name__ == "__main__":
    init_db()
    host = os.environ.get("PM_CHASER_HOST", "0.0.0.0")
    port = int(os.environ.get("PM_CHASER_PORT", "8000"))
    mcp.run(transport="streamable-http", host=host, port=port)
