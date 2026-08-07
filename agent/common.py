"""Shared helpers for the standalone agent scripts (run.py's chase/digest
jobs, pm_bot.py's manager listener). Extracted so both share one copy of the
LM Studio / kubectl port-forward / MCP / failure-notification plumbing
instead of drifting into two copies.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path

import httpx2 as httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"
MODEL = "qwen/qwen3.6-35b-a3b"
ROUND_TIMEOUT = 300
KUBECTL = "/usr/local/bin/kubectl"
PORT_FORWARD_LOCAL_PORT = 18173

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} {msg}", flush=True)


def parse_skill(name: str) -> str:
    """Extract When to Use + Procedure + Pitfalls + Verification from a
    SKILL.md. Verification is deliberately included here even though
    Odysseus never auto-injected it (brevity mattered when every extra
    prompt token cost real time under the old 27B model) — it's a free
    self-check now, and a second line of defense against the kind of
    mistake testing already caught once (splitting one escalation into
    three separate messages)."""
    text = (SKILLS_DIR / name / "SKILL.md").read_text()

    def section(heading: str) -> str:
        m = re.search(rf"## {heading}\n(.*?)(?=\n## |\Z)", text, re.S)
        return m.group(1).strip() if m else ""

    return (
        f"When to Use:\n{section('When to Use')}\n\n"
        f"Procedure:\n{section('Procedure')}\n\n"
        f"Pitfalls:\n{section('Pitfalls')}\n\n"
        f"Before your final sign-off, verify:\n{section('Verification')}"
    )


def start_port_forward() -> subprocess.Popen:
    """`kubectl port-forward` proxies through the Kubernetes API server
    (exposed to the Mac on 6443) rather than the LoadBalancer IP
    (172.19.0.x), which only routes from inside Docker Desktop's VM —
    confirmed directly (ConnectTimeout from the host). This also sidesteps
    the LoadBalancer IP drift problem entirely, since port-forward always
    targets the service by name, never a cached IP."""
    proc = subprocess.Popen(
        [KUBECTL, "port-forward", "-n", "pm-chaser", "svc/pm-chaser-mcp",
         f"{PORT_FORWARD_LOCAL_PORT}:8000"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"kubectl port-forward exited early: {proc.stderr.read()}")
        try:
            with socket.create_connection(("localhost", PORT_FORWARD_LOCAL_PORT), timeout=0.5):
                return proc
        except OSError:
            time.sleep(0.3)
    proc.terminate()
    raise RuntimeError("kubectl port-forward did not become ready in time")


def parse_tool_args(raw: str) -> dict:
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


async def call_lm_studio(client: httpx.AsyncClient, messages: list[dict], tools: list[dict]) -> dict:
    resp = await client.post(
        LM_STUDIO_URL,
        json={
            "model": MODEL,
            "messages": messages,
            "tools": tools,
            # Generous headroom: reasoning tokens count against this budget too,
            # and a synthesis-heavy round has hit 10k+ reasoning chars alone —
            # too low a cap here truncates the response before the model ever
            # reaches its actual tool call, which fails *silently* (0 tool
            # calls, empty content, no error) rather than with a clear signal.
            "max_tokens": 8000,
            "stream": False,
        },
        timeout=ROUND_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


async def notify_failure(job_name: str, error: str) -> None:
    """Best-effort Telegram alert on a real failure — every failure mode
    found so far (truncated response, dead LM Studio, orphaned MCP session)
    used to just sit in a log file nobody was watching. Never raises itself;
    a failed notification should never mask the original failure."""
    try:
        pf_proc = start_port_forward()
        try:
            mcp_url = f"http://localhost:{PORT_FORWARD_LOCAL_PORT}/mcp"
            async with streamable_http_client(mcp_url) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool("list_people", {"role": "manager"})
                    # A tool returning a list comes back as one content block
                    # per item, not one JSON array in a single block.
                    managers = [json.loads(b.text) for b in result.content]
                    if not managers:
                        log(f"[{job_name}] could not send failure notification: no manager registered")
                        return
                    manager_name = managers[0]["name"]
                    send_result = await session.call_tool("telegram_send_message", {
                        "owner_name": manager_name,
                        "text": f"pm-chaser {job_name} run failed: {error}",
                    })
                    send_text = "\n".join(b.text for b in send_result.content)
                    log(f"[{job_name}] failure notification to {manager_name} -> {send_text}")
        finally:
            pf_proc.terminate()
            try:
                pf_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pf_proc.kill()
    except Exception as exc:
        log(f"[{job_name}] failed to send failure notification: {exc}")
