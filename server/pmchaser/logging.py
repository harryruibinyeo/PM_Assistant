"""Structured logging for pmchaser: one line per tool call, with timing
and outcome - the Phase 2 fix for finding #14 (no logging at all in
server code, no trace when the agent misbehaves overnight).

Deliberately NOT a decorator applied to the 16 MCP tool adapter functions
in pmchaser/mcp/tools.py: those functions' exact signature is what the
MCP SDK introspects to build the tool schema the agent's prompt is built
from (see mcp/tools.py's own module docstring - this is the single
hardest constraint of the whole refactor). A generic decorator's
behavior under that introspection wasn't worth risking for a logging
feature. Instead, each adapter's one-line body calls
`log_tool_call(name, fn, *args, **kwargs)` explicitly - the real work
happens inside that call, and the adapter's own `def` line, signature,
and docstring are completely untouched by it.

Not JSON: this deployment is a single Docker container read with `docker
logs`/`journalctl`, not fed into a log aggregation pipeline - plain
greppable key=value pairs on one line are more useful here than a JSON
blob nobody's parsing programmatically (yet; revisit if that changes).
"""

from __future__ import annotations

import logging
import os
import time
import uuid

logger = logging.getLogger("pmchaser")

_configured = False


def _configure_once() -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s pmchaser %(message)s",
    ))
    logger.addHandler(handler)
    logger.setLevel(os.environ.get("PM_CHASER_LOG_LEVEL", "INFO").upper())
    _configured = True


def log_tool_call(tool_name: str, fn, *args, **kwargs):
    """Runs `fn(*args, **kwargs)`, logging one line with the tool name,
    a short call_id (to correlate multi-line output, e.g. a traceback,
    back to one call), duration, and outcome:

    - "ok": returned normally, and if the result is a dict, it has no
      "error" key.
    - "tool_error": returned normally but the result dict itself carries
      an "error" key (e.g. "No task with id 5") - an expected, handled
      failure, not a bug, but worth being able to grep for the pattern
      of a specific tool erroring a lot.
    - "exception": an unhandled exception. Logged with the traceback
      (logger.exception) and then re-raised - this never swallows a
      real failure, only observes it on the way past.
    """
    _configure_once()
    call_id = uuid.uuid4().hex[:8]
    start = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
    except Exception:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.exception(
            "tool_call call_id=%s tool=%s duration_ms=%.1f outcome=exception",
            call_id, tool_name, duration_ms,
        )
        raise
    duration_ms = (time.perf_counter() - start) * 1000
    outcome = "tool_error" if isinstance(result, dict) and "error" in result else "ok"
    logger.info(
        "tool_call call_id=%s tool=%s duration_ms=%.1f outcome=%s",
        call_id, tool_name, duration_ms, outcome,
    )
    return result
