"""Entrypoint: registers the tools with MCPServer and starts it.

Run locally with:  python main.py
Talks over the "streamable-http" transport so it can run in its own
container/pod, reachable from the agent runtime over the network, rather
than being bolted into the agent's own process.
"""

import os

from dotenv import load_dotenv
from mcp.server import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

from sqlalchemy import text

from pmchaser.db.base import engine, init_db
from pmchaser.mcp import tools
from pmchaser.mcp.auth import wrap_if_configured

# Loads server/.env if present (local dev). In Kubernetes, TELEGRAM_BOT_TOKEN
# will instead come from a mounted Secret as a real environment variable, so
# this is a no-op there - os.environ already has it either way.
load_dotenv()

mcp = MCPServer(name="pm-chaser")

# The two planning tools come first deliberately: they are the entry point for
# the scheduled runs, and each replaces a chain of five-plus calls that the
# local model could not reliably complete.
mcp.add_tool(tools.get_chase_plan)
mcp.add_tool(tools.chase_now)
mcp.add_tool(tools.get_digest_data)

mcp.add_tool(tools.create_task)
mcp.add_tool(tools.create_tasks_bulk)
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
mcp.add_tool(tools.notify_manager)


# Plain HTTP route, not an MCP tool - chase_listener.py (a host-side process,
# not the LLM) long-polls this to know the instant an employee reply arrives,
# then triggers an immediate chase run instead of waiting for the next
# 15-minute cron tick. Registered via custom_route so it rides on the same
# port/app as the MCP endpoint with no separate server to run, and it never
# shows up in the MCP tool schema every profile's prompt pays for, since
# custom_route is a distinct registration path from add_tool.
@mcp.custom_route("/internal/peek", methods=["GET"])
async def peek(request: Request) -> JSONResponse:
    timeout = int(request.query_params.get("timeout", "25"))
    try:
        new = await tools.peek_for_new_replies(timeout=timeout)
    except tools.TelegramNotConfigured as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    return JSONResponse({"new": new})


# Docker HEALTHCHECK / a future orchestrator's liveness probe: proves the
# process's own DB connection actually works, not just that the HTTP
# server is accepting connections at all. A process that's up but can't
# reach its SQLite file (bad volume mount, a permissions change) should
# read as unhealthy, not healthy.
@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> JSONResponse:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - any failure means unhealthy
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=503)
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    import uvicorn

    init_db()
    host = os.environ.get("PM_CHASER_HOST", "0.0.0.0")
    port = int(os.environ.get("PM_CHASER_PORT", "8000"))

    # Builds and runs the app by hand (what mcp.run(transport=
    # "streamable-http", ...) does internally - see MCPServer.
    # run_streamable_http_async's source) rather than using that
    # convenience method directly, so PM_CHASER_MCP_TOKEN's optional
    # bearer-auth middleware can wrap every route (both the MCP endpoint
    # and /internal/peek) before uvicorn ever sees the app. See
    # pmchaser/mcp/auth.py for why this isn't done via the mcp SDK's own
    # OAuth-oriented auth parameters instead.
    app = wrap_if_configured(
        mcp.streamable_http_app(host=host),
        os.environ.get("PM_CHASER_MCP_TOKEN"),
    )
    uvicorn.run(app, host=host, port=port)
