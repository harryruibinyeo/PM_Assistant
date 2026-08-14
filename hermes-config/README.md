# hermes-config

The actual behavior of both bots — not just settings, but the real prompts and procedures that decide what they do — tracked here instead of only living under `~/.hermes`, which is not a git repository and has no backup or history of its own.

## How this is wired up

Each file here is the **real, live file** Hermes reads — not a copy. The corresponding path under `~/.hermes/profiles/...` is a **symlink** pointing back into this directory:

| Tracked here | Live path (symlink) |
|---|---|
| `task-manager-bot/SOUL.md` | `~/.hermes/profiles/task-manager-bot/SOUL.md` |
| `task-manager-bot/config.yaml` | `~/.hermes/profiles/task-manager-bot/config.yaml` |
| `pmchaser-bot/config.yaml` | `~/.hermes/profiles/pmchaser-bot/config.yaml` |
| `pmchaser-bot/task-chaser/SKILL.md` | `~/.hermes/profiles/pmchaser-bot/skills/pm-chaser/task-chaser/SKILL.md` |
| `pmchaser-bot/task-digest/SKILL.md` | `~/.hermes/profiles/pmchaser-bot/skills/pm-chaser/task-digest/SKILL.md` |

Editing either the file here or the symlinked path edits the *same* file — there's only one copy on disk. This means:
- Changes take effect immediately (same as before this was set up) — no sync step needed.
- `git diff`/`git log` on this directory shows the real history of what the bots have been instructed to do, same as any other code.
- If these symlinks are ever broken (e.g. a profile gets rebuilt from scratch), recreate them with `ln -s <this-dir>/<file> <the ~/.hermes path>` — see the table above for exact paths.

## What's deliberately NOT tracked here

- **`.env` files** — real Telegram bot tokens. Never commit these.
- **`logs/`, `sessions/`, `skills/.hub/`** — runtime state, not source, and would just be noise/churn in git history.
- **Bundled marketplace skills** (`xlsx`, `ocr-and-documents`, `docx`, `document-to-action-items`) — copies of external skill packages, not this project's own content. If one of them is ever customized specifically for this project, track that specific change here instead of the whole package.

## `pmchaser-bot/config.yaml` note

Mostly Hermes's own default template (commented-out fallback-model options, etc.) — the only project-specific parts are the `model`/`mcp_servers`/`memory` sections. Tracked in full anyway for completeness, since it's small and it's still the real file the bot runs on.
