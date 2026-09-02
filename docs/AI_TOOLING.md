# AI & Agent Tooling

What's actually doing the "thinking" in this system, and the design decisions behind each layer.

## The model: a local MoE, never a cloud API

**`qwen/qwen3.6-35b-a3b`**, a mixture-of-experts model, served entirely on-device via **[LM Studio](https://lmstudio.ai)** on Apple Silicon (Metal GPU acceleration), exposed as an OpenAI-compatible endpoint at `localhost:1234/v1`. Both agent profiles point at the same local endpoint.

**Why local, not a hosted API:** every task title, every reply, every check-in this system handles is real operational data about real people — a cloud API call would mean sending that data off-device on every single tool-calling turn, indefinitely, for a system that runs unattended around the clock. Running the model locally makes that a non-issue by construction, at the cost of using a smaller model than a frontier hosted one — which is precisely why so much of this project's engineering effort (below) went into structuring the problem so a ~35B model can execute it reliably, rather than leaning on a larger model to paper over an unstructured prompt.

The model also genuinely has vision (confirmed via its own `config.json` — a real `vision_config` and image tokens, not text-only), used for the photo-of-a-task-list intake path, with `image_input_mode: native` set explicitly after discovering Hermes's default `"auto"` mode was routing images through a lossy text-summarization pre-pass instead of letting the model see actual pixels.

## The agent runtime: Hermes Agent

**[Hermes Agent](https://github.com/NousResearch/hermes-agent)** (NousResearch, AGPL) is the framework running both bots. It provides, per isolated **profile**:

- A Telegram gateway (when the profile wants one) or a headless cron-only mode
- An MCP client, so the profile can call out to `pm-chaser-mcp`'s tools
- **Skills** — markdown files describing a procedure, loaded into context for a specific job
- **`SOUL.md`** — a profile's persistent, always-loaded behavioral instructions (identity, rules, worked examples)
- Scheduled cron jobs, independent of any live conversation
- A configurable toolset — built-in capabilities (browser, vision, file, memory, ...) that can be selectively enabled per profile

Two profiles run here, fully isolated from each other — separate `config.yaml`, separate skills, separate memory, separate Telegram bot tokens — sharing only the underlying LM Studio endpoint and the MCP tool server. See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for how they divide the work.

## MCP: one tool server, two agents

The **[Model Context Protocol](https://modelcontextprotocol.io)** is what lets both Hermes profiles call into the same Python tool server (`pm-chaser-mcp`) over a standard interface instead of each having its own bespoke integration. It's the reason a rule change in one place (e.g., how the re-ping floor is computed) automatically applies identically to the manager's live chat and the automated chase sweep — both are MCP clients hitting the exact same tool implementations. Full tool catalog: [`MCP_TOOLS.md`](./MCP_TOOLS.md).

## Skills and `SOUL.md`: behavior as plain language, not code

Every procedural rule an agent follows — how to interpret "waiting on the finance sheet," when to ask a clarifying question instead of guessing, how to phrase an escalation to the manager — lives in a markdown file (`hermes-config/`, `skills/`), not in Python. This is a deliberate boundary, not a limitation: **code enforces what must always be true** (an employee cannot self-grant a deadline extension — there's no tool call that lets them), **language handles everything that requires judgment** (does this reply mean "done," "blocked," or nothing at all?). Putting the judgment calls in editable prose rather than code means refining the model's behavior — after watching it get something wrong in real use — is a documentation edit, not a deploy.

## Real engineering problems this surfaced, and how they were closed

These are the fixes with the most transferable lessons for anyone building tool-calling agents on a local model. Full incident writeups, with before/after measurements, are in [`PROJECT_MANAGEMENT.md`](../PROJECT_MANAGEMENT.md).

- **Disabling the `tool_search`/`tool_describe`/`tool_call` discovery bridge** for both profiles. It exists for agents with too many tools to fit in one prompt; with ~16-20 total, it was pure overhead that the model occasionally, unpredictably routed through anyway — including calling the same once-only planning tool twice in one run.
- **Stripping unused built-in toolsets** (`computer_use`, `browser`, `code_execution`, `tts`, and others) that were being reprocessed into the prompt schema on every single turn despite never being called across weeks of real use — cutting one profile's per-turn input tokens from ~42K to ~32K.
- **Proof-of-send instead of narrated confidence.** A local model, asked to send a message, would sometimes write a confident closing line — "Pinged Daniel about the deck" — without ever calling `telegram_send_message`. Fixed structurally: a scheduled job now hard-fails if it ends without a real send tool call ever succeeding, rather than trusting the model's own summary of what it did.
- **An install-tree context leak.** A global `terminal.cwd` default was resolving into Hermes's own source checkout, silently loading its 77KB contributor `AGENTS.md` as "project context" on every single turn — a known upstream Hermes issue, fixed by pointing `terminal.cwd` at each profile's own (empty) directory.
- **Timezone-safe deadline parsing.** A deadline typed without an offset ("Friday 5pm") was being read as UTC; at UTC+8 that silently put the true deadline eight hours later than intended and broke overdue detection for the entire working day. Fixed by interpreting naive input in a configured local timezone and always rendering both UTC and local time back to the model.
- **One task-owner spam-avoidance rule, enforced in code, not prompt wording:** repeated overdue tasks for the same person are grouped into a single escalation message rather than one ping per task — a rule that would be easy for a model to "forget" under load if it were left as an instruction rather than a query-level group-by.

## Multi-modal intake

- **Bulk task creation** from an uploaded spreadsheet, PDF, or photo of a task list — parsed, resolved against registered people, previewed once, created via `create_tasks_bulk` on explicit approval.
- **Meeting-minutes extraction** from a voice recording, an uploaded audio file, a Word document, a PDF, or plain pasted text — transcribed/extracted, summarized, and turned into action items with an owner, a deadline, and a suggested priority inferred from the source's own urgency language.
- **Document template filling** — given a blank form (e.g. a delivery order template) and a short free-text note describing what goes in it, the model reads the *real* fields from the actual uploaded template (never assumes a previous template's layout still applies) and fills only what the note actually covers, leaving the rest blank and flagged rather than guessed.
