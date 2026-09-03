# Testing this branch on the Mac

**If you are Claude (or anyone) reading this because the user asked you to
pull and test this branch: read this entire file before running anything.**
It exists specifically so you don't have to re-derive this project's
context from scratch, and so you don't accidentally do something
irreversible to a tool that's in real daily use.

## What this branch is

Two branches carry this refactor of pm-chaser's `server/` code - the MCP
tool service both Hermes bot profiles (`task-manager-bot`, `pmchaser-bot`)
talk to - and this file lives on both:

- `refactor/production-grade` - `server/` code only. Phases 0-2: the
  safety-net tests, the `tools.py`→`pmchaser/` package split, N+1/index/
  logging fixes and 5 flagged bug fixes - all behavior-identical or
  narrowly-scoped, verified by an automated golden-master snapshot that
  diffs every tool's exact output against the pre-refactor original.
  `hermes-config/` is untouched here.
- `refactor/agent-prompts` (branches off the one above) - Phase 3, the
  agent-loop latency work: per-profile tool exposure, trimmed docstrings,
  new composite tools. This phase **does** touch `hermes-config/` -
  it's the one this file's higher-risk procedure exists for.

Every commit message on both branches is a full writeup of what changed
and why (`git log` to read them). This file is the *operational*
summary: what to actually do on this machine.

## Status as of this file (check before trusting the rest of this doc)

Run this first - it tells you which procedure below applies, regardless
of which of the two branches above you were told to test:

```bash
git -C <path-to-your-pm-chaser-clone> diff main -- hermes-config/
```

- **Empty output** → only `server/` code has changed so far (you're on
  `refactor/production-grade`, or an early point on `refactor/agent-prompts`
  before its hermes-config commit). Checking out the branch has **zero
  effect on live bot behavior** by itself - only rebuilding and
  restarting the Docker container makes the server changes take effect
  at all. Use **Procedure A**.
- **Non-empty output** → `refactor/agent-prompts`' per-profile tool
  exposure (and whatever else Phase 3 has added since) has landed.
  **This is a different, higher-risk situation - use Procedure B, not A.**

If this section's instructions ever contradict what `git log` and `git
diff` actually show, trust the live repository state over this file - it
may not have been updated for the latest push. Say so to the user rather
than guessing.

## The one thing that matters most: the symlink hazard

`hermes-config/SOUL.md`, `hermes-config/*/config.yaml`, and the `SKILL.md`
files are **symlinked** into `~/.hermes/profiles/...` (see
`hermes-config/README.md`). They are not copies - there is exactly one
file on disk, and the path under `~/.hermes` and the path in this repo
are the same inode. This means:

- The instant this branch is checked out **in the working directory those
  symlinks point into** (your existing pm-chaser clone - not a fresh one),
  the *live* bot configuration changes. There is no deploy step, no
  confirmation prompt, and no distinction between "checked out for
  testing" and "checked out for real" - Hermes reads whatever is at that
  path.
- This only matters once `hermes-config/` actually differs from
  production (see Status section above). Pure `server/` changes are
  invisible to Hermes until you rebuild and restart the Docker container
  yourself.
- A `config.yaml` change specifically needs a supervised `hermes gateway
  restart` to take effect on an already-running profile - the project's
  own devlog (`PROJECT_MANAGEMENT.md`) records that a running gateway
  does **not** hot-reload it, and neither does a plain conversation
  `/reset`.

## Safety rules - non-negotiable

1. **Never** point a rebuilt container at the real `pm_chaser.db` before
   first testing against a scratch copy.
2. **Back up `pm_chaser.db`** before any testing session that touches the
   live deployment, even if you don't intend to modify it directly. A
   plain file copy is enough (or `sqlite3 pm_chaser.db ".backup
   backup.db"` if you want a copy safe to take while it's in use).
3. **Never** run a `pytest -m live` test against the real
   `TELEGRAM_BOT_TOKEN` pointed at the real database - see
   `server/docs/DEV_SETUP.md`'s "Live tests" section. Live tests must
   always use a fresh, empty scratch database; that's what actually
   prevents a test from messaging a real team member, regardless of
   which bot token is configured.
4. If you don't know the exact command the production deployment
   currently runs (docker run flags, compose file, etc.), **inspect it
   first** (`docker ps`, `docker inspect <container>`) rather than
   guessing - mirror its real env vars/mounts for the test container,
   don't reinvent them.
5. If anything here is ambiguous, or a step doesn't match what you
   actually observe on the machine, **stop and ask the user** rather than
   improvising a fix on a system in daily use.

## Procedure A: testing pure server/ changes (the common case)

Applies when `git diff main -- hermes-config/` is empty.

1. In your existing pm-chaser clone: `git fetch origin && git checkout
   refactor/production-grade` (or whatever branch/commit you were told to
   test). This is safe - it does not touch anything live yet.
2. Inspect the currently-running production container to learn its real
   configuration: `docker ps` to find it, `docker inspect <name>` to see
   its env vars (`PM_CHASER_DB_PATH`, `PM_CHASER_PORT`, etc.) and volume
   mounts.
3. Rebuild the image from the new code: `docker build -t
   pm-chaser-mcp:test ./server` (adjust the tag/context to match how the
   real image is normally built, if different).
4. Copy the real `pm_chaser.db` to a scratch path, e.g.
   `pm_chaser_test.db`.
5. Run the new image as a **separate** container on a **different port**
   than production, with `PM_CHASER_DB_PATH` pointed at the scratch copy
   from step 4 - production keeps running, completely untouched, the
   whole time. Example (adjust to match the real deployment's actual
   flags from step 2):
   ```bash
   docker run -d --name pm-chaser-mcp-test \
     -e PM_CHASER_DB_PATH=/data/pm_chaser_test.db \
     -e PM_CHASER_PORT=8001 -p 8001:8001 \
     -v <path-to-scratch-db-dir>:/data \
     pm-chaser-mcp:test
   ```
6. Verify it's healthy: `docker logs pm-chaser-mcp-test`, and if you have
   Python available, the repo's own test suite (`server/tests/`) can run
   against this checkout directly - see `server/docs/DEV_SETUP.md`.
7. Because this branch's server code is behavior-identical to production
   (proven by `server/tests/test_golden_master.py`, which snapshots every
   tool's exact output), you generally do **not** need to involve Hermes
   or real Telegram to validate this phase - a wire-level MCP client
   check (`server/tests/test_mcp_contract.py`'s approach) is enough. Only
   go further (point a real Hermes profile at it, over a scratch DB) if
   you specifically want to see it in a live conversation.
8. When satisfied, cutting production over means: stop the old
   container, start a new one from the `test` image using production's
   **real** `PM_CHASER_DB_PATH` (not the scratch copy) and its normal
   port. `hermes-config/` is untouched in this phase, so no gateway
   restart is needed for this step.
9. Clean up: `docker rm -f pm-chaser-mcp-test`, remove the scratch DB copy
   once you're done with it.

## Procedure B: once hermes-config is touched (higher risk)

Applies when `git diff main -- hermes-config/` is non-empty - as of Phase
3's per-profile tool exposure, this is real, not hypothetical: pm-chaser
now runs as **two** server instances from the same image and the same
database, each advertising only the tools that profile's own
`SOUL.md`/`SKILL.md` actually calls (see
`server/pmchaser/mcp/profiles.py`):

| Profile | `PM_CHASER_TOOL_PROFILE` | Port | config.yaml points at |
|---|---|---|---|
| `pmchaser-bot` | `pmchaser-bot` | `18173` (unchanged - `chase_listener.py`'s `PEEK_URL` is hardcoded to it) | `hermes-config/pmchaser-bot/config.yaml` |
| `task-manager-bot` | `task-manager-bot` | `18174` (new) | `hermes-config/task-manager-bot/config.yaml` |

Do Procedure A's steps 1-4 first (rebuild the image, get a scratch DB
copy ready), *then*:

1. Confirm with the user that now is an acceptable time for their live
   manager-facing bot to briefly run experimental behavior - checking
   out the branch changes it immediately, not after "deploying."
2. Start **two** containers from the rebuilt image, both pointed at the
   **same** scratch DB file from Procedure A step 4 (they share the
   Telegram-cursor and task/person tables - running them against two
   different DB files will make replies and chase state silently
   disagree between the two):
   ```bash
   docker run -d --name pm-chaser-mcp-test-pmchaser \
     -e PM_CHASER_DB_PATH=/data/pm_chaser_test.db \
     -e PM_CHASER_TOOL_PROFILE=pmchaser-bot \
     -e PM_CHASER_PORT=18173 -p 18173:18173 \
     -v <path-to-scratch-db-dir>:/data \
     pm-chaser-mcp:test

   docker run -d --name pm-chaser-mcp-test-taskmanager \
     -e PM_CHASER_DB_PATH=/data/pm_chaser_test.db \
     -e PM_CHASER_TOOL_PROFILE=task-manager-bot \
     -e PM_CHASER_PORT=18174 -p 18174:18174 \
     -v <path-to-scratch-db-dir>:/data \
     pm-chaser-mcp:test
   ```
   Adjust env vars/mounts to mirror whatever `docker inspect` showed for
   the real production container (Procedure A step 2) - `TELEGRAM_BOT_TOKEN`
   in particular, which both instances need (only `pmchaser-bot`'s
   strictly needs `TASK_MANAGER_BOT_TOKEN`, for `notify_manager` - harmless
   to give both).
3. Verify both are up and advertise the right tools before touching
   Hermes at all: `docker logs` each, and if Python is available,
   `server/tests/test_mcp_contract.py`'s approach (a real MCP client
   listing tools against each port) confirms `pmchaser-bot`'s instance
   serves exactly 5 tools and `task-manager-bot`'s serves exactly 14.
4. Check out the branch in the repo directory the symlinks point into.
   This is the moment live `SOUL.md`/`SKILL.md`/`config.yaml` changes -
   the checked-out `config.yaml`s already point at `:18173`/`:18174`
   (see the table above), matching the containers from step 2.
5. Run a supervised gateway restart (e.g. `hermes gateway restart`) - a
   bare re-invocation is refused while a gateway is already running under
   launchd; use the supervised command, not a manual restart.
6. Test via real Telegram messages to the real bots. The database is
   empty (scratch copy) so you'll need to register a person and create a
   task or two before there's anything to chase.
7. **To revert:** stop both test containers; check out the production
   branch (`main`, or whatever it actually is - confirm rather than
   assume) in that same repo directory to restore the real
   `hermes-config/` content (both `config.yaml`s point back at whatever
   the single-server production URL was) through the symlinks; run the
   gateway restart again; make sure the single production Docker
   container is running (it was never stopped by this procedure, but
   confirm).

## Where to find more context

- `git log` on this branch - every commit message is a complete writeup
  of what changed, why, and how it was verified.
- `server/tests/golden/tool_outputs.json` + `server/tests/test_golden_master.py`
  - the actual proof that a given change didn't alter tool behavior.
- `server/docs/DEV_SETUP.md` - dev environment setup, the live-test safety
  rule referenced above.
- `docs/ARCHITECTURE.md`, `docs/MCP_TOOLS.md`, `docs/AI_TOOLING.md` - how
  the system fits together (may describe the pre-refactor `tools.py`
  layout until a later phase updates them - check dates/branch before
  trusting file-path references there).
- `PROJECT_MANAGEMENT.md` - the project's own historical devlog. Not
  updated by this refactor; still the authoritative record of *why*
  things were originally built the way they were (the tool-call
  reliability crisis, the Kubernetes-to-Docker migration, etc.).
