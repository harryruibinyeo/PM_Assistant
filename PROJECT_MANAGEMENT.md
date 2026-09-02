# pm-chaser — Project Management Documentation

This file is the running history of this project: what was done, in what order, and *why* — including the setup work that happened before this repo existed. New entries get added as the build progresses. Anything technical is explained inline so this reads clearly without already knowing the jargon.

**Read this note before trusting the "Architecture and tech stack" section below at face value in the future**: it describes the system *as of 2026-08-14*. The dated entries further down are the permanent historical record — including entries about Odysseus and Kubernetes, both since replaced — kept exactly as written even after being superseded, because they explain the reasoning trail that led here. If this section and a dated entry ever disagree, the **most recent dated entry is correct**; update this section to match rather than trusting it blindly.

---

# Architecture and tech stack

## What this is

An AI agent that tracks who owes what by when, chases people on Telegram for status updates, understands their free-text replies, and reports to the manager — plus, as of 2026-08-14, a fuller personal-assistant surface for the manager: bulk task intake from files, meeting-minutes extraction, and a chosen conversational persona. Everything runs locally — no cloud AI service is involved.

## How the pieces fit together (current, 2026-08-14)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ macOS host  (Apple Silicon, 48 GB unified memory)                            │
│                                                                                │
│   ┌───────────────────────────────┐                                          │
│   │ LM Studio  (native, Metal)     │   All reasoning happens here.           │
│   │ qwen/qwen3.6-35b-a3b (MoE)     │   Runs OUTSIDE Docker — containers      │
│   └───────────────▲─────────────────┘   on macOS cannot reach the GPU.        │
│                   │ HTTP · localhost:1234/v1                                 │
│        ┌──────────┴───────────────────────────────────┐                     │
│        │                                                 │                    │
│  ┌─────┴───────────────────────┐        ┌───────────────┴──────────────────┐ │
│  │ Hermes Agent                 │        │ Hermes Agent                     │ │
│  │ profile: task-manager-bot    │        │ profile: pmchaser-bot            │ │
│  │ (launchd-supervised gateway) │        │ (launchd-supervised, cron-only)  │ │
│  │                               │        │                                   │ │
│  │ Live Telegram conversation,   │        │ No live gateway of its own —     │ │
│  │ own bot + token:              │        │ two Hermes cron jobs:            │ │
│  │  @taskmanager_...bot          │        │  task-chaser  — every 15 min     │ │
│  │                               │        │  task-digest  — daily 09:00      │ │
│  │ Talks ONLY to Marcus         │        │                                   │ │
│  │ ("boss"), 1:1. Persona: Toby  │        │ Sends via pm-chaser-mcp's OWN    │ │
│  │ or Abby, manager's choice,    │        │ chase-bot token — not its own    │ │
│  │ persisted in BotState.        │        │ gateway/token at all.            │ │
│  └───────────────┬───────────────┘        └────────────────┬──────────────────┘ │
│                  │              MCP · streamable-http         │                  │
│                  └───────────────────────┬─────────────────────┘                 │
│                                          ▼                                       │
│                          ┌────────────────────────────────────┐                 │
│                          │ pm-chaser-mcp                        │                │
│                          │ Plain Docker container                │               │
│                          │ (--restart=always), port 18173→8000   │               │
│                          │  ~20 MCP tools (task CRUD, chase       │               │
│                          │   planning, Telegram I/O, persona)     │               │
│                          │  SQLite on a named Docker volume       │               │
│                          │  Holds BOTH bot tokens:                │               │
│                          │   TELEGRAM_BOT_TOKEN (chase bot)       │               │
│                          │   TASK_MANAGER_BOT_TOKEN (rename-only) │               │
│                          └──────────────────┬─────────────────────┘               │
└─────────────────────────────────────────────┼─────────────────────────────────────┘
                                              │ HTTPS
                                              ▼
                                  ┌───────────────────────┐
                                  │   Telegram Bot API     │
                                  └───────────┬─────────────┘
                             ┌────────────────┴─────────────────┐
                             ▼                                    ▼
                    @taskmanager_...bot                   @pmchaser_...bot
                             │                                    │
                             ▼                                    ▼
                      Marcus (manager)                Daniel, Priya, ... (employees)
                      creates/chases/reviews,           get chased, replies interpreted,
                      talks to "Toby"/"Abby"             extensions routed back to manager
```

Two connection details that replaced the old Odysseus/Kubernetes ones (see the historical entries below for the full story of what came before):

- **Two separate Telegram bots, not one.** `@taskmanager_...bot` is manager-only, restricted by `TELEGRAM_ALLOWED_USERS`; `@pmchaser_...bot` is the original employee-facing chase bot, used both by the scheduled cron and by `task-manager-bot`'s own `chase_now`/`telegram_send_message` calls. They share nothing except both being registered with `pm-chaser-mcp`.
- **`pm-chaser-mcp` is a plain Docker container, not a Kubernetes Service** — migrated off Kubernetes 2026-08-14 (see that day's entry). Both Hermes profiles reach it at a fixed local port (`18173`), no LoadBalancer IP, no drift.

## The stack, and why each piece is there

| Layer | Choice | Why this one |
|---|---|---|
| Reasoning | **`qwen/qwen3.6-35b-a3b`** (MoE, ~3B active params) via **LM Studio** | Replaced the original dense 27B model 2026-08-07 — same rough memory footprint, ~5x faster, since Apple Silicon inference is memory-bandwidth-bound and MoE only reads its active experts per token. |
| Agent runtime | **Hermes Agent** (`github.com/NousResearch/hermes-agent`), two isolated profiles | Replaced both Odysseus (decommissioned 2026-08-12) and the original hand-written `agent/run.py`/`agent/pm_bot.py` (deleted 2026-08-14) — an existing framework already had a scheduler, MCP client, Telegram gateway, and skills system, so a third bespoke agent runtime wasn't worth building. |
| Agent instructions | `SOUL.md` (`task-manager-bot`) + `SKILL.md` (`pmchaser-bot`'s `task-chaser`/`task-digest`) | Same idea as the original Skills, ported to Hermes's own convention — plain-language procedure, pitfalls, worked examples, no code. **Lives entirely in `~/.hermes`, which is NOT a git repo** — see the "documentation and tracking" entry near the end of this file. |
| New capability | **`pm-chaser-mcp`** (Python 3.12, `mcp` 2.0 SDK) | Grew from the original 7 tools to ~20 — task CRUD, chase planning, Telegram I/O, and now assistant-persona settings too. |
| Data | **SQLite** + **SQLAlchemy** | Same as always — `Person`, `Task`, `CheckIn`, `UnmatchedMessage`, `BotState` — plus `Task.manager_id` (added 2026-08-14, multi-manager groundwork) and `BotState.assistant_name` (persona feature). |
| Messaging | **Telegram Bot API** via **`httpx2`**, two separate bot tokens | Just a handful of calls — `sendMessage`, `getUpdates`, and now `setMyName`. Still no full bot framework — the wrong shape for a service that's only ever called into by tool calls, never listening on its own. |
| Hosting | **Plain Docker** (`--restart=always`), **not Kubernetes** | Migrated off Kubernetes 2026-08-14 after the LoadBalancer-IP-drift failure class kept recurring with no Odysseus left to justify the added complexity — see that day's entry for the full reasoning and tradeoff. |
| Scheduling | **Hermes's own cron**, on the `pmchaser-bot` profile | Replaced `launchd` (`com.pmchaser.chase`/`digest`) — chase every 15 min (tightened from the original 30), digest daily 09:00. |

## How one chase actually flows (current)

1. **Hermes's cron** fires the `task-chaser` skill on the `pmchaser-bot` profile — no human involved.
2. The agent calls **`get_chase_plan()`** — one call. The service polls Telegram, files anything that arrived, applies every rule in Python (priority-based re-ping floor, escalation threshold, blocked-task exclusion, unreachable owners, one-task-per-person) and returns a finished shortlist — same one-call-does-everything design as the original, still the load-bearing idea in this whole project.
3. For any reply, the agent **reads what it means** — *"waiting on the finance sheet"* → `blocked`, *"push it to 9pm"* → also `blocked` (an extension request is never self-granted, see 2026-08-14) — and calls `update_task`, then sends a brief acknowledgment back to the person.
4. For anything to chase, the agent **writes the message itself** and calls `telegram_send_message(..., task_id=...)`, which sends it and records the check-in atomically.
5. Their reply is picked up on the next run, matched back to the task by Telegram's reply-threading (or flagged as ambiguous rather than guessed).
6. Separately, the manager can trigger any of this on demand through `task-manager-bot`: `chase_now`, a live "did X reply?" check, or the full digest — without waiting for the next scheduled tick.
7. Once a day (or on demand), **`get_digest_data()`** feeds the manager a summary — including blocked work needing a decision.

## The dividing line that shapes everything

**Code decides *what* and *who*. The model decides *what to say* and *what replies mean*.**

| Decided in Python | Decided by the model |
|---|---|
| Which tasks are overdue or due soon | Every word of every message sent to a human |
| Who was pinged too recently to chase again | What a free-text reply actually means |
| Who to escalate after repeated silence | Which task an ambiguous message referred to |
| Which tasks are blocked and belong to the manager | Whether a reply is even a status update |
| One task per person per run | What belongs in the digest and what leads it |

Rules that must *always* hold cannot live in prose the model might skip — so they live in code, where they are guaranteed. Everything requiring language or judgement stays with the model. This split was not the original design; it came from the failure documented under *"The debugging that mattered most"*.

---

## 2026-08-04 — Local AI stack set up (before this project started)

Before this project existed, the local infrastructure it depends on was already built and tested:

- **[Odysseus](https://github.com/odysseus-dev/odysseus)** — a self-hosted AI workspace app (chat, agents, research, docs, email, notes/calendar) — cloned to `../odysseus/`, run via `docker compose` at `http://localhost:7000`.
- **LM Studio** (the "classic" version) installed natively on the Mac, not in Docker — this is what actually runs the AI model, using the Mac's GPU (Metal) directly. Docker containers on a Mac can't reach the GPU, so the model has to be served outside Docker and called into from inside it.
- Odysseus's Docker container reaches LM Studio at `http://host.docker.internal:1234/v1` — a special address Docker provides that means "the Mac itself, not inside the container." This was configured through Odysseus's Settings screen, not through its `.env` file (the `LM_STUDIO_URL` variable in `.env` turned out not to actually be wired into `docker-compose.yml`, so setting it there would silently do nothing).
- **Model: Qwen3.6-27B-MLX-4bit** — a 27-billion-parameter AI model, quantized (compressed) to run efficiently on this Mac's memory, shared between LM Studio and another app (Bionic) via the same model cache folder.
- Confirmed working end-to-end: a chat message in Odysseus successfully round-trips through LM Studio and gets a real answer back.

**Why this matters going forward:** every automated job this project runs will reason using this same Qwen model, through this same LM Studio connection. No cloud AI API is used anywhere in this project.

---

## 2026-08-04 — Project scoped: Action-Item Tracking (chasing people on Telegram)

Starting point was a list of 10 ways AI can help with project/program management. Priority order chosen: **#4, Action Item Tracking, first**; #1, Meeting Minutes, next after that.

The idea for #4: an AI that knows every task, its owner, and its deadline; automatically pings the owner on Telegram when something's due or overdue; reads their reply (even messy free text) and figures out their real status; and gives the manager a plain-English daily summary.

**Decision — new sibling repo, not inside Odysseus.** This project (`pm-chaser/`) lives next to `odysseus/`, as its own folder and its own git repository, rather than being added into the Odysseus codebase.
- *Why:* Odysseus is a third-party open-source project under the AGPL license. Mixing new code directly into it would create licensing complications and make it harder to pull in future Odysseus updates. Keeping this project separate — and never editing Odysseus's own files — avoids all of that; this project just *connects* to the running Odysseus the same way you'd install a plugin.

**Investigated what Odysseus already provides**, before designing anything new, to avoid rebuilding things that already exist. Found:
- An **auto-scheduler** (`src/task_scheduler.py` in the Odysseus codebase) — lets you say "run this every 30 minutes" or "once a day," and it wakes up an AI agent to do a job with nobody clicking anything.
- A **chat interface** the manager can already use to create/edit things by typing normally.
- A **plug-in system (MCP)** — Odysseus's AI can be given new abilities by connecting to small external "helper programs." Confirmed (`src/mcp_manager.py`) that these helper programs can run **anywhere reachable over the network**, not just bolted directly into Odysseus's own process — meaning a new helper program can be built and run completely independently.
- A **Skills system** (`services/memory/skills.py`) — lets you hand the AI a written instructions sheet (plain text, not code) describing how to behave for a specific job.
- **What's missing:** nowhere to store task/owner/deadline/progress data, and no Telegram integration at all (confirmed via a repo-wide search — genuinely doesn't exist yet).

**Decision — build one small new service, not a parallel system.** Rather than building a whole separate app (its own chat interface, its own scheduler, its own agent logic), the plan is to build exactly one new thing — a small helper program called **`pm-chaser-mcp`** — and plug it into the Odysseus that's already running. It does two jobs only:
1. Stores the data (people, tasks, deadlines, progress, message history) in **SQLite** — a database that's just a single file on disk, no separate server process needed, plenty for a small team.
2. Talks to Telegram's messaging API — sending pings, and checking for replies.

It exposes 7 abilities ("tools") for Odysseus's AI to call: `create_task`, `list_tasks`, `update_task`, `register_person`, `telegram_send_message`, `telegram_get_updates`, `record_checkin`. Everything involving actual thinking — drafting a message's wording, understanding what someone meant in their reply, writing the digest — stays inside Odysseus's existing AI agent. `pm-chaser-mcp` is deliberately just plumbing.

**Decision — Kubernetes, but scoped narrowly.** Only `pm-chaser-mcp` runs on Kubernetes (a system that keeps a program running reliably — auto-restarting it if it crashes, managing its storage). Specifically **Docker Desktop's built-in Kubernetes** (not minikube or kind), chosen because it shares networking with the Mac's existing `docker compose` setup — so Odysseus should be able to reach the new service the same way it already reaches LM Studio, without new networking problems to solve. **Odysseus itself stays on `docker compose`, untouched** — it is not being migrated to Kubernetes.

**Decision — Qwen only, no second model.** Considered adding a second AI model called Hermes (fine-tuned specifically to be reliable at calling tools/functions) just for the two automated jobs, since Odysseus has a setting for using a different model for background jobs vs. regular chat. Decided against it: Qwen's models already handle tool-calling well, and the tool schema here is simple (7 well-defined tools) — not complex enough to need a specialist model. A second model would also compete for the Mac's memory and add friction switching models in LM Studio, for no proven benefit. **Only Qwen3.6-27B is used, for everything.** Revisit only if Qwen is actually observed getting tool calls wrong once this is running for real.

**Considered and declined — a separate agent-orchestration framework** (tools like AutoGPT/CrewAI/OpenHands, which coordinate multiple AI agents through complex workflows). Declined because this project is one repeating job ("check deadlines → chase → parse reply → maybe digest"), not a multi-agent coordination problem — Odysseus's existing single-agent scheduler already covers it. Noted as worth revisiting for a *future* project that genuinely needs several specialized agents working together (e.g. a future Meeting Minutes pipeline: a transcriber agent, a summarizer agent, and an action-extractor agent working in sequence).

Full plan (architecture diagram, the complete build order, and how each phase gets verified) was written up and approved. Reference copy: `/Users/threeporkchops/.claude/plans/robust-crafting-clock.md`.

---

## 2026-08-05 — Step 1: repo scaffolded

Created this repo:
- `pm-chaser/` folder, sibling to `odysseus/`, own git repository on branch `main`.
- `.gitignore` — set up to exclude anything that shouldn't ever be committed: the local SQLite database file, the real Telegram bot token, and the real Kubernetes secret file (only a `.example` template stays tracked).
- `README.md` — short entry point pointing here.
- This file.

**Next up — Step 2:** before writing any real code, prove that a program running on the new Kubernetes cluster can actually be reached from the existing Odysseus `docker compose` container, the way the plan assumes. This is the one unproven assumption in the whole plan, so it gets checked first and cheaply, with a disposable test program, before anything real is built on top of it.

---

## 2026-08-05 — Step 2: network path tested — original assumption was wrong, found the real one

**Setup:** Enabled Kubernetes in Docker Desktop (v4.85.0's newer UI presents this as "Create Kubernetes Cluster" rather than a simple checkbox). Chose cluster type **kind** ("Kubernetes IN Docker" — each Kubernetes "node" runs as its own nested container) over the alternative, **Kubeadm** (the tool real production clusters use) — kind is Docker Desktop's default and better-documented for this kind of local setup, and we don't need Kubeadm's production-realism for a single local service. 1 node, version 1.36.1 (default, matches the installed `kubectl`).

**Test:** deployed a disposable nginx web server into the cluster and tried to reach it from Odysseus's container, the way the plan originally assumed (`host.docker.internal` + a `NodePort` service — the same pattern already working for LM Studio).

**Result: that assumption was wrong.** It didn't work — not even from the Mac itself. Investigating why, in three layers:
1. **Your Mac** — its own network, `localhost`, etc.
2. **Docker Desktop's internal Linux VM** — macOS can't run Linux containers natively, so Docker Desktop quietly runs a real lightweight Linux VM in the background, and *everything* (Odysseus's `docker compose` containers, and now the Kubernetes cluster) actually lives inside that one shared VM.
3. **Individual container networks inside that VM** — Odysseus's containers and the Kubernetes cluster's containers live in different internal "neighborhoods" (Docker networks) within the same VM.

`host.docker.internal` bridges **layer 1 to layer 2** ("let a container reach the Mac") — it was never a bridge *between two neighborhoods inside layer 2*, which is what's actually needed here. `NodePort` specifically failed because Docker Desktop only opens one single port from the Mac into the kind cluster (`6443`, for `kubectl` itself) — nothing else gets automatically exposed to the Mac at all.

**What actually works:** those two neighborhoods inside the shared VM can already reach each other directly, without needing the Mac involved at all. Tested three service types:
- `NodePort` — failed, not exposed to the Mac (irrelevant anyway — we don't need Mac access, only Odysseus's container needs it).
- `ClusterIP` (the plain default) — failed even from inside Odysseus's container. This address is a Kubernetes-internal illusion, only usable by traffic already inside the cluster's own machinery.
- `LoadBalancer` — **worked.** Got assigned an IP (`172.19.0.5` in this test) that Odysseus's container could reach directly and get a real response from.

**Decision — use `LoadBalancer`, not `NodePort`, for `pm-chaser-mcp`'s Kubernetes Service.** This corrects the original plan. Odysseus will be pointed at that service's assigned IP directly when we register it as an MCP server in Phase 5.

**Known open risk, accepted for now:** that IP address comes from the cluster's internal network and could change if the cluster is ever destroyed and rebuilt (not tested whether it survives an ordinary Docker Desktop restart). For a local personal project, treating "re-check the IP if the cluster is ever reset" as an acceptable manual step is fine — not worth building extra infrastructure (a stable DNS layer, a proxy) to solve a problem that should rarely occur.

Cleaned up the disposable test deployment and service afterward.

**Next up — Step 3:** build `pm-chaser-mcp` itself, locally, with no Kubernetes involved yet — the data model and the 7 tools, tested directly on this Mac.

---

## 2026-08-05 — Step 3: `pm-chaser-mcp` built and tested locally

**Environment:** the official `mcp` SDK requires Python 3.10+, but this Mac only had the system Python 3.9.6. Installed Python 3.12 via Homebrew (a clean, separate install — doesn't touch the system Python) and created a virtual environment at `server/.venv/` so the project's dependencies stay isolated.

**A security check that turned out fine, but was worth doing:** installing dependencies pulled in `mcp==2.0.0` plus two packages that weren't expected — `mcp-types` and `httpx2` — neither of which is part of the `mcp` SDK I had prior knowledge of. Rather than assume this was fine, stopped and verified both against their real PyPI pages before writing or running any code that depended on them, since this service will eventually hold a real Telegram bot token and run with tool-calling access:
- **`mcp` 2.0.0** — confirmed genuinely released 2026-07-28, maintained by the real Model Context Protocol team (a Linux Foundation project) including a known Anthropic engineer. Just a newer major version than the one I had prior knowledge of.
- **`httpx2`** — confirmed as Pydantic's official, acknowledged continuation of the original `httpx` HTTP library (whose original author, Tom Christie, is credited as the original author here too), taken over because the original project had gone quiet.

Both legitimate. Standardized on `httpx2` for this project's own Telegram calls too, rather than installing the now-effectively-legacy `httpx` as a second, redundant HTTP library.

**API differences from the major version bump:** because `mcp` 2.0.0 is a genuinely new major version, its API is not the same as the older, more commonly documented `FastMCP` pattern. Checked the actual installed library directly rather than guessing:
- The high-level server class is `MCPServer` (`from mcp.server import MCPServer`), not `FastMCP` — same shape (a `.tool()` decorator / `.add_tool()`, a `.run(transport=...)` method), just renamed.
- The client-side connector is `streamable_http_client` (underscore), not `streamablehttp_client`.
- It yields 2 values (`read_stream, write_stream`), not 3, when opening a connection.

**Built:**
- `server/models.py` — SQLAlchemy tables for `Person`, `Task`, `CheckIn`, plus a tiny `BotState` row that tracks Telegram's "what have I already seen" cursor.
- `server/telegram_client.py` — the only two Telegram API calls this project needs (`sendMessage`, `getUpdates`), using `httpx2` directly rather than a full bot framework (which is built around keeping a live connection open — the wrong shape here, since Telegram is only ever checked when the agent calls a tool during a scheduled run).
- `server/tools.py` — the 7 tools. One refinement made while writing this: `telegram_send_message` takes a person's **name**, not a raw Telegram chat ID — resolved internally — so the agent-facing tool surface is name-based throughout (matching `create_task`/`list_tasks`), rather than exposing a Telegram-internal ID the agent has no reason to know.
- `server/main.py` — wires the 7 tools into `MCPServer` and starts it over the `streamable-http` transport (proven in Step 2 to be how Odysseus will reach it).

**A real bug found and fixed:** SQLite has no true timezone-aware datetime type — SQLAlchemy round-trips datetimes through it as plain strings, and the timezone information does not survive that round trip. A datetime written as timezone-aware came back from the database "naive" (no timezone attached), and comparing it against a still-aware "now" crashed with `TypeError: can't compare offset-naive and offset-aware datetimes`. Fixed by standardizing on "naive, but always UTC" datetimes everywhere in this app — the standard workaround for this well-known SQLite/SQLAlchemy limitation — rather than comparing values that came from two different conventions.

**Tested two ways, both passing:**
1. `server/test_tools.py` — calls all 7 tool functions directly (bypassing the MCP protocol layer, since they're just plain Python functions) and checks the database updates correctly, including error paths (unknown owner, duplicate registration, unknown task id) and that the two Telegram tools fail gracefully (not a crash) when `TELEGRAM_BOT_TOKEN` isn't set yet.
2. `server/test_wire.py` — starts the real server and connects to it with the actual MCP client library over `streamable-http` (the real wire protocol, not just calling Python functions), lists its advertised tools, and calls `register_person` / `create_task` / `list_tasks` for real. Along the way, discovered that a tool returning a list comes back as **one content block per list item**, not one JSON array — adjusted the test's parsing accordingly once confirmed against the running server.

**Next up — Step 4:** wire up the real Telegram bot — create it via `@BotFather`, get `TELEGRAM_BOT_TOKEN` configured, and test the send/receive/account-linking flow for real with your own Telegram account.

---

## 2026-08-05 — Step 4: real Telegram bot wired up and tested end to end

**Bot created:** via `@BotFather` — **@pmchaser_threeporkchops_bot** ("PM Chaser"). Token stored in `server/.env` (gitignored, never committed) and loaded automatically at startup via `python-dotenv` (added as a dependency). `server/.env.example` added as a tracked template with no real value, so the shape of what's needed is still visible in git even though the real token isn't. Validated the token for real against Telegram's `getMe` endpoint before doing anything else with it.

**Tested the full loop for real, not just locally simulated:**
1. `register_person("Daniel", role="manager")` → got a link code.
2. Sent `/start <code>` to the bot from a real Telegram account → `telegram_get_updates` correctly linked the account. (One harmless artifact: Telegram's own auto-sent bare `/start`, from the client's suggested-button UI before the coded one was typed, showed up in `unmatched` as expected — not a bug, just Telegram's own first-contact message.)
3. `telegram_send_message` sent a real message that was actually received on a real phone — confirmed by the human, not just Telegram's API reporting success.
4. Created a real task, sent a real chase-style message about it (hand-written for this test — see note below), and logged it via `record_checkin`.
5. Replied on Telegram with an arbitrary message → `telegram_get_updates` correctly matched the reply back to that exact task, with no `unmatched` entries. This is the core mechanism the entire project depends on, and it now has a real, human-in-the-loop confirmation that it works — not just a local test script talking to itself.

**Important clarification surfaced during this step, worth keeping visible:** the chase message sent during step 4 above was hand-written directly in a test script for this test only — a stand-in to validate the mechanism. It is **not** how the finished system works. In the finished system, `pm-chaser-mcp` never contains a single hardcoded message: Odysseus's agent (using Qwen via LM Studio, guided by the Skill written in Step 6) decides all message wording and all reply interpretation itself, and only *calls* `telegram_send_message` / reads `telegram_get_updates` results the same way this test script did manually. This step proved the pipe works; it did not yet involve the real writer at the other end of that pipe.

**Next up — Step 5:** containerize `pm-chaser-mcp`, deploy it to Kubernetes with a `LoadBalancer` Service (per the Step 2 correction), and register it in Odysseus's Settings → MCP Servers — at which point Odysseus's own agent, not a hand-written test script, starts driving all of this.

---

## 2026-08-05 — Step 5: containerized, deployed to Kubernetes, and connected to Odysseus

**Containerized:** `server/Dockerfile`, based on `python:3.12-slim`. `PM_CHASER_DB_PATH` points at `/data/pm_chaser.db` — a mounted volume, so the SQLite file survives a pod restart — and `TELEGRAM_BOT_TOKEN` comes in as a real environment variable from a Kubernetes Secret (not a `.env` file — that's a local-dev-only convenience; `load_dotenv()` in `main.py` is a harmless no-op when no `.env` file exists, which is the case inside the container).

**A real gap found and worked around, before writing any manifests:** tested empirically (rather than assuming) whether a locally-`docker build`-ed image is visible inside the "kind" cluster's own container runtime. It isn't — kind nodes run their own separate `containerd`, with its own image store, entirely apart from the host Docker daemon's. The `kind` CLI (installed via Homebrew to help with this) couldn't help either, since it doesn't recognize a cluster Docker Desktop provisioned through its own internal path rather than through the `kind` CLI itself. Worked around it directly: `docker save <image> | docker exec -i desktop-control-plane ctr -n k8s.io images import -` — pipes the image straight into the node container's own `containerd`, confirmed present afterward via `ctr images ls`. This is a manual step that will need repeating any time the image changes, until/unless a registry gets added later.

**Kubernetes resources created**, in a new `pm-chaser` namespace: `Deployment` (1 replica, `imagePullPolicy: Never` since the image is loaded locally, not pulled), `Service` (`type: LoadBalancer`, per the Step 2 correction), `Secret` (holds `TELEGRAM_BOT_TOKEN`, written from the existing `.env` value programmatically so it was never re-typed or printed), `PersistentVolumeClaim` (256Mi, using the cluster's default `standard` StorageClass). **Deliberately skipped** the `ConfigMap` from the original plan — there's currently no actual non-secret setting for this service to hold; the chase interval belongs to Odysseus's scheduled job (Step 7), not to `pm-chaser-mcp` itself. No sense creating a config file with nothing genuine to put in it.

**Verified for real, twice:**
1. The Kubernetes Service got assigned `172.19.0.5` (same address range Step 2 predicted) — ran the actual MCP wire-protocol test (`test_wire.py`) against it from inside a throwaway container (the bare Mac process can't reach this address either, same limitation as Step 2 — needed a container to test from). Passed: real tool calls, real data written.
2. Registered `http://172.19.0.5:8000/mcp` in Odysseus's Settings → **Integrations → MCP** tab (not a standalone "MCP Servers" page — it's one tab among several integration types; had to check the actual frontend code, `static/js/settings.js`, to find the real navigation path after an initial wrong guess). Confirmed by the user directly in Odysseus's UI: shows connected, all 7 tools listed.

**Next up:** confirm Odysseus's own chat/agent can actually *call* these tools (not just see them) — e.g. asking it to list tasks — before moving to Step 6 (writing the Skill that teaches it how to use them for the actual chasing job).

**Found along the way — Agent vs. Chat mode:** the first live test failed silently (the model flatly said it had no access to any task list) even though the server showed "connected" with 7 tools in Settings. Traced this through the actual frontend/backend code rather than guessing: Odysseus has a per-message **"Agent" / "Chat" mode toggle** next to the message input (`static/index.html`, `static/js/chat.js`) — tools are only ever passed to the model when a message is sent in **Agent mode** (`agent_mode=(chat_mode == "agent")` in `routes/chat_routes.py`); plain Chat mode never sees any tools at all, by design, regardless of what's connected in Settings. The browser had it saved on "Chat" from an earlier session. Worth remembering for Step 7 too — the scheduled automated jobs will need to run through whatever path carries `agent_mode=True`, not the plain chat path.

**Confirmed working, for real, in Odysseus's own UI:** switched to Agent mode, asked "list my tasks" — the model correctly called `MCP__073ACBD3__LIST_TASKS`, got back real data from the Kubernetes-hosted service, and presented it as a clean table with sensible follow-up suggestions ("create a new task, update this one, or check for overdue/due-soon items?"). Full chain confirmed working end to end, driven entirely by the agent's own reasoning: **Odysseus (Agent mode) → Kubernetes `LoadBalancer` → `pm-chaser-mcp` → SQLite.**

**Step 5 complete.** Next up — **Step 6:** write the `task-chaser` Skill that teaches this same agent how to actually do the chasing job (tone, escalation rules, digest format, and handling the reply-matching gap found during Step 4).

---

## 2026-08-05 — Interlude: hardening the tools before writing the Skill

Before writing the Skill, walked the whole system as both a PM and an employee to find problems like the spontaneous-reply gap. Found several that would have made the Skill's rules unenforceable — an instruction like "don't re-ping within 4 hours" is worthless if the agent has no way to see when the last ping was sent. Fixing these first was the right order; writing the Skill and then discovering the agent couldn't obey half of it would have been worse.

**Tool surface: 7 → 9.**
- **Added `list_people`** — the agent had no way to enumerate the team at all, so it literally could not find out who the manager was in order to send them a digest. It could only learn names by seeing them attached to tasks.
- **Added `delete_task`**, and a `cancelled` status via `update_task`. Previously there was no way to remove anything — telling, since wiping the Step 5 test data required hand-editing SQLite.
- **Added `resolve_unmatched`** — closes the loop on the spontaneous-reply problem.
- **Removed `record_checkin`**, folding it into `telegram_send_message(task_id=...)`. Sending a chase and logging it were two separate calls, and skipping the second silently made the ping invisible to future runs — meaning the person would be re-pinged every 30 minutes forever. Now it's one atomic call, so the wrong path no longer exists.

**Reply matching now refuses to guess.** It reads Telegram's `reply_to_message` field (requires storing the outgoing `telegram_message_id`, which wasn't captured before), so a threaded reply lands on the right task even with several pings outstanding. When it genuinely cannot tell — two open pings, no threading — it no longer silently attaches the reply to whichever ping was most recent. It records the ambiguity with a list of candidate tasks and hands the decision to the agent.

**Unmatched messages are now persisted.** This was worse than first described: `telegram_get_updates` permanently advances Telegram's cursor, so a message not stored anywhere is gone for good if the run fails afterwards. Matched replies survived (written onto the check-in), but unmatched ones evaporated. They now persist across runs until explicitly resolved or dismissed.

**Timezone bug fixed.** A deadline typed without an offset was being treated as UTC. At UTC+8 that put "Friday 5pm" at 1am Saturday — every deadline eight hours wrong, and overdue detection wrong all day. Naive input is now interpreted in `PM_CHASER_TZ` (default `Asia/Singapore`), and output carries both UTC and local renderings. This also finally gave the ConfigMap skipped in Step 5 something genuine to hold.

**`list_tasks` now returns decisions, not raw data.** It includes `hours_until_deadline` (negative = overdue), `hours_since_last_checkin`, `unanswered_checkin_count`, and `owner_is_linked` — all pre-computed. LLMs are unreliable at date arithmetic, so the service does it rather than hoping the model gets it right. `owner_is_linked` also stops an unreachable person being mistaken for someone ignoring their pings and escalated for it.

Also: case-insensitive name lookup, `status="done"` forces 100% (one direction only — 100% while still "in_progress" is a legitimate awaiting-review state), and Telegram's own bare `/start` plus messages from unregistered strangers are discarded rather than polluting the unmatched queue.

**Deliberately deferred:** removing a *person*. Someone who owns tasks can't be cleanly deleted without deciding what happens to their task history, and that deserves a real decision rather than a silent default. Also skipped an `update_person` tool — renaming was done directly in the database instead, since adding a tool would have cost another rebuild/redeploy/reconnect cycle for something rare.

**Testing:** the local suite grew to 48 checks, with Telegram stubbed out so the reply-matching logic could be exercised directly — threaded replies, ambiguous replies, spontaneous updates, strangers, and Telegram's bare `/start`. One failure during the run turned out to be the *test scenario* being wrong rather than the code: a message arrived while a ping was still outstanding, so matching it as a reply was correct behaviour. Confirmed by inspecting the actual check-in state rather than assuming, then fixed the test.

**Redeployed.** The PVC database had the old schema, and SQLAlchemy only creates missing *tables* — it does not add *columns* to existing ones. Since that database was empty, deleting the file was cleaner than writing a migration. The `LoadBalancer` IP stayed `172.19.0.5`, so Odysseus's saved URL kept working; only a Reconnect was needed to pick up the new tool list.

---

## 2026-08-05 — Step 6: the task-chaser Skill

Wrote `skills/task-chaser/SKILL.md` (source of truth in this repo; installed into Odysseus at `data/skills/project-management/task-chaser/SKILL.md`).

**Three findings from reading Odysseus's skill code, each of which would have caused a silent failure:**

1. **Skills are filtered by owner, strictly.** `SkillsManager.load()` hides any skill whose frontmatter `owner` doesn't match the logged-in username — there's an explicit security comment noting that un-owned skills used to leak to every user. Without `owner: admin`, the Skill would have been invisible to everyone and we'd have been debugging a silent no-op.
2. **`requires_toolsets` must stay empty.** It gates against Odysseus's own built-in tool categories (`TOOL_SECTIONS` in `agent_loop.py`), not connected MCP servers. Listing the pm-chaser tools there would have *hidden* the skill rather than targeting it.
3. **Only four headings are recognised**, and only some sections are auto-injected. The parser knows `When to Use`, `Procedure`, `Pitfalls`, `Verification`; any other `##` heading has its title silently stripped and its content absorbed elsewhere. More importantly, the block Odysseus injects into the agent's context automatically contains only description, when_to_use, procedure and pitfalls — **verification and any extra content are shown only if the agent explicitly calls `manage_skills action=view`**.

That third point drove the structure. The daily digest procedure was first written under its own `## Daily Digest Procedure` heading; the parser silently deleted the heading, leaving six unlabelled steps sitting directly beneath the chase steps — an easy way for the agent to send a digest during a chase run. Switching to `###` was worse: it got absorbed into the Verification section, inflating it from 6 bullets to 12. The fix was to stop fighting the parser and put **both jobs in the single `Procedure` list** with an explicit branch at step 1, so both are guaranteed to reach the agent. Verified by parsing the file with Odysseus's own parser: 18 procedure steps, 10 pitfalls, 7 verification, nothing lost.

**Relevance matching needed tuning too.** Skills are auto-injected based on a similarity score against the request. Testing real prompts showed the chase run matched but **the daily digest did not** — meaning the digest job would have run without its instructions. Reading the matcher revealed a tag-boost path: if every token of a tag appears in the query, the score clears the threshold outright. The original tags missed "digest" entirely, and `tasks` did not match the singular `task`. After expanding the tags, all realistic prompts match while genuine negatives ("what is the weather today", "write me a python script") still correctly don't.

**Still, fuzzy matching is not good enough for a scheduled job that must never silently skip its instructions.** The scheduled prompts therefore carry the procedure inline as well, so the run works whether or not the skill is matched.

**Skill content** encodes what was learned building this: process incoming replies *before* sending anything; skip anyone pinged in the last 4 hours; escalate to the manager after 3 unanswered pings instead of continuing; chase at most one task per person per run (so replies stay attributable); never count an unlinked person as ignoring you; never do date arithmetic; and always pass `task_id` when chasing.

---

## 2026-08-05 — The debugging that mattered most: why the agent never sent anything

With everything built and connected, the first live test failed in a way that took four attempts to diagnose. Worth recording in full, because three of my four hypotheses were wrong and the real cause was somewhere I wasn't looking.

**The symptom.** Asked in Odysseus chat to run the chase check, the model would reason correctly — identify the right task, the right person, the right overdue time — announce "sending the chase message to Marcus now", and then not send it. Across one 12-round run it announced the send six separate times. `telegram_send_message` was never called once. It called the read-only tools (`telegram_get_updates`, `list_tasks`) happily and repeatedly, looping on them instead of progressing.

**Wrong hypothesis 1: the skill was too long.** The injected skill text was ~6,000 characters (18 procedure steps, 11 verbose pitfalls), so the theory was that the model was summarising rather than executing. Split the skill in two (chase / digest) and made every step terse, cutting it to ~2,700. It made no difference — and worse, the *original longer* version had actually produced tool calls while the leaner one produced none, which contradicted the theory outright.

**Wrong hypothesis 2: the call chain was too long.** The model had to make five to eight bookkeeping calls before reaching the one that mattered. Real problem, real fix (`get_chase_plan`, described below) — but it did not fix this symptom either.

**Wrong hypothesis 3: the model can't emit a tool call containing generated prose.** Every tool it successfully called took trivial arguments; the one it never called required composing a multi-sentence message *inside* the call. Plausible, and testable: called LM Studio directly with just those two tool schemas. **It emitted the send call perfectly on the first attempt**, composing the message text and all. So the model was fine — the difference had to be environmental.

**Wrong hypothesis 4: the tool wasn't being exposed.** The model's own reasoning listed the functions it could see, and `telegram_send_message` was absent — it even said the tool "should also be available", inferring rather than reading. Checked Odysseus's manager directly: all 11 tools connected, all 11 OpenAI schemas generated correctly. The model had simply hallucinated its own tool list.

**The actual cause: total tool-surface size, and testing in the wrong place.** Odysseus exposes 60 built-in tools — **28,541 characters** of tool documentation — plus our 11. That is ~71 tools for a 27B 4-bit model to hold in attention while executing a multi-step procedure. The isolated test that worked gave it 2 tools and ~500 characters.

Crucially, **chat and scheduled tasks are not the same environment.** Odysseus's scheduler runs RAG-based tool selection first; its own code comments say why: *"Without this, all 40+ tools get sent and models hit their tool limit."* MCP tools are added on a separate path that pruning never touches, so ours always survive. Measured:

| Environment | Built-in tools | Tool docs | Total |
|---|---|---|---|
| Chat (where I kept testing) | 60 | 28,541 chars | 71 |
| Scheduled task (default) | 41 | 14,070 chars | 52 |
| Scheduled task + crew allowlist | **2** | **918 chars** | **13** |

I had spent the whole session testing in the chat window because it was convenient, when the product only ever needed to work as a scheduled task. The default scheduled path alone was not enough either — RAG pulled in ten email tools, because "message each person" is semantically close to email, and `compose_task_relevant_tools` unions the result with an always-available set and a shell/file default set.

**The fix** was a **CrewMember with an `enabled_tools` allowlist** of just `["ask_user", "update_plan"]`. That inverts to a disabled set covering every other built-in, while leaving MCP tools untouched. Result: 918 characters of built-in tool documentation instead of 28,541 — a 31× reduction, close to the isolated test that worked first time.

**Lesson worth keeping:** test in the environment the thing actually runs in. Four hypotheses, three wrong, and the answer was that the failing environment was never the target environment.

---

## 2026-08-05 — Moving the mechanical decisions into code

Separately from the diagnosis above, the original design had a genuine flaw: the agent was doing work that did not need an LLM at all. Deciding *which* tasks are overdue, who to skip because they are unlinked or were pinged recently, who to escalate at three unanswered pings, and one-task-per-person — all of that is mechanical filtering with exactly one correct answer, and it was being done across five to eight tool calls of date arithmetic and list-winnowing.

Worse, rules expressed only in a Skill are *suggestions*. "Never re-ping within four hours" is only true if the model remembers to check.

**Added `get_chase_plan()`** — one call that polls Telegram, files everything that arrived, applies every filter rule in Python, and returns a finished answer: who to chase (one per person, most urgent first), who to escalate, who is unreachable, which replies need interpreting, and which messages could not be matched. **It also returns everything it filtered out, in `skipped`, with the reason** — so the agent can still see the full picture and knowingly override a rule rather than being silently constrained by it.

**Added `get_digest_data()`** — the same idea for the digest: one call returning the manager (looked up, never guessed) plus all active tasks with their state. Deliberately does *not* pre-categorise tasks as "at risk"; that is a judgement call and judgement stays with the agent.

**The dividing line adopted: code decides *what* and *who*; the LLM decides *what to say* and *what replies mean*.** The agent keeps everything language-shaped — composing every outbound message in its own words, interpreting free-text replies into structured status, resolving ambiguous messages, writing the digest narrative, and deciding what deserves the manager's attention. It loses only date arithmetic and list filtering.

This is less autonomous than the original design, and that was a deliberate, discussed trade. The honest accounting: the agent can no longer invent a chase strategy nobody anticipated, and every rule moved into Python is a situation frozen to one judgement. What it gains is that the rules always hold, and the failure mode of getting lost in bookkeeping disappears.

---

## 2026-08-05 — Step 7: automation live, and verified end to end

**Created a CrewMember, "PM Chaser"**, with `enabled_tools` set to `["ask_user", "update_plan"]` and timezone `Asia/Singapore`. That allowlist is what collapses the built-in tool surface (see the diagnosis above); the timezone is what the scheduler uses to interpret cron expressions.

**Created two ScheduledTasks**, both linked to that crew, both leaving `model`/`endpoint_url` unset so they fall through to the account default (Qwen via LM Studio):

| Task | Cron | Fires |
|---|---|---|
| Task chase check | `*/30 * * * *` | every 30 minutes |
| Daily task digest | `0 9 * * *` | 09:00 Singapore (01:00 UTC) |

Each task's prompt carries the procedure inline rather than relying only on the Skill being matched by similarity — the Skill adds tone and pitfalls on top when it matches, but the run does not depend on that happening.

**An operational behaviour worth knowing about: scheduled tasks yield to interactive use.** `BACKGROUND_TASK_FOREGROUND_GATE` (default on) pauses background work whenever the UI is active, so a run started while the browser is open gets cancelled mid-flight. This is sensible design on a single-GPU machine — a background chase competing with a live chat for the same model would make both slow — but it means an idle browser tab can still defer runs, because some UI polling counts as activity. Notably `/api/email/unread-state` is *not* on Odysseus's passive-exclusions list even though its sibling `/api/email/urgency-state` is, so sitting on the Email page starves background tasks. `BACKGROUND_TASK_MAX_WAIT_SECONDS` defaults to `0`, meaning "wait indefinitely for quiet" rather than skip — so runs queue rather than being lost. Two env knobs exist if it ever becomes annoying: `BACKGROUND_TASK_BROWSER_ACTIVE_SECONDS` (default 45) and `BACKGROUND_TASK_FOREGROUND_GATE=false`. The second is not recommended here.

### Verified end to end, with real Telegram messages

**1. Chase sent.** The scheduled run fired, and Qwen composed and sent this unprompted:

> *"Hey Marcus, quick check on "Send the vendor contract to legal" — it's been about 4 hours past the deadline and still showing as not started. Are you able to push this through today?"*

Nothing there is templated. It named the task, turned `hours_until_deadline: -4.1` into "about 4 hours past the deadline", noticed the status was `not_started` and worked that in, and asked one question. The check-in was recorded with Telegram's `message_id`, which is what makes threaded replies attributable.

**2. Reply interpreted.** Replying *"This is a blocker as I am waiting on the finance sheet from the finance department"* caused the agent to set the task's status to **`blocked`**. There is no keyword list mapping that phrase to that status — the model read free text and inferred structure, then called `update_task` itself.

**3. No re-ping.** The same run correctly did *not* send another message, because `hours_since_last_checkin` was under the four-hour floor. The rule held.

**4. Spontaneous follow-up resolved.** A second, unsolicited message (*"finance just said that they would do it in 2 days"*) arrived with no ping outstanding. It was stored as unmatched with the reason *"spontaneous update — no ping was outstanding"*, and the agent worked out unaided that it belonged to task 1 and attached it via `resolve_unmatched` — recorded with no `sent_at`, so an unsolicited update stays distinguishable from a ping-and-reply pair. **This closes the exact gap identified back in Step 4, before the tooling to handle it existed.** The agent also left the status at `blocked` rather than flipping it to something more optimistic, which is the right read: finance delivering in two days does not unblock anything today.

**5. Digest sent.** The digest run produced: *"Digest sent to Marcus. One item flagged: the vendor contract task is overdue and blocked at 0% progress — no other blockers or unresponsive team members today."* Its schedule correctly reset to 01:00 UTC (09:00 Singapore) afterwards, so pulling it forward for the test did not disturb the real cadence.

**Caveat on the test data:** the same person is registered as both manager and task owner, so the digest went to the person being chased. That is an artefact of a one-person test, not of the design.

### Operational requirements

The system runs unattended, but depends on all of these being up: **LM Studio** with the model loaded (it was silently unloaded at one point, and every run simply failed), **Docker Desktop**, the **Kubernetes cluster**, and the **Odysseus containers**. If `pm-chaser` code changes, the image must be rebuilt and re-imported into the cluster by hand — there is no registry.

---

## 2026-08-05 (later) — Separating the PM from the employee, and one-tap onboarding

Until now a single person was registered as both manager and task owner, which made the digest and the chase indistinguishable in testing. Restructured to two real people on two real Telegram accounts:

- **Marcus** — `role=manager`, linked to the account that created the bot. Receives digests and escalations. Assigns work.
- **Daniel** — `role=team_member`, linked to a separate personal account. Owns the tasks and gets chased.

**Why two accounts are genuinely required:** `Person.telegram_chat_id` is unique, because incoming messages are matched to a person *by* their chat id. One Telegram account can therefore only ever be one person in the system — sharing it would make replies unattributable. (If only one account had been available, the workable fallback was to link the employee and leave the manager unlinked, reading the digest from the Odysseus run output instead.)

**Added Telegram deep links.** Onboarding previously meant the PM relaying a code out-of-band — "message @pmchaser_… and type /start A7K2QX". `register_person`, `list_people` and the chase plan's `unreachable` list now also return a `link_url` of the form `https://t.me/<bot>?start=<code>`. Tapping it opens the bot and sends the code automatically, so the PM pastes one link instead of explaining a procedure. The bot's username is read from Telegram's `getMe` and cached rather than configured, so it cannot drift out of sync with the token in use.

**Some out-of-band first contact is unavoidable**, and worth understanding rather than trying to engineer away: Telegram forbids a bot from messaging anyone who has not messaged it first. The person must always initiate. The deep link reduces that to a single tap; it cannot remove it.

**Correction to an earlier claim in this document.** It was previously stated that reconnecting the MCP server in Odysseus is only needed when the *tool list* changes. That is wrong, and it cost a confusing failed run: the MCP connection is a live session bound to a specific pod, so **any pod restart orphans it**, schema change or not. The symptom is clean at least — the agent reported *"the pm-chaser MCP server is returning 'Session terminated' on every call"* and said what to do about it, rather than failing silently. **After every `kubectl rollout restart`, click Reconnect in Odysseus → Settings → Integrations → MCP.**

**Verified with the new setup:** a chase run messaged **Daniel only** (chat `482910573`), about the single most overdue task; tasks #2 and #3 were correctly left alone by the one-task-per-person rule; **Marcus received nothing**. The message Qwen composed — *"it's about 1 day overdue (deadline was yesterday evening)"* — turned `-26.0` hours into both a duration and a wall-clock reference, using the local-time field rather than reciting a number.

**Known ordering detail:** `get_digest_data()` does not poll Telegram — that is the chase run's job. So the digest reflects task state as of the last chase run, which is at most 30 minutes stale. Fine in practice, but worth knowing when testing the two in sequence: run the chase first if you want a reply reflected in the digest.

**PM task entry via Odysseus chat: verified working.** The open worry was that chat's 71-tool surface — the environment where the chase run originally failed — would make task creation unreliable too. It does not: *"Create a task for Daniel to review the vendor SOW, due Friday 5pm. High priority."* produced a correctly assigned, correctly prioritised task due **Friday 07 Aug 17:00 local**. That confirms the earlier reasoning: the big tool surface breaks *multi-step procedures*, not single one-shot commands. It also confirms the timezone pitfall in the Skill is doing its job — the model sent the deadline without an offset and the server read it as local time, rather than the model "helpfully" converting to UTC first and landing it at 1am Saturday.

---

## 2026-08-05 (later still) — Blocked tasks go to the manager, not back to the employee

A gap surfaced from watching real use rather than from testing. Daniel replied that the vendor contract was *"blocked, problems with finance, will need two more days"*, which correctly set the task to `blocked`. But its deadline was still in the past, so it stayed permanently overdue — meaning the bot would keep chasing him every four hours about something he had already explained and could not fix.

Three options were considered: chase blocked tasks far less often; stop chasing them and surface them to the manager instead; or let the agent extend the deadline when someone asks. **The third was rejected on principle — moving a deadline is the manager's call, not the employee's and certainly not the bot's.** The second was chosen: a blocker is *information for the manager*, not a reason to nag the person who is stuck.

**What changed:**
- `get_chase_plan()` now excludes any task with status `blocked`, recording it in `skipped` with the reason *"blocked — needs the manager to unblock or reschedule, not the owner to be chased again"*.
- `get_digest_data()` gained a `blocked_needing_decision` list carrying, for each: the owner, the deadline, whether it has **already passed**, and `reason_given` — the person's own words.
- The digest Skill now instructs the agent to name who is blocked, quote their reason, and **recommend a concrete next step** (extend, reassign, or clear the blocker), calling out explicitly when the deadline has already gone.

**A related gap fixed at the same time:** the digest Skill already said "name the specific blockers people reported", but `get_digest_data()` never returned any reply text — so the agent literally could not comply. `_task_dict` now includes `last_reply_text` and `last_reply_at_local`, being the most recent thing a person actually said about a task, whether it answered a ping or arrived unprompted.

**Verified.** Before the change, the digest said only *"the vendor contract task is overdue and blocked at 0% progress"*. After it:

> **Blocked** — Daniel's vendor contract task is overdue and blocked by finance issues; needs a decision on deadline extension or reassignment.
> **At risk** — Daniel's Q3 board deck is due in ~5 hours with 0% progress.

The second line is worth noting: **nothing in the data marks that deck as "at risk"** — there is no such flag, threshold or rule. The model saw "due in 5 hours, 0% progress" and made the judgement itself. That is precisely the call deliberately *not* hardcoded into `get_digest_data()`, and it is doing real work.

---

## 2026-08-06 — Chase runs were taking ~3 minutes; fixed to ~20–40 seconds

Not a `pm-chaser` change — nothing in this repo, no redeploy, no Reconnect. The cause was entirely in how LM Studio had the model loaded.

**Measured, not assumed.** `lms ps` showed the model loaded with `parallel: 4` and a ~120k-token context reservation. A direct benchmark against LM Studio's API, bypassing Odysseus entirely, confirmed generation at **2.32 tokens/sec** — on an M4 Pro, a 16GB 4-bit model should manage roughly 15-17 tok/s (memory-bandwidth-bound: every token requires reading the whole model, and this chip does ~273 GB/s). The GPU was confirmed in use (near-0% CPU during generation, and the model format is MLX, which is GPU-only by construction) — the problem was configuration, not hardware or an offload failure.

**The fix:** `lms unload qwen3.6-27b-mlx && lms load qwen3.6-27b-mlx -c 32768 --parallel 1 --gpu max --speculative-draft-mtp -y`. `parallel: 4` provisions the model to serve four simultaneous requests — a batched-serving mode with real overhead — when the agent only ever makes one call at a time. Dropping to `parallel: 1` was the change that mattered; `--speculative-draft-mtp` (multi-token speculative decoding, free if the model supports it) and the smaller context reservation were included but not isolated as separately responsible.

**Verified after reload:** 14.5-14.7 tok/s plain generation, 12.2 tok/s with a tool schema attached — roughly 6x the original speed, consistent across three separate test loads.

**One loose end, left unresolved deliberately:** `lms ps` continued reporting `contextLength: 119552` regardless of what `-c` was given, including on a fresh model identifier with `-c 8192`. Since the metric that actually matters — throughput — moved and stayed moved, this is being treated as a stale/cosmetic reporting field in this LM Studio version rather than a real problem, rather than spending further time on a number that doesn't affect behavior.

**Operationally important: this does not persist.** It is a runtime load setting, not a saved config. If LM Studio restarts or the Mac reboots, the model will very likely reload with its previous defaults (`parallel: 4`) and the slowdown returns. If chase/digest runs are ever slow again, check `lms ps` for `parallel` before assuming anything else is wrong.

---

## 2026-08-07 — Diagnosed why "fixed" runs were still slow, found the real cost was Odysseus, moved the two scheduled jobs off it entirely

Started as a follow-up speed investigation, ended as an architecture change. Full chain, in order:

**Runs were silently doing nothing.** Checking a "successful" `TaskRun`, the model's own log showed `tools_sent=2` — only the two built-in `ask_user`/`update_plan` tools, none of the real `pm-chaser` tools — despite the crew allowlist being correct. Root cause: the `pm-chaser-mcp` LoadBalancer IP had drifted again (`172.19.0.5` → `172.19.0.3` → `172.19.0.2` across the session, on ordinary pod restarts, not full cluster rebuilds), and separately, Odysseus's external MCP connection doesn't survive its own process restart — it has to be manually reconnected every time, and does **not** auto-reconnect on startup the way its built-in stdio tools do. Both had to be manually fixed (update the stored URL, click Reconnect) more than once in one session as Docker Desktop itself restarted underneath everything.

**Measured where the time actually goes, with real `[agent-timing]` log data.** A clean 3-round chase run: round 1 (`get_chase_plan()`, zero arguments, zero judgment) took 66.8s, of which 61.4s was spent on nothing but "thinking" before the tool was even called. Captured the actual reasoning trace directly from LM Studio (`reasoning_content` in the API response) and it was the model **restating all 7 remaining procedure steps** before doing step 1, which had already been stated verbatim as the very next action. Round 2 (the actual judgment — composing messages, deciding escalations) only spent 6-8s thinking by comparison. The cost was backwards from what the step's difficulty would predict.

**Tried to disable thinking — confirmed, not just assumed, that nothing on this stack allows it.** Three separate mechanisms tested directly against LM Studio's API (`/no_think` in the prompt, `chat_template_kwargs.enable_thinking: false`, top-level `enable_thinking: false`) — none reduced reasoning tokens beyond normal run-to-run noise. Checked LM Studio's UI (Developer settings, per-model load settings, Model Defaults) — no reasoning/thinking toggle exists anywhere in it either. Checked Odysseus's own `llm_core.py` — it only ever sets `reasoning_effort` for two hardcoded cases (OpenAI GPT-5.x, Mistral), never for local models, and there's no generic pass-through setting. This is a real, confirmed limitation of this model + LM Studio combination, not a missing config.

**Found the actual lever: assistant-message prefill.** Seeding the conversation with a trailing assistant turn ("Calling get_chase_plan now, no further analysis needed.") before the model's turn caused it to skip reasoning entirely and jump straight to the correct tool call — same result, 257 reasoning tokens → 0, ~26s → ~3.6s for that round, tested directly against LM Studio. Only safe to apply to the one round that's genuinely mechanical (the opening `get_chase_plan`/`get_digest_data` call); deliberately **not** applied to later rounds, where real judgment happens.

**Checked whether Odysseus could use this — it can't.** `agent_loop.py` has a `forced_tools` parameter that would force a specific tool call without the model reasoning its way there, which is functionally the same fix through Odysseus's own supported path. But the only code that ever populates it is `chat_routes.py`, hardcoded to forcing web-search tools in chat; `task_scheduler.py`'s call into the agent loop never passes it, and there's no `ScheduledTask`/`CrewMember` field for it. Using it for the chase job would mean editing Odysseus's own tracked source, which stays off the table.

**Decision: moved the two unattended jobs (chase, digest) off Odysseus entirely.** Not "replace Odysseus with LM Studio" — LM Studio was never anything but the model engine underneath, unchanged in both pictures. What moved is *who drives the conversation with it*: previously Odysseus's `agent_loop.py` (scheduler + tool-calling loop + MCP client, all general-purpose and none of it configurable enough to use the prefill fix or a forced first tool), now a small dedicated script under our own control. **Odysseus's role is unchanged for the one thing it's still good at** — Marcus creating/editing tasks by chatting with it normally. Only the unattended half moved.

### New component: `agent/run.py`

Talks directly to LM Studio (`http://localhost:1234/v1/chat/completions`) and directly to `pm-chaser-mcp` (via the real `mcp` client SDK, `streamable_http_client` + `ClientSession` — same pattern as `test_wire.py`), driven by `launchd` instead of Odysseus's `ScheduledTask`. The chase/digest skill *content* didn't move or change — `SKILL.md`'s Procedure/Pitfalls sections are parsed directly into the system prompt, so the tuning already done stays the single source of truth.

- **Prefill trick applied to round 1 only**, per job (`get_chase_plan` for chase, `get_digest_data` for digest) — the proven, safe win. Never applied to later rounds.
- **`pm-chaser-mcp` reached via `kubectl port-forward`, not the LoadBalancer IP.** Discovered the hard way: the LoadBalancer IP (`172.19.0.x`) only routes from *inside* Docker Desktop's VM — it's not reachable from a process running directly on the Mac host (a plain `ConnectTimeout`, first real test). `kubectl port-forward` proxies through the Kubernetes API server instead (already known to be exposed to the host on 6443), and as a bonus this **structurally eliminates the LoadBalancer-IP-drift bug** that caused several silent failures earlier tonight and last session — port-forward always targets the service by name, never a cached IP. The script starts and tears down its own port-forward per run.
- **Found and fixed a real silent-failure bug during testing, before it ever ran unattended.** First live digest test: round 2 spent 230s and 10,692 characters reasoning about a genuinely complex digest, then returned **zero tool calls and empty content** — `max_tokens: 3000` was too low, and reasoning tokens count against that budget, so the response was cut off before the model ever reached its tool call. The run would have logged "done" and exited 0, having silently sent nothing — precisely the failure class this whole investigation started from, just reproduced in new code instead of Odysseus's. Fixed two ways: raised `max_tokens` to 8000, and — more importantly — an empty final turn (no tool call *and* no text) now raises an error instead of being treated as a clean finish. Re-tested clean after the fix.
- **Verified end-to-end twice**, both via manual invocation and via `launchctl start` (the real trigger path, not just us calling the script by hand) — real Telegram sends confirmed by real `telegram_message_id`s from Telegram's own API (52, 53, 54).

### Also fixed today, smaller

- **`SKILL.md` recap trimmed** (`task-chaser` v3.1.0, `task-digest` v2.1.0): both skills previously ended by asking the agent to "report briefly what you sent" — a full narrative recap nobody reads on an unattended run, and its own dedicated LLM round. Now both close with one short line only. Measured saving on that round alone: 46.0s → 14.9s. This fix lives in the skill files themselves, so it benefits both the (now paused) Odysseus path and the new `agent/run.py` path equally.
- **`parallel` reset itself back to `4` twice more this session**, unprompted — not tied to an explicit reload each time, seemingly drifting on its own on a timescale of tens of minutes to an hour. Confirms the "does not persist" note above is an understatement; check `lms ps` liberally, not just after a known restart.

### Tried a different model: `qwen/qwen3.6-35b-a3b` (MoE, ~3B active params)

Not yet made the live default — still being validated — but the results so far are strong enough to record. On Apple Silicon, inference is memory-bandwidth-bound (every token costs however many parameters have to be read), so a MoE model with only ~3B *active* parameters per token should generate far faster than the dense 27B model regardless of total size (35B). Confirmed: **round 1's identical request (no prefill trick) dropped from ~26-60s to 4.68s** — roughly 5.5x — and a full real chase run that used to take 70-150s completed in **11.5-24.8s**. Total params (35B) still have to be loaded into memory (~20GB, comparable to the 27B model's ~16GB), so this isn't a free lunch on RAM — but it is one on speed.

**The catch, exactly as predicted before testing:** MoE's smaller active-parameter budget shows up as *worse judgment*, not worse tool-calling — mechanical correctness held up fine throughout. First real test: given 3 overdue tasks to escalate, it sent **three separate robotic messages** instead of the one combined, natural-sounding message the 27B model always produced. Root cause: the skill said "send one message... naming the task" (singular) — the 27B model correctly generalized this to "batch multiple tasks into one message"; the 35B-A3B model took it more literally. **Fixed** by rewriting step 6 to state explicitly, with a real sent message as a concrete example: combine everything in `to_escalate` into one message, never one per task. Re-tested clean immediately after.

**A second, smaller literalism bug surfaced right after:** the recap-trim fix from earlier (step 9) had used a fictional example — `"e.g., 'Sent to Daniel and Priya, escalated 2 to Marcus.'"` — and the model echoed the *names* from the example into a run where nobody was actually chased, producing a factually wrong sign-off. Same root cause as the escalation bug: this model pattern-matches provided examples more literally than the 27B model did. Fixed in two passes — first to stop copying names (still left an awkward but no-longer-false "Sent to Marcus, escalated 3 to Marcus"), then to stop forcing both clauses into one sentence when only one applies. Confirmed clean on the third try: `"Escalated 3 tasks from Daniel to Marcus."`

**Lesson for any future model swap:** a smaller/faster model doesn't fail by refusing to act or breaking tool-calling — it fails by taking instructions more literally than the previous model did, especially anywhere the skill relied on the model correctly generalizing past an example's specific wording. Test the exact scenarios where the skill gives a concrete example, not just the happy path.

**Also added:** `agent/run.py`'s `parse_skill()` now includes the SKILL.md `Verification` section in the system prompt as an explicit pre-sign-off self-check — previously excluded (inherited from Odysseus, which never auto-injects it) purely because every extra prompt token cost real time under the old, slower model. That tradeoff no longer applies at this speed, so it's now free insurance against exactly this class of mistake.

### Three further improvements, once speed was no longer the binding constraint

1. **`to_escalate` grouped by owner at the source, not left to the model to notice.** The escalation-splitting bug above was fixed with a skill instruction, which is weaker than it looks — `to_chase` was already immune to the same mistake because Python pre-deduplicates it to one entry per person; `to_escalate` didn't get the same treatment. Fixed in `server/tools.py`'s `get_chase_plan()`: `to_escalate` is now `[{"owner_name": ..., "tasks": [...]}]`, grouped before it ever reaches the model — matching the project's own stated principle, "code decides WHAT and WHO." Deployed (rebuilt image, `kubectl rollout restart`) and verified directly via a real MCP call before touching anything downstream. Skill wording simplified accordingly (v3.5.0) since the model no longer has to infer the grouping.

2. **Telegram failure notification** (`notify_failure()` in `agent/run.py`) — every failure mode found tonight (truncation, orphaned MCP session, dead LM Studio) used to just sit in a log file. Now any real failure sends the manager a Telegram message via `list_people(role="manager")` + `telegram_send_message`, closing the loop for good on silent failures. Tested by forcing a real, clean failure (`MAX_ROUNDS=0`) through the actual `main()` entrypoint — confirmed a real send (`telegram_message_id: 71`), not just "no exception was raised."
   - **Found and fixed a related design gap while testing this:** a digest run where round 1 successfully sent the digest, then round 2 (the closing sign-off) spiraled into 29,309 characters of reasoning and hit the token cap without producing output — a real, if intermittent, failure (reproducing it again immediately came back clean, 20.6s). Without a fix, this would have fired a "run failed" alert to Marcus even though his digest had already correctly arrived. Added `action_taken` tracking: an empty final turn only raises a hard failure if no real action (a send, an update) happened yet this run; if the actual work already succeeded, it's logged as a non-fatal oddity instead.

3. **Round 1 dropped from the LLM loop entirely.** `get_chase_plan`/`get_digest_data` are zero-argument, zero-judgment calls — the prefill trick made the model *skip thinking* about deciding to call them, but it still nominally owned that decision. `agent/run.py` now calls the first tool directly in Python before the model ever runs ("round 0"), and starts the LLM conversation already holding the result. Removes the prefill-hack code entirely; the trigger messages were reworded to say "already been called for you" so the model doesn't try to call it again (which the skill's own pitfalls already forbid).

All three re-verified end-to-end with real Telegram sends after implementation, using the `qwen/qwen3.6-35b-a3b` model. `agent/run.py`'s live `MODEL` constant is still the 27B model — switching it to 35B-A3B remains a deliberate, separate decision, not yet made.

### Current state

- Odysseus's `Task chase check` and `Daily task digest` `ScheduledTask`s: **paused** (`status='paused'` in the DB) — left in place, not deleted, in case of rollback.
- `launchd`: `com.pmchaser.chase` (every 30 min) and `com.pmchaser.digest` (daily 09:00, Mac's local time is already UTC+8/Singapore) — both loaded, both verified via a real `launchctl start`. Logs at `agent/logs/chase.log` and `agent/logs/digest.log`.
- Odysseus keeps running, scoped to manager task creation only.

---

## 2026-08-07 (continued) — Model swap made live and committed; built a second Telegram bot so the manager can create tasks without Odysseus

### Model swap finalized, first commit

`agent/run.py`'s `MODEL` constant switched from the 27B dense model to `qwen/qwen3.6-35b-a3b`, made the live default after one more clean real-world test (Docker Desktop had gone to sleep mid-test; reopened, pod auto-restarted, LoadBalancer IP drifted as expected — irrelevant to `agent/run.py`'s port-forward approach, which doesn't care). All of the session's changes up to that point committed as `6c8846a` — the first commit of this build, after being deliberately left uncommitted per the standing rule (never commit without being asked).

### Kubernetes-vs-Docker question, resolved

Asked directly: is Kubernetes actually needed here, or would plain Docker containers do? Answer given: Kubernetes's only unique contribution (Service/LoadBalancer networking) was the direct cause of the LoadBalancer-IP-drift bug; everything else it offers (restart policy, rolling updates, declarative config) is available in plain Docker too at this scale. Then reconsidered on two grounds: (1) removing Odysseus removes the IP-drift bug's actual trigger (the stale cached URL lived in Odysseus, not in Kubernetes itself) regardless of which container runtime is kept; (2) the user wants to **showcase Kubernetes skills for career/portfolio purposes** — a legitimate goal this project has already generated real material for (diagnosing the LoadBalancer/NodePort/ClusterIP networking differences under Docker Desktop, the IP-drift bug itself, deploying without a registry). **Decision: keep `pm-chaser-mcp` on Kubernetes.** Optional future polish if pursued further: liveness/readiness probes and resource requests/limits, neither of which exist on the Deployment yet.

### New component: `agent/pm_bot.py` — the manager's own Telegram bot, replacing Odysseus's last remaining job

Odysseus's only remaining role was Marcus creating/editing tasks by chatting with it. Built a standalone replacement: a **second, dedicated Telegram bot** (`@taskmanager_threeporkchops_bot`, separate token) that Marcus talks to directly, with real multi-turn conversation memory — unlike `run.py`'s chase/digest jobs, which fire once and exit, every message here is a genuine back-and-forth ("add a task for Daniel" → "what's the deadline?" → "Friday 5pm").

**Key design decisions:**
- **No `/start`-with-code linking flow needed.** In Telegram, a private chat's `chat.id` is the user's own numeric Telegram ID, not bot-scoped — confirmed for real before writing any code (messaged the new bot from Marcus's account, checked `getUpdates`, got back the same `chat_id` — `8713903593` — already on file from the employee bot). Supplied via `MANAGER_CHAT_ID` in `agent/.env` rather than resolved through `list_people()`, which deliberately never exposes the raw `telegram_chat_id` (only `is_linked`/`link_code`/`link_url`) — a real gap between the original plan and what the tool layer actually returns, caught before writing the lookup code.
- **This bot's Telegram send/receive never goes through the pod.** `server/telegram_client.py` was refactored from a module-level singleton into a `TelegramClient` class (existing module functions kept as thin delegations — zero behavior change for `server/tools.py`, verified with `test_tools.py`/`test_wire.py` before redeploying); `pm_bot.py` imports it directly and holds its own token. Only the five task/people tools (`create_task`, `list_tasks`, `update_task`, `register_person`, `list_people`, later `delete_person`) go through MCP. Means listening for new messages has zero dependency on Kubernetes being healthy — only *acting* on one does, and that fails loudly in-channel ("having trouble reaching the task system") rather than silently.
- **Conversation memory is in-process only** (`ConversationState`, turn-based, trimmed whole-turns-only so a `tool` message is never separated from its `assistant` tool_call), reset after 30 minutes of inactivity, lost on crash/restart — accepted tradeoff, losing an in-flight exchange just means re-sending it.
- **Port-forward started once at startup and monitored**, not per-message like `run.py`; MCP session reopened fresh per message (a long-lived one would silently orphan on a pod restart, the same failure mode already documented for Odysseus). One retry per message before apologizing in-channel.
- **`delete_task` deliberately excluded.** Excluded going in, on the reasoning that pairing an irreversible no-confirmation delete with a freeform chat surface — on a model already shown to take instructions more literally under ambiguity — was the wrong risk combination.

**A caught-live regression during the `telegram_client.py` refactor:** the original `get_bot_username()` swallowed a missing-token error and returned `None`, which `build_link_url()` (and `test_tools.py`'s no-token local runs) depended on. The naive refactor resolved the token eagerly at the wrong layer, breaking that tolerance and crashing `test_tools.py`. Fixed by keeping the eager/lazy split at the same boundary as the original: `send_message`/`get_updates` still raise immediately on a missing token, only `get_bot_username`/`build_link_url` tolerate it.

### `delete_person`, added mid-build, with an enforced confirm-first rule

While testing, a mistaken registration ("register Stacy") couldn't be undone — there was no unregister tool at all (a known, pre-existing gap, already listed under "Deferred" before tonight). Added `delete_person(name)` to `server/tools.py`: refuses if the person owns any task, open or closed (reassign/delete those first) — keeps it limited to genuine "wrong person, nothing built on them yet" cleanup, never a way to lose task/check-in history. The tool layer enforces what's *safe*; a new skill rule enforces that **a human actually agreed** — `task-manager`'s skill explicitly forbids calling `delete_person` on the first ask, requiring an explicit yes first, since it's the one irreversible, no-undo action available to this bot. Covered in `test_tools.py`; rebuilt and redeployed (bundled with the `telegram_client.py` refactor in one rollout).

### Bugs found and fixed during real testing (all via actual Telegram messages, not synthetic tests)

1. **A digest run narrated sending instead of actually sending** — the model wrote out the full digest text plus a closing "Digest sent to Marcus." line with **zero tool calls**, exactly the failure Pitfall #1 already warned about, just not one the existing safety net caught (`action_taken` only guarded an *empty* final turn, not a confident-but-false one). Fixed two ways: (a) structural — `requires_send: True` on the digest job in `agent/run.py`; a run ending without `telegram_send_message` ever succeeding is now always a hard failure regardless of what the model's closing text claims; (b) a corrective nudge — rather than discarding the digest content the model already composed, inject a message telling it to actually call the tool with that same content and give it one more round, before the hard-failure check becomes the final backstop. Recurred once more after the first (skill-only) fix, confirming prompt wording alone isn't reliable for this model — the structural fix is what actually holds. `telegram_send_message` was called and confirmed sent on the retry.
2. **Deadline month/day mix-up**: "11 Aug" was converted to `2026-11-11` (November), not August — the "11" was reused for both month and day, "Aug" ignored entirely. Real task deadline corrected live via `update_task`. Fixed in the skill with an explicit two-step rule (find the month name first via a literal lookup table, the leftover number is always the day) plus a concrete worked example — re-tested clean on the next real "11 Aug" a few minutes later.
3. **`register_person` replies omitted the actual link.** The skill's "confirm briefly" instruction led the model to reply "Owen is now registered" without the link Marcus needs to forward — silently breaking onboarding, since Telegram won't let the bot message Owen first. Fixed with an explicit exception: always include the real `link_url`/`link_code` verbatim after a successful `register_person`.
4. **Multi-owner ambiguity, refined mid-testing.** Originally: any request naming multiple owners creates one task per person, same title, confirm-after. Correct for "assign Daniel and Priya to finish the deck" (one clear task, two names) but wrong for "create tasks for Priya and Daniel" (no shared description — could mean one duplicated task or two unrelated ones). Skill now branches: proceed without asking only when one task description clearly covers everyone named; ask first when it's genuinely ambiguous which was meant.

### Verified working end-to-end, all via real Telegram messages

Incomplete create request → clarifying question, not a guess. Unregistered owner named → refused to invent them, asked to register. Full register → create-task → deadline-set → list → digest-in-chat conversation, multi-turn memory holding correctly across every step. Pod restart mid-conversation (from the `telegram_client.py`/`delete_person` redeploy) → `pm_bot.py`'s own port-forward died and auto-restarted without crashing the listener, confirmed by a direct tool call moments later. `delete_person` exercised for real twice (Owen registered, then cleanly deregistered once his tasks were cleared first).

### Current state / what's left

- `pm_bot.py` has only been run manually in the foreground for testing — **not yet installed as a `launchd` KeepAlive service.** That's the next concrete step before Odysseus can be decommissioned.
- Database reset twice during testing (all tasks/check-ins/unmatched messages cleared via direct DB access on the pod; people left untouched per instruction) — treat current task data as fresh test data, not historical.
- Odysseus is still running and still capable of creating tasks — deliberately not decommissioned yet. Per the user: only do that once `pm_bot.py` is verified fully working, which is close but not yet declared done (the `launchd` install + a longer unattended stretch are the remaining bar).

---

## 2026-08-12 — Migrated to Hermes Agent; Odysseus decommissioned

Hermes Agent (`github.com/NousResearch/hermes-agent`, AGPL) was found already installed and running on this Mac — an existing "DJ Fatty" Telegram bot on its `default` profile — rather than deliberately set up for this project. Model-agnostic, so it reuses the same local LM Studio backend already in use. Trial-migrated pm-chaser onto it via two new, fully isolated profiles rather than continuing with the hand-rolled `agent/run.py`/`agent/pm_bot.py` pair:

- **`task-manager-bot`** — replaces `pm_bot.py`. A real, native Telegram gateway (own bot token, `TELEGRAM_ALLOWED_USERS` restricted to the manager's chat ID) with genuine multi-turn conversation, driven by a `SOUL.md` instructions file (Hermes's equivalent of the old `SKILL.md`).
- **`pmchaser-bot`** — replaces `agent/run.py`. Hosts the scheduled `task-chaser`/`task-digest` cron jobs. No Telegram gateway of its own — sends go through `pm-chaser-mcp`'s own chase-bot token via MCP tool calls, same tool surface `task-manager-bot` also uses.

**Odysseus decommissioned** — data preserved, fully reversible, but no longer running as part of this project.

**Real gotchas found migrating, worth knowing if this pattern is ever repeated:**
- Docker Desktop's Kubernetes had a **separate image cache** from plain `docker build` under `UseContainerdSnapshotter: true` — a rebuilt image silently kept running stale code until a privileged debug-pod workaround forced a real update.
- Both profiles needed `memory.memory_enabled`/`user_profile_enabled: false` and `nudge_interval: 0` — Hermes's own self-improvement background review otherwise wrote stray skill/memory files unprompted.
- `task-manager-bot` additionally needed `display.interim_assistant_messages: false`, or mid-turn tool-retry narration ("I need to pass the parameters inside an arguments object") leaked to the manager as visible messages.
- **Editing `SOUL.md`/skills mid-conversation does not retroactively apply to an already-open session** — only `/reset` or a new conversation picks up file edits. (Refined further on 2026-08-14 — see the reliability-crisis entry: this turned out to be true for prompt files, but a *config.yaml* change needs an actual process restart, not just a `/reset`.)

---

## 2026-08-14 — Migration to Hermes finalized; old system deleted for good

Treated as final, not a trial: `agent/run.py`, `agent/pm_bot.py`, `agent/common.py` **deleted from the repo** (git history still has them if ever needed), and all three old `launchd` jobs (`com.pmchaser.pmbot`, `com.pmchaser.chase`, `com.pmchaser.digest`) unloaded **and their plists removed** from `~/Library/LaunchAgents/`.

**A real incident that motivated deleting rather than just unloading**: after a Mac reboot, the "retired" `com.pmchaser.pmbot.plist` silently reloaded on its own (`launchctl unload` does not persist across a reboot — it only affects the current session) and started polling Telegram with the **same bot token** `task-manager-bot` uses, causing a real `Conflict: terminated by other getUpdates request` that broke `task-manager-bot`'s gateway for several minutes. **Lesson for any future `launchd` job meant to stay permanently disabled**: `launchctl unload` alone is not durable — either `unload -w` (persist-disable, keeps the plist for a later `load -w` rollback) or delete the plist outright.

---

## 2026-08-14 — Migrated `pm-chaser-mcp` off Kubernetes to plain Docker

After the `kubectl port-forward` tunnel (the connection method `agent/run.py`/`agent/pm_bot.py` had settled on back on 2026-08-07) died a third time — this time from a Mac reboot, costing real Telegram messages during the instability — asked directly: is Kubernetes still earning its keep? Walked through the honest tradeoff: Kubernetes's real value (self-healing across *multiple* nodes, scaling, rolling deploys) needs multiple machines or variable load to matter; on one Mac mini with low traffic, none of that applies, and the one thing actually being used — restart-on-crash — plain Docker gives for free. This reverses the 2026-08-07 decision to keep Kubernetes for portfolio value; the user chose to migrate off it this time.

**What changed:**
- `pm-chaser-mcp` now runs as `docker run -d --name pm-chaser-mcp --restart=always -p 18173:8000 -v pm-chaser-data:/data --env-file server/.env pm-chaser-mcp:local` — same image, same effective port (`18173→8000`, chosen to match what Hermes already had configured, so **zero config changes needed on either Hermes profile**).
- The PVC (backed by `rancher.io/local-path` — i.e. already just a directory on the Docker Desktop VM, no real network storage) replaced by a Docker named volume (`pm-chaser-data`). Data migrated via `kubectl cp` out, `docker cp` in; verified row counts matched before and after.
- `server/.env` gained `PM_CHASER_TZ=Asia/Singapore` (previously supplied by a Kubernetes `ConfigMap`, now needs to live in the same env file as the bot tokens since there's no ConfigMap anymore).
- The `kubectl port-forward` process was killed — no longer needed at all, Hermes connects directly to the container's published port. This eliminates the entire IP-drift/tunnel-death failure class this project had been fighting since 2026-08-05, not just makes it more durable.
- Old Kubernetes Deployment scaled to 0 replicas, **not deleted** — Service/PVC/ConfigMap/Secret all still exist if this decision is ever revisited (though the Docker volume would be the authoritative copy of the data by then, not the stale PVC).

**One real gap, deliberately left open**: Docker Desktop itself has `AutoStart: False`, so even with `--restart=always` on the container, nothing comes back automatically after a Mac reboot until Docker Desktop is manually opened first. Offered to fix via a macOS Login Item; **user declined, prefers opening Docker Desktop manually after a reboot**. This is now the one manual step in an otherwise self-healing stack.

---

## 2026-08-14 — Task priority required; on-demand chase and digest added

`create_task` no longer defaults priority — it's now a required argument (`low`/`medium`/`high`; the DB's old `"normal"` middle tier renamed to `"medium"`, a clean-slate rename with no migration needed since task data was wiped alongside it). Priority now directly drives the scheduled chase's re-ping floor: `_PRIORITY_CHASE_FLOOR_HOURS = {"high": 1, "medium": 6, "low": 24}` in `server/tools.py`, replacing the old flat 4-hour floor for everyone. The scheduled chase's own cron interval was tightened from 30 to **15 minutes**, so the hourly high-priority floor lands within 15 minutes of the true mark instead of up to 30.

**New tool: `chase_now(owner_name)`** — a full manual override for `task-manager-bot`, bypassing both the re-ping floor and the due-soon eligibility window (an explicit "chase Daniel now" already means the manager decided the timing, not the automated sweep's business). Returns every open task for that person, not just the single most urgent one, unlike the scheduled sweep. **On-demand digest** needed no new tool — `get_digest_data()` already covered it; the only difference is the manager is already in the conversation, so the answer is the model's normal reply rather than a `telegram_send_message` call.

Three real bugs caught live during this build, all fixed the same day — see the reliability-crisis entry further down for the broader pattern these turned out to be an early instance of:
1. "send chase now" with no name given → the model inferred a target from earlier conversation instead of asking. Fixed with an explicit "ask, don't infer" rule.
2. `chase_now` wasn't a true override at first — it still silently excluded tasks outside the normal 24h due-soon window. Fixed: an explicit "chase now" bypasses that window too, and includes no-deadline tasks.
3. The model narrated a send ("Pinged Daniel about...") without ever actually calling `telegram_send_message` — the first real instance of the confident-but-false-confirmation pattern that recurred several more times later the same day.

---

## 2026-08-14 — Deadline extensions require the manager, never self-granted

An employee once got a deadline pushed on their own say-so; decided this must never happen automatically — moving a deadline is the manager's call, same principle already established back on 2026-08-05 for blocked tasks. Implemented by reusing the existing `blocked` mechanism rather than adding new state: if an owner's reply asks for more time, the interpreting skill/rule now calls `update_task(status="blocked")` instead of ever passing a new `deadline` — routing it into the same `blocked_needing_decision` digest path already built for real blockers. When the manager approves an extension, `update_task` is called with both the new `deadline` **and** `status="in_progress"` in the same call — approving the extension is also what un-blocks the task, since blocked tasks are permanently skipped by both the scheduled sweep and `chase_now` until their status changes.

**Live-tested for real**: an owner replied "I need an extension to 9pm" to a chase; the scheduled cron correctly resolved an ambiguity (two open check-ins existed at the time) and set the task to `blocked`; the manager then said "approve it," and the bot correctly extended the deadline and un-blocked it in one call.

---

## 2026-08-14 — Multi-manager schema groundwork (not the full feature)

User floated scaling from one manager to several, each with their own employees, some employees potentially shared across managers. Explicit direction: lay groundwork now so adding a second manager later is a quick add-on, but do **not** build the actual multi-manager UX (extra bot profiles, name disambiguation) while there's still only one manager to test against.

**What was built**: `Task.manager_id` — a *second* foreign key to `Person`, separate from `owner_id`, living on the task rather than the person (a person can be a manager on one task and just a contributor on another, and an employee can have tasks under different managers — the shared-employee case). `create_task` auto-resolves it to the sole existing manager when not given explicitly, so today's call sites needed zero changes. `get_chase_plan`'s escalation grouping now keys on `(owner, manager)` instead of just `owner`, so an owner with escalating tasks under two different managers correctly produces two separate entries. Deliberately **not** built: any manager-scoping filter parameter on `get_chase_plan`/`get_digest_data` (today everything still returns the full, unfiltered view — adding a filter is exactly the fast follow-up once a second manager profile exists), and no second bot profile.

**A real regression this caused, found via live testing, not code review**: giving `Task` two foreign keys to `Person` broke SQLAlchemy's ability to auto-resolve which one `Person.tasks` (the reverse relationship) should use — it refused to configure its object mappers at all, silently breaking *every* database-touching tool service-wide (`list_people`, `create_task`, everything), not just anything manager-related. Fixed with one line — `Person.tasks = relationship("Task", back_populates="owner", foreign_keys="Task.owner_id")` — verified via `configure_mappers()` before redeploying. **Lesson for any future schema change adding a second FK between two already-related tables**: `foreign_keys=` needs setting explicitly on *both* sides of the relationship, not just the side being directly edited — SQLAlchemy won't complain until mapper configuration time, which can be well after the responsible edit was made and easy to miss.

---

## 2026-08-14 — Meeting minutes: transcribe/summarize/extract, now from audio, Word docs, PDFs, or pasted text

Built as the first of several "personal assistant" capabilities beyond pure task-chasing (user's original 10-item AI-in-PM checklist had this as priority #1, ahead of action-item tracking which is what the rest of this project already does). Mirrors the existing bulk-file-upload pattern almost exactly: get text from somewhere, summarize it, extract candidate action items (owner via `list_people`, deadline via the same ISO 8601 conversion, a *suggested* priority inferred from the source's own urgency language), one consolidated preview, one explicit yes, then loop `create_task`.

**Sources, and a real premise bug found and fixed**: a native Telegram voice note arrives *already transcribed* into the message text — Hermes's platform layer runs local `faster-whisper` at ingest, before the model ever sees the message. The rule as first written incorrectly told the model to always call a `transcribe_audio` tool itself; live-tested and confirmed via logs that this simply isn't how a voice note reaches the model. Corrected to the real two-case rule: a native voice note needs no tool call at all (already text); a separately *uploaded audio file* (not a voice-note bubble — e.g. a long recording) genuinely isn't auto-transcribed and does need an explicit `transcribe_audio(file_path)` call. Later extended to also accept uploaded Word docs (`docx` skill, copied in from Hermes's own shared skill library — same trust tier as the already-installed `xlsx`/`ocr-and-documents`, not a random registry download) and PDFs (`ocr-and-documents`, already installed), plus plain typed/pasted notes directly in chat, which need no extraction tool at all.

**A real ambiguity resolved between this and the existing bulk-task-creation rule**: that rule already claimed PDF as a trigger format for spreadsheet-style task lists. Adding PDF/Word-doc to the meeting-minutes rule created genuine overlap. Resolved by content, not format: rows/columns of title-owner-deadline routes to bulk task creation; prose/discussion/decisions routes to meeting minutes; ask the manager if genuinely unclear which is meant. Spreadsheets stay unambiguous either way.

---

## 2026-08-14 — The tool-call reliability crisis: root-caused and fixed, not just patched

Across one afternoon, `task-manager-bot` produced three genuinely different wrong-tool-selection failures in quick succession — not one recurring bug repeating, but the model reaching for a different wrong mechanism each time: routing already-directly-callable tools through Hermes's `tool_search`/`tool_describe`/`tool_call` discovery bridge; treating a tool name as an MCP "prompt" via `get_prompt`; calling a semantically wrong tool (`chase_now`) with a missing required argument on a plain "hi." Each produced either 40-70+ second responses, or a confidently fabricated "done" with no real tool call behind it (`set_assistant_name` claimed successful; database unchanged).

**Two real, structural causes found, not "the model is just unreliable":**

1. **`tool_search` was pure unnecessary overhead** for a profile with only ~20 tools total. Traced Hermes's own `tools/tool_search.py`: with the bridge disabled, every tool passes through directly with full schemas, no indirection layer to get lost in. Fixed via `tools: { tool_search: false }` in `task-manager-bot`'s `config.yaml`.
2. **Every conversation was silently loading a 77KB, wildly irrelevant file as project context**: `~/.hermes/hermes-agent/AGENTS.md` — Hermes's *own* contributor documentation, truncated mid-content every single turn, eating roughly 10K tokens of pure noise. Root cause: the global `terminal.cwd: .` setting was resolving into Hermes's own install tree — a known, documented Hermes issue (referenced in its own `prompt_builder.py` source as bug #64590), whose own suggested fix is to set `terminal.cwd` explicitly. Fixed via `terminal: { cwd: ~/.hermes/profiles/task-manager-bot }` in the same `config.yaml` — that directory has no such file, so it correctly loads nothing (this bot has no "project" to load context from).

**Verified with a real before/after**, using `hermes chat -q "..." --profile task-manager-bot` (non-interactive CLI mode) as a fast, isolated test harness — every claim grepped from real `agent.log` entries plus direct database/Telegram-API verification, not the model's own text response. Before: 57-69 second responses, repeated tool errors, a claimed rename that never actually happened. After both fixes: clean single-tool-call turns, 2-11 seconds total, input tokens down from ~42-43K to ~32K per turn.

**A real operational gotcha found applying the fixes**: `task-manager-bot`'s gateway runs under `launchd`, confirmed by Hermes's own refusal to let a bare re-invocation start ("A gateway is already running under launchd for this profile... leaves an orphan dispatcher... can corrupt [the] DB"). The `tools.tool_search` config re-reads per turn (mtime-cached, confirmed via source), but the `terminal.cwd` fix did **not** take effect on the already-running process — nor did a plain conversation `/reset`, since that only clears history, not the process. The correct fix is `hermes gateway restart` (the supervised command), not a shell re-invocation and not a conversation reset. **Lesson for any future config.yaml change to a running profile**: assume it needs a supervised restart to actually go live; verify by checking the running process's start time is *after* the file edit.

---

## 2026-08-14 — `SOUL.md` trimmed, validated against a live test battery

After the two fixes above resolved the acute crisis, reassessed whether `SOUL.md`'s size (176 lines / 33.6KB / ~8,400 tokens at the time) was *also* contributing. Read the full file and found every one of the 34 "Pitfalls" bullets was a near-verbatim restatement of something already stated in a numbered rule, just phrased negatively — genuine duplication, not reinforcement. Consolidated to 13 pitfalls (every distinct warning kept, pure duplicates merged), dropped content describing the now-impossible `tool_search` scenario, and cut one redundant worked example. Net: ~12% smaller.

**Validated, not assumed**: a 5-test battery via the CLI harness, checked against real database state — greeting/persona, full-info task creation (deadline/priority verified correct in the DB), ambiguous multi-owner (correctly asked instead of guessing), `chase_now` with no tasks (correctly reported nothing, no fabricated send), digest, and delete-without-confirmation (correctly asked first, task verified still present afterward). No regression found from the trim. Not a strict scientific comparison, though — the pre-trim version wasn't failing on these same prompts either, since the crisis traced to the two structural causes above, not `SOUL.md` content; the trim's value is efficiency, not the reliability fix itself.

---

## 2026-08-14 — Assistant persona: manager-chosen name (Toby/Abby), live and confirmed

User wanted `task-manager-bot` to have a chosen name and to always address the manager as "boss," with the choice made interactively through the bot itself (not hardcoded once by request) and persisted so it survives conversation resets. Since this profile's own Hermes memory feature is deliberately disabled (see 2026-08-12 gotchas), the choice needed real, separate persistence.

**Built**: `BotState` (the existing single-row settings table, previously only holding the Telegram polling cursor) gained an `assistant_name` column — reused rather than adding a new table, matching the existing "one persistent settings row" pattern. Two new tools, `get_assistant_name()`/`set_assistant_name(name)` — the latter validates strictly against "Toby"/"Abby" and also updates the bot's **real Telegram display name** via a new `set_bot_display_name()` wrapper around Telegram's `setMyName` Bot API method, best-effort (the persona choice still saves even if the cosmetic Telegram-side update fails). New rule 0 in `SOUL.md`: check on first contact, ask if unset, never re-ask once chosen, handle "change your name" as an explicit re-trigger later.

**A genuinely separate token needed discovering**: `task-manager-bot` has its own Telegram bot (`@taskmanager_...bot`), entirely different from `pm-chaser-mcp`'s existing `TELEGRAM_BOT_TOKEN` (the employee-facing chase bot, `@pmchaser_...bot`). To let `set_assistant_name` rename the *manager's* bot specifically, that second token was added to `pm-chaser-mcp`'s own `server/.env` as `TASK_MANAGER_BOT_TOKEN`.

**First two real attempts both failed silently**, turning out to be early instances of the reliability crisis documented above (`set_assistant_name` never actually got called despite a confident claim). **After both crisis fixes, confirmed genuinely working**: `get_assistant_name()`'s database state and Telegram's own `getMyName` API both independently verified a real rename.

---

## 2026-08-14 — Documentation and infrastructure tracking

Recognized a real gap: the two places a human would go to understand this project — this file and `README.md` — were stale (last meaningfully updated 2026-08-07, a week before all of the above), while the two places that *were* current — `~/.hermes/profiles/*/SOUL.md` and `config.yaml` — weren't version-controlled anywhere at all. `~/.hermes` is not a git repository; a disk failure or accidental edit there would have lost the actual, current behavioral documentation of both bots with no recovery path.

**Fixed**: this file rewritten to match current reality (architecture section above) with all prior historical entries kept intact rather than deleted, since they're an accurate record of the reasoning trail even where later superseded. `~/.hermes/profiles/task-manager-bot/SOUL.md` and `config.yaml`, and `~/.hermes/profiles/pmchaser-bot/skills/pm-chaser/{task-chaser,task-digest}/SKILL.md` moved into this repo under `hermes-config/` and symlinked back to their original paths — Hermes reads the same effective files, but they're now tracked, diffable, and recoverable. Deliberately **not** tracked: `.env` files (real secrets), `logs/`/`sessions/`/`skills/.hub` (runtime state, not source), and the bundled marketplace skills (`xlsx`/`ocr-and-documents`/`docx`/`document-to-action-items`) — those are copies of external packages, not this project's own content.
