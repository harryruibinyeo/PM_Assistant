# pm-chaser

An AI agent that tracks task assignments and deadlines, chases owners for status updates on Telegram, and reports a plain-English digest back to the manager.

It doesn't run on its own — it's a small helper service (`pm-chaser-mcp`) that plugs into an existing [Odysseus](https://github.com/odysseus-dev/odysseus) instance (running separately at `../odysseus/`). Odysseus's own AI agent and scheduler do the actual "thinking" (drafting messages, reading replies, writing the digest); this repo only adds the data storage and Telegram plumbing Odysseus doesn't have.

## Where to look

- **`PROJECT_MANAGEMENT.md`** — the full history of this project: every step taken, every decision made, and *why*, written so it's understandable without already knowing the jargon. Start here.
- **`server/`** — the `pm-chaser-mcp` service itself (data model, tools, Telegram client).
- **`skills/task-chaser/SKILL.md`** — the instructions sheet that teaches Odysseus's agent how to behave for this job.
- **`k8s/`** — the Kubernetes config for deploying `pm-chaser-mcp`.

## Status

Build in progress. See `PROJECT_MANAGEMENT.md` for the current phase.
