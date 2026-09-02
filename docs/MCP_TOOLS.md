# MCP Tool Catalog

`pm-chaser-mcp` (`server/main.py` + `server/tools.py`) exposes **16 tools** over the [Model Context Protocol](https://modelcontextprotocol.io) using the `streamable-http` transport. Both Hermes Agent profiles — `task-manager-bot` and `pmchaser-bot` — connect to the same running instance as MCP clients; neither has any direct database access of its own. Every tool signature below is the real one in `server/tools.py`.

## Why a shared tool server instead of tools baked into each bot

One source of truth for task state, chase timing, and Telegram I/O, used identically by the manager's live chat and the automated cron sweep. A rule enforced here (e.g. "an owner can't grant their own deadline extension") holds for *both* surfaces automatically, because both surfaces are calling into the exact same code path — there's no way for one bot to have a rule the other doesn't.

## Planning tools — the model's entry point for anything multi-step

These three collapse what would otherwise be a five-plus-call chain (query tasks → compute overdue windows → apply per-priority floors → group escalations → look up the manager) into one call, returning already-filtered, already-structured data. See [`ARCHITECTURE.md`](./ARCHITECTURE.md#the-chase-planning-tools-pushing-judgment-out-of-the-model-where-it-doesnt-belong) for why this exists.

| Tool | Signature | Purpose |
|---|---|---|
| `get_chase_plan` | `(due_soon_hours=24, max_unanswered=3)` | One full chase sweep's worth of gathering and filtering: who's overdue, who's due soon, whose replies need interpreting, who to escalate, who's unreachable. The scheduled `pmchaser-chase` cron job calls this exactly once per run. |
| `chase_now` | `(owner_name)` | Manual override — "chase Daniel now." Bypasses both the re-ping floor and the due-soon window; returns *every* open task for that person, not just the single most urgent one. |
| `get_digest_data` | `(at_risk_hours=24)` | Gathers everything a status digest needs — overdue work, blockers awaiting a decision, people gone quiet, unreachable owners — without deciding what's worth flagging. That judgment stays with the model. Used both for the (now on-demand-only) digest and for S.A.M.'s "how's everything looking" answers in chat. |

## Task lifecycle

| Tool | Signature | Purpose |
|---|---|---|
| `create_task` | `(title, owner_name, priority, deadline=None, description=None)` | Create one task. `priority` is required — it directly sets the re-ping floor (`high`: 1h, `medium`: 6h, `low`: 24h). |
| `create_tasks_bulk` | `(tasks: list[dict])` | Create several tasks in one call — the batch path for a spreadsheet upload, meeting-minutes action items, or any multi-row source, so partial-failure bookkeeping isn't left to the model looping `create_task` itself. |
| `list_tasks` | `(filter="all", owner_name=None, due_soon_hours=24)` | List tasks with full chase state (status, deadline, last check-in). |
| `update_task` | `(task_id, status=None, progress_pct=None, deadline=None, priority=None, ...)` | The general-purpose task editor. Extending a deadline **and** un-blocking a task happen in the same call by design, so a task can never end up with a new deadline while still marked `blocked`. |
| `reassign_task` | `(task_id, new_owner_name)` | Move a task to a different person. Deliberately narrower than `update_task(owner_name=...)` so ownership never changes as a silent side effect of an unrelated edit. |
| `delete_task` | `(task_id)` | Permanently delete a task and its check-in history. `update_task(status="cancelled")` is preferred for anything that was genuinely worked on; this is for mistakes only. |

## People & Telegram linking

| Tool | Signature | Purpose |
|---|---|---|
| `register_person` | `(name, telegram_username=None, role="team_member")` | Register a team member or manager; returns a link code they use to connect their Telegram account. |
| `list_people` | `(role=None)` | List registered people, role, link status, and open-task count. |
| `delete_person` | `(name)` | Remove someone registered by mistake. Refuses if they own *any* task, open or closed — reassign or delete those first. Gated behind an explicit human confirmation at the skill level, since it's the one irreversible, no-undo action available. |

## Telegram I/O & reply resolution

| Tool | Signature | Purpose |
|---|---|---|
| `telegram_send_message` | `(owner_name, text, task_id=None)` | Send a message to a linked person. Passing `task_id` records the check-in, which is what suppresses re-pinging them next cycle and lets their eventual reply be matched back automatically. |
| `telegram_get_updates` | `()` | Poll Telegram for new messages since the last check. Returns three buckets: newly-linked people, replies confidently matched to a task, and unmatched messages. |
| `resolve_unmatched` | `(unmatched_id, task_id=None)` | Resolve a message `telegram_get_updates` couldn't confidently match to a task. Has no messaging capability of its own — a clarifying question back to the sender is always a separate `telegram_send_message` call. |
| `notify_manager` | `(text)` | Send the manager a short note from S.A.M.'s own bot identity (a second, separate Telegram bot token) — used after a reply produces a real status change, and for escalations. Kept distinct from the employee-facing chase bot so the manager never has to parse which persona is talking. |

## The `tool_search` bridge, and why it's disabled here

Hermes Agent has a generic discovery bridge (`tool_search` → `tool_describe` → `tool_call`) meant for agents juggling far more tools than fit comfortably in one prompt. Both profiles here have small, fixed toolsets — 16 from this server plus a handful of Hermes built-ins — so the bridge is pure overhead with a real failure mode attached: it was caught, live, routing an already-directly-callable tool through the indirect path and in one case invoking the same planning tool twice in a single run when it's only ever meant to run once. Both `config.yaml`s now set `tools: { tool_search: false }`, which removes the wrong path entirely rather than relying on prompt wording to keep the model off it. Full incident writeups are in `PROJECT_MANAGEMENT.md`.
