# Dev setup

This branch (`refactor/production-grade`) was developed and tested on
**Windows**, against real Python 3.11/3.12, real SQLite, and the real LM
Studio endpoint at `192.168.1.8:1234`. This doc covers what's specific to
that: what Windows needs that macOS doesn't, how the test suite is split
between offline and live, and what still genuinely requires the Mac.

## First-time setup (Windows or macOS)

```bash
cd server
python -m venv .venv

# Windows
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
# macOS/Linux
.venv/bin/python -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` pulls in `tzdata` (see below) unconditionally - it's
a no-op on macOS/Linux, which already ship IANA timezone data.

## Why Windows needs `tzdata` and macOS doesn't

`models.py` does `ZoneInfo(os.environ.get("PM_CHASER_TZ", "Asia/Singapore"))`
at import time. macOS and most Linux distros ship the IANA timezone
database as part of the OS; a stock Windows Python install does not, so
that line raises `ZoneInfoNotFoundError` immediately on import - the
*first* thing that happens when you try to run anything, tests included.
The `tzdata` PyPI package is Python's own copy of the same database and is
now a pinned dependency in `requirements.txt` for this reason. It was
found the hard way running the original `server/test_tools.py` on this
Windows box before any refactor work began.

## Running the tests

```bash
.venv/Scripts/python.exe -m pytest tests/ -v
```

Fully offline by default: no Telegram token, no network, no LM Studio
required. Uses an isolated temp SQLite DB per test (see `tests/conftest.py`)
and an in-memory Telegram double (`FakeTelegram`) - the same technique the
original `server/test_tools.py` used, just with real per-test isolation
instead of one shared DB across a whole linear script.

Two files are worth knowing about specifically:

- **`tests/test_tool_contract.py`** and **`tests/test_golden_master.py`** -
  these two are the actual proof that a refactor changed nothing an agent
  would notice. See their module docstrings, and the plan's "hardest
  constraint" section, before touching either.
- **`tests/test_mcp_contract.py`** - boots a real `python main.py`
  subprocess and talks to it over the real streamable-http MCP transport.
  Slower than the rest (~2s) but it's the strongest guarantee in the suite:
  it asks the actual protocol layer what an agent would be told, not just
  what `inspect.signature()` says.

## Live tests

A `live` pytest marker exists for tests that hit the real Telegram Bot API.
**Never run by default** - `pytest.ini` excludes it (`-m "not live"`), and
CI never runs it. Opt in explicitly:

```bash
TELEGRAM_BOT_TOKEN=... pytest -m live
```

**Safety constraint, non-negotiable:** a live test must run against a
**fresh, empty scratch database** (a temp `PM_CHASER_DB_PATH`, exactly like
the offline suite uses) - **never** the real `pm_chaser.db`. This is what
actually prevents a live test from messaging a real employee, regardless
of which bot token is configured: with no pre-existing person registered
in a fresh DB, nothing to send `telegram_send_message`/`chase_now` at
exists yet except whoever you personally link during that test run. If a
future live test needs to *send* something, link your own Telegram account
to the bot in that test's own setup and assert against yourself - never
assume a name from the real roster exists.

(An earlier version of this plan suggested a separate throwaway bot for
this. The project owner asked to use the real two bots instead - which is
fine under the constraint above, since the isolation comes from the
database being fresh, not from which bot token is in play.)

## What still needs the Mac

Hermes Agent itself - both profiles' live gateway, cron jobs, and the
`hermes` CLI `chase_listener.py` shells out to - only exists on the Mac
today. This laptop can run and test the MCP server, the eventual
dashboard, migrations, and (since LM Studio is reachable at
`192.168.1.8:1234`) real prompt-token and latency measurements against the
actual model. It cannot run a behavioral test through an actual Hermes
profile. See `BRANCH_TESTING.md` (added once there's something on
`refactor/agent-prompts` to test) for the Mac-side runbook.

## OneDrive warning

If your clone lives under a cloud-synced folder (this one is under
`OneDrive\Desktop\...`), **do not put a real `.env` there** -
`.gitignore` stops git from committing it, not OneDrive from uploading it.
Consider relocating the working copy outside any synced folder;
`.git/` and the SQLite `.db` file both being live-synced invites both sync
conflicts and DB corruption, independent of the secrets question.

## Dashboard secrets (Phase 5)

Once the dashboard exists, generate its two secrets locally - the
password you choose never leaves your machine:

```bash
.venv/Scripts/python.exe -m pip install bcrypt
.venv/Scripts/python.exe scripts/gen_dashboard_secrets.py
```
