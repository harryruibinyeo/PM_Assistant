# pm-chaser — Project Management Documentation

This file is the running history of this project: what was done, in what order, and *why* — including the setup work that happened before this repo existed. New entries get added as the build progresses. Anything technical is explained inline so this reads clearly without already knowing the jargon.

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
1. `register_person("Henry", role="manager")` → got a link code.
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

**The symptom.** Asked in Odysseus chat to run the chase check, the model would reason correctly — identify the right task, the right person, the right overdue time — announce "sending the chase message to Jeffrey now", and then not send it. Across one 12-round run it announced the send six separate times. `telegram_send_message` was never called once. It called the read-only tools (`telegram_get_updates`, `list_tasks`) happily and repeatedly, looping on them instead of progressing.

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

> *"Hey Jeffrey, quick check on "Send the vendor contract to legal" — it's been about 4 hours past the deadline and still showing as not started. Are you able to push this through today?"*

Nothing there is templated. It named the task, turned `hours_until_deadline: -4.1` into "about 4 hours past the deadline", noticed the status was `not_started` and worked that in, and asked one question. The check-in was recorded with Telegram's `message_id`, which is what makes threaded replies attributable.

**2. Reply interpreted.** Replying *"This is a blocker as I am waiting on the finance sheet from the finance department"* caused the agent to set the task's status to **`blocked`**. There is no keyword list mapping that phrase to that status — the model read free text and inferred structure, then called `update_task` itself.

**3. No re-ping.** The same run correctly did *not* send another message, because `hours_since_last_checkin` was under the four-hour floor. The rule held.

**4. Spontaneous follow-up resolved.** A second, unsolicited message (*"finance just said that they would do it in 2 days"*) arrived with no ping outstanding. It was stored as unmatched with the reason *"spontaneous update — no ping was outstanding"*, and the agent worked out unaided that it belonged to task 1 and attached it via `resolve_unmatched` — recorded with no `sent_at`, so an unsolicited update stays distinguishable from a ping-and-reply pair. **This closes the exact gap identified back in Step 4, before the tooling to handle it existed.** The agent also left the status at `blocked` rather than flipping it to something more optimistic, which is the right read: finance delivering in two days does not unblock anything today.

**5. Digest sent.** The digest run produced: *"Digest sent to Jeffrey. One item flagged: the vendor contract task is overdue and blocked at 0% progress — no other blockers or unresponsive team members today."* Its schedule correctly reset to 01:00 UTC (09:00 Singapore) afterwards, so pulling it forward for the test did not disturb the real cadence.

**Caveat on the test data:** the same person is registered as both manager and task owner, so the digest went to the person being chased. That is an artefact of a one-person test, not of the design.

### Operational requirements

The system runs unattended, but depends on all of these being up: **LM Studio** with the model loaded (it was silently unloaded at one point, and every run simply failed), **Docker Desktop**, the **Kubernetes cluster**, and the **Odysseus containers**. If `pm-chaser` code changes, the image must be rebuilt and re-imported into the cluster by hand — there is no registry.
