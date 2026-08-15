#!/usr/bin/env python3
"""Wakes up the pmchaser-chase cron job the instant an employee replies,
instead of leaving it to sit unprocessed for up to 15 minutes.

Runs on the host, not in Docker — deliberately not a live Telegram gateway
(the manager explicitly did not want employees getting access to a
conversational agent), and deliberately not inside pm-chaser-mcp's own
container, since it needs to shell out to the `hermes` CLI, which isn't
installed there.

The loop:
  1. Long-poll pm-chaser-mcp's /internal/peek route (a plain HTTP route, not
     an MCP tool — see server/main.py) for up to PEEK_TIMEOUT_SECONDS. This
     is read-only: it never advances BotState.last_update_id, so it can
     never cause a reply to be skipped by the job that actually processes it.
  2. On {"new": true}, run `hermes -p pmchaser-bot cron run <job-id>` as a
     blocking subprocess call. Confirmed (by reading Hermes's own
     tools/cronjob_tools.py) that a bare CLI invocation with no live
     gateway/dispatch context to hand off to runs the job inline and
     synchronously in this process, right now — not "sometime on the next
     60-second ticker tick." Blocking on this call is what naturally
     prevents this loop's own next peek from overlapping with the job's own
     poll of the same bot token; Telegram allows only one in-flight
     getUpdates long-poll per token, and this project has hit that conflict
     for real before.
  3. Loop back to step 1.

The 15-minute scheduled chase and the daily digest are both untouched by
this script and keep running as the safety net if this process is ever down.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PEEK_URL = "http://localhost:18173/internal/peek"
PEEK_TIMEOUT_SECONDS = 25
# Client-side HTTP timeout must exceed the server-side long-poll timeout, or
# the client gives up before the server would have responded anyway.
HTTP_TIMEOUT_SECONDS = PEEK_TIMEOUT_SECONDS + 10

CRON_JOB_NAME = "pmchaser-chase"
CRON_JOBS_FILE = Path.home() / ".hermes/profiles/pmchaser-bot/cron/jobs.json"

MAX_BACKOFF_SECONDS = 60


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def resolve_job_id() -> str:
    """Look up pmchaser-chase's job ID by name rather than hardcoding it, in
    case the job is ever deleted and recreated (which gives it a new ID)."""
    data = json.loads(CRON_JOBS_FILE.read_text())
    for job in data.get("jobs", []):
        if job.get("name") == CRON_JOB_NAME:
            return job["id"]
    raise RuntimeError(
        f"No cron job named '{CRON_JOB_NAME}' found in {CRON_JOBS_FILE} — "
        "has it been renamed or deleted?"
    )


def peek() -> bool:
    """True if a new employee message has arrived. Long-polls up to
    PEEK_TIMEOUT_SECONDS; never mutates any state."""
    url = f"{PEEK_URL}?timeout={PEEK_TIMEOUT_SECONDS}"
    with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_SECONDS) as resp:
        body = json.loads(resp.read())
    if "error" in body:
        raise RuntimeError(f"peek endpoint returned an error: {body['error']}")
    return bool(body.get("new"))


def trigger_chase(job_id: str) -> None:
    """Run the existing chase job right now, blocking until it's actually
    done — confirmed empirically (not just from reading the source): a
    successful `hermes cron run` reports "Ran now: succeeded" and updates
    the job's last_run_at only once the real execution has finished, not on
    mere dispatch. --accept-hooks is not optional: without it, an unseen
    shell hook blocks on an approval prompt that never arrives (no TTY here)
    and the process hangs forever — hit this for real while building this
    script. A run killed while genuinely hung leaves a stale fire_claim that
    blocks every subsequent trigger for up to 5 minutes (claim_ttl_seconds
    in Hermes's own cron/jobs.py) — also hit this for real; if triggers ever
    start silently no-op'ing ("already being fired"), that's what's
    happening, and it self-clears, it doesn't need manual intervention.
    """
    log(f"new reply detected — triggering {CRON_JOB_NAME} ({job_id})")
    result = subprocess.run(
        ["hermes", "-p", "pmchaser-bot", "cron", "run", job_id, "--accept-hooks"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        log(
            f"cron run exited {result.returncode} — stderr: "
            f"{result.stderr.strip()[:500]}"
        )
    elif "already being fired" in result.stdout:
        log("skipped — job already in flight (stale or concurrent claim)")
    else:
        log(f"cron run finished — {result.stdout.strip().splitlines()[-1] if result.stdout.strip() else 'ok'}")


def main() -> None:
    job_id = resolve_job_id()
    log(f"chase_listener starting — watching for {CRON_JOB_NAME} ({job_id})")

    backoff = 1
    while True:
        try:
            if peek():
                trigger_chase(job_id)
            backoff = 1
        except (urllib.error.URLError, TimeoutError, RuntimeError, OSError) as exc:
            log(f"peek/trigger failed ({exc!r}) — retrying in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
