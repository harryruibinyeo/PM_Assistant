# pm-chaser

An AI agent that tracks task assignments and deadlines, chases owners for status updates on Telegram, understands their free-text replies, and reports back to the manager — plus, as of 2026-08-14, a fuller personal-assistant surface: bulk task intake from files, meeting-minutes extraction, and a chosen conversational persona.

It runs on **Hermes Agent** (`github.com/NousResearch/hermes-agent`), across two isolated profiles:

- **`task-manager-bot`** — the manager's own Telegram conversation. Create/chase/review tasks, on-demand chase, digest, meeting minutes, bulk file upload.
- **`pmchaser-bot`** — no live gateway of its own; runs the scheduled chase (every 15 min) and daily digest as Hermes cron jobs.

Both talk to this repo's own service, **`pm-chaser-mcp`**, over MCP — a plain Docker container (not Kubernetes; migrated off it 2026-08-14) holding the actual task/person data (SQLite) and the Telegram plumbing. All reasoning happens locally via LM Studio (`qwen/qwen3.6-35b-a3b`) — no cloud AI API is used anywhere in this project.

## Where to look

- **`PROJECT_MANAGEMENT.md`** — the full history of this project: every step taken, every decision made, and *why*, written so it's understandable without already knowing the jargon, plus the current architecture diagram and stack. Start here.
- **`server/`** — the `pm-chaser-mcp` service itself (data model, tools, Telegram client).
- **`hermes-config/`** — the actual bot behavior: `task-manager-bot/SOUL.md` + `config.yaml`, and `pmchaser-bot`'s `task-chaser`/`task-digest` `SKILL.md` files. These are the *real*, tracked copies — the live files under `~/.hermes/profiles/...` are symlinks pointing here, so editing either one edits the same file. See `hermes-config/README.md` for exactly how that's wired up.
- **`k8s/`** — retained for reference/rollback only. `pm-chaser-mcp` no longer runs on Kubernetes as of 2026-08-14; this describes the old deployment.

## Status

Live and in active use by one manager and their team. See `PROJECT_MANAGEMENT.md` for the full current state, what's built, and what's still open.
