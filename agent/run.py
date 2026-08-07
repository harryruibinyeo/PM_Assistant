"""Standalone chase/digest runner.

Calls LM Studio and pm-chaser-mcp directly, replacing Odysseus's
ScheduledTask + agent_loop for these two unattended jobs. Odysseus keeps its
own role unchanged: the manager still creates/edits tasks by chatting with it
normally — this script only owns the chase check and the daily digest.

Why this exists (see PROJECT_MANAGEMENT.md for the full story): routing the
two unattended jobs through Odysseus's general-purpose chat-agent machinery
was the source of most operational fragility — MCP sessions orphaned on every
pod restart, a LoadBalancer IP that can drift and make "Reconnect" lie about
success, a foreground-activity gate that kills scheduled runs when the UI is
open, and no way to reach Odysseus's own forced_tools mechanism for scheduled
tasks. None of that is inherent to the task itself.

Usage:
  .venv/bin/python run.py chase
  .venv/bin/python run.py digest
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time

import httpx2 as httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from common import (
    PORT_FORWARD_LOCAL_PORT,
    call_lm_studio,
    log,
    notify_failure,
    parse_skill,
    parse_tool_args,
    start_port_forward,
)

MAX_ROUNDS = 5

# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format) — one entry per MCP tool
# these two jobs are allowed to call. Mirrors server/tools.py signatures.
# ---------------------------------------------------------------------------

TOOLS = {
    "get_chase_plan": {
        "type": "function",
        "function": {
            "name": "get_chase_plan",
            "description": (
                "One call that polls Telegram, files everything that arrived, "
                "applies every filter rule, and returns who to chase, escalate, "
                "unreachable owners, replies to interpret, and what was filtered "
                "out with reasons."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "get_digest_data": {
        "type": "function",
        "function": {
            "name": "get_digest_data",
            "description": (
                "One call returning the manager to send to, every active task "
                "with its state, anything blocked and awaiting a decision, who "
                "has stopped replying, and who never linked Telegram."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "update_task": {
        "type": "function",
        "function": {
            "name": "update_task",
            "description": (
                "Update a task's status/progress after interpreting a reply. "
                "Any argument left out is left unchanged."
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
    "telegram_send_message": {
        "type": "function",
        "function": {
            "name": "telegram_send_message",
            "description": (
                "Send a Telegram message to a registered, linked person. "
                "Pass task_id when chasing a specific task."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "owner_name": {"type": "string"},
                    "text": {"type": "string"},
                    "task_id": {"type": "integer"},
                },
                "required": ["owner_name", "text"],
            },
        },
    },
    "resolve_unmatched": {
        "type": "function",
        "function": {
            "name": "resolve_unmatched",
            "description": "Attach an unmatched message to a task, or dismiss it (omit task_id).",
            "parameters": {
                "type": "object",
                "properties": {
                    "unmatched_id": {"type": "integer"},
                    "task_id": {"type": "integer"},
                },
                "required": ["unmatched_id"],
            },
        },
    },
}

JOBS = {
    "chase": {
        "skill": "task-chaser",
        "tools": ["get_chase_plan", "update_task", "telegram_send_message", "resolve_unmatched"],
        "first_tool": "get_chase_plan",
        "trigger": (
            "get_chase_plan has already been called for you — its result is the "
            "tool message just above. Do not call it again. Working from that result:\n\n"
            "1. For each item in replies_to_interpret: decide what the message means "
            "and call update_task with the status and progress it implies.\n"
            "2. For each item in unmatched_to_resolve: work out which task it refers "
            "to and call resolve_unmatched.\n"
            "3. For each item in to_chase: write a short, specific message and send it "
            "with telegram_send_message(task_id=...).\n"
            "4. For each item in to_escalate: notify the manager.\n"
            "5. If unreachable is not empty, tell the manager who they are."
        ),
    },
    "digest": {
        "skill": "task-digest",
        "tools": ["get_digest_data", "telegram_send_message"],
        "first_tool": "get_digest_data",
        # A digest run always has something to say (even "all clear") and
        # must always end with a real telegram_send_message call — unlike
        # chase, there is no legitimate "nothing to do" outcome that skips
        # sending. Caught for real: the model wrote out the full digest text
        # plus a closing "Digest sent to Jeffrey." line with zero tool calls
        # after round 0 — narrated the send instead of performing it. The
        # model's own claim can't be trusted here; only the tool actually
        # succeeding counts.
        "requires_send": True,
        "trigger": (
            "get_digest_data has already been called for you — its result is the "
            "tool message just above. Do not call it again. Working from that result:\n\n"
            "1. Decide what matters: blocked_needing_decision first, then overdue "
            "work, anything at risk of slipping, people who stopped responding, "
            "and anyone unreachable.\n"
            "2. Write it as short readable prose and send it with "
            "telegram_send_message(manager_name, digest) — no task_id."
        ),
    },
}


async def run_job(job_name: str) -> None:
    job = JOBS[job_name]
    mcp_url = f"http://localhost:{PORT_FORWARD_LOCAL_PORT}/mcp"

    pf_proc = start_port_forward()
    log(f"[{job_name}] port-forward up, using pm-chaser-mcp at {mcp_url}")
    try:
        await _run_job_inner(job_name, job, mcp_url)
    finally:
        pf_proc.terminate()
        try:
            pf_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pf_proc.kill()


async def _run_job_inner(job_name: str, job: dict, mcp_url: str) -> None:
    system_prompt = parse_skill(job["skill"])
    tool_schemas = [TOOLS[t] for t in job["tools"]]

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": job["trigger"]},
    ]

    run_start = time.time()

    async with httpx.AsyncClient() as http_client, \
            streamable_http_client(mcp_url) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            # The opening fetch (get_chase_plan/get_digest_data) is
            # zero-argument and zero-judgment — Python calls it directly
            # instead of spending an LLM round asking the model to decide to
            # do the only thing it could possibly do. Replaces the earlier
            # prefill-trick workaround: that made the model *skip thinking*
            # about this decision, but it still nominally owned it. This
            # removes the round from the loop entirely.
            first_tool = job["first_tool"]
            round0_start = time.time()
            try:
                result = await session.call_tool(first_tool, {})
                result_text = "\n".join(b.text for b in result.content)
            except Exception as exc:
                result_text = json.dumps({"error": str(exc)})
                log(f"[{job_name}] tool {first_tool} FAILED: {exc}")
            else:
                log(f"[{job_name}] tool {first_tool}({{}}) -> {result_text[:200]}")
            log(f"[{job_name}] round 0 (direct call): {time.time() - round0_start:.1f}s")

            tool_call_id = "round0"
            messages.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": tool_call_id,
                    "type": "function",
                    "function": {"name": first_tool, "arguments": "{}"},
                }],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": result_text,
            })

            # Tracks whether any real action (a successful telegram_send_message,
            # update_task, or resolve_unmatched) has happened yet — distinct from
            # round 0's data-only fetch. Used below to tell "nothing was sent"
            # (a real failure) apart from "the wrap-up hiccupped after the real
            # work already succeeded" (not worth alarming anyone over).
            action_taken = False
            # Narrower than action_taken: specifically whether
            # telegram_send_message itself succeeded, checked below for jobs
            # where "wrote a confident final message but never actually sent"
            # must be treated as a failure regardless of finish_reason.
            send_happened = False

            for round_num in range(1, MAX_ROUNDS + 1):
                round_messages = list(messages)

                round_start = time.time()
                response = await call_lm_studio(http_client, round_messages, tool_schemas)

                choice = response["choices"][0]["message"]
                finish_reason = response["choices"][0].get("finish_reason")
                tool_calls = choice.get("tool_calls") or []
                reasoning_len = len(choice.get("reasoning_content") or "")
                round_elapsed = time.time() - round_start
                log(
                    f"[{job_name}] round {round_num}: {round_elapsed:.1f}s, "
                    f"{len(tool_calls)} tool call(s), reasoning_chars={reasoning_len}, "
                    f"finish_reason={finish_reason}"
                )

                assistant_msg = {"role": "assistant", "content": choice.get("content") or ""}
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                messages.append(assistant_msg)

                if not tool_calls:
                    final_text = (choice.get("content") or "").strip()
                    # An empty final turn is never a valid outcome — the skill
                    # always asks for at least a one-line sign-off, even on a
                    # quiet run ("nothing to chase"). Seen for real once:
                    # finish_reason="length" truncated the response mid-
                    # reasoning, before the model reached its tool call or any
                    # visible text, and the run would have looked like a clean
                    # success (0 tool calls, exit 0) despite sending nothing.
                    if not final_text:
                        if action_taken:
                            # The real work (a send, an update) already
                            # happened successfully — this round is only the
                            # closing sign-off, which is cosmetic. Log it and
                            # move on rather than raising a failure (and
                            # triggering a "run failed" alert) over something
                            # the manager already correctly received.
                            log(
                                f"[{job_name}] round {round_num} produced no sign-off "
                                f"(finish_reason={finish_reason}), but real work already "
                                "happened this run — not treating as a failure"
                            )
                            break
                        raise RuntimeError(
                            f"round {round_num} ended with no tool call and no text "
                            f"(finish_reason={finish_reason}) — likely truncated before "
                            "acting; nothing was sent"
                        )

                    if job.get("requires_send") and not send_happened:
                        # The model wrote a confident-sounding final answer —
                        # including, seen for real, a closing line claiming
                        # it was sent — without ever calling
                        # telegram_send_message. Rather than accepting that
                        # as done (the old bug) or discarding the digest it
                        # just composed, nudge it to actually send that same
                        # content and give it one more round. The final
                        # requires_send check after this loop remains as the
                        # backstop if it still won't comply.
                        log(
                            f"[{job_name}] round {round_num} produced a final answer "
                            "without ever calling telegram_send_message — nudging it "
                            "to actually send instead of accepting the narration"
                        )
                        messages.append({
                            "role": "user",
                            "content": (
                                "That was never sent — you have not called "
                                "telegram_send_message. Call it now with that same "
                                "content before finishing."
                            ),
                        })
                        continue

                    log(f"[{job_name}] done: {final_text}")
                    break

                for tc in tool_calls:
                    tool_name = tc["function"]["name"]
                    tool_args = parse_tool_args(tc["function"].get("arguments", "{}"))
                    try:
                        result = await session.call_tool(tool_name, tool_args)
                        result_text = "\n".join(b.text for b in result.content)
                    except Exception as exc:
                        result_text = json.dumps({"error": str(exc)})
                        log(f"[{job_name}] tool {tool_name} FAILED: {exc}")
                    else:
                        log(f"[{job_name}] tool {tool_name}({tool_args}) -> {result_text[:200]}")
                        action_taken = True
                        if tool_name == "telegram_send_message":
                            send_happened = True

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_text,
                    })
            else:
                log(f"[{job_name}] FAILED: hit max rounds ({MAX_ROUNDS}) without finishing")
                raise RuntimeError(f"{job_name} did not finish within {MAX_ROUNDS} rounds")

            if job.get("requires_send") and not send_happened:
                raise RuntimeError(
                    f"{job_name} ended without ever calling telegram_send_message — "
                    "the model likely narrated the send in its final text instead of "
                    "actually performing it; nothing was sent"
                )

    log(f"[{job_name}] total time: {time.time() - run_start:.1f}s")


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in JOBS:
        print("Usage: run.py [chase|digest]", file=sys.stderr)
        sys.exit(1)
    job_name = sys.argv[1]
    try:
        asyncio.run(run_job(job_name))
    except Exception as exc:
        log(f"[{job_name}] FAILED: {exc}")
        asyncio.run(notify_failure(job_name, str(exc)))
        sys.exit(1)


if __name__ == "__main__":
    main()
