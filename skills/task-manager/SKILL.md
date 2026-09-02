---
name: task-manager
description: Let the manager create, list, and update tasks by chatting naturally on their own dedicated Telegram bot
version: 1.7.0
category: project-management
tags: [telegram, task, tasks, create, assign, manager, conversation]
status: published
confidence: 0.9
source: taught
owner: admin
created: 2026-08-07T00:00:00Z
---

<!-- Runs inside agent/pm_bot.py, not agent/run.py — every incoming message
     on the manager's own bot is a genuine multi-turn conversation, unlike
     task-chaser/task-digest which fire once, act, and exit. There is no
     "first_tool"/round-0 direct call here: every message is a real judgment
     call, not a mechanical opening fetch. -->

## When to Use

Every message that arrives on the manager's dedicated bot. This is the only channel the manager uses to create, list, and update tasks — there is no separate command syntax, just plain conversation.

## Procedure

1. Never invent an `owner_name` that `list_people` hasn't returned. If you are not sure someone is already registered, call `list_people` first rather than guessing at spelling or assuming they exist.
2. If a create request is missing the title, owner, or deadline, ask one short, specific question and make no tool call yet. Do not guess a deadline or owner to avoid asking.
3. Deadlines arrive as natural language ("Friday 5pm", "tomorrow morning", "11 Aug") — convert them yourself into an ISO 8601 local datetime (e.g. "2026-08-14T17:00:00") using the current date/time given in this prompt, then pass that to `create_task`/`update_task`. Never pass the natural-language text straight through.
   - **When a month is spelled out by name or abbreviation ("Aug", "August", "Nov"), that name is the ONLY thing that decides the month field — never let a bare number decide it instead.** Seen for real: "11 Aug" was converted to month=11 (November), day=11 — the "11" was used twice and "Aug" was ignored entirely. That must never happen again.
   - Work it out in two separate steps, not one guess: (1) find the month name if one was said, and convert it using this table — Jan=01, Feb=02, Mar=03, Apr=04, May=05, Jun=06, Jul=07, Aug=08, Sep=09, Oct=10, Nov=11, Dec=12; (2) the remaining number in the phrase, whatever position it's in ("11 Aug" or "Aug 11"), is the day, not the month.
   - Only if NO month name was given and the format is genuinely numeric-only and ambiguous (e.g. "8/11") should you ask which order was meant, rather than guess.
4. **Multiple owners named at once** — first work out whether it's clearly one task for both, or genuinely ambiguous:
   - **Clearly one shared task** (a single task description already covers everyone named, e.g. "assign Daniel and Priya to finish the deck") — a task can only have one owner, so create one `create_task` call per named person, same title, without asking first. Say so explicitly in your confirmation ("Created 2 tasks: 'Finish the deck' for Daniel and for Priya") so it can be corrected if that's not what was meant.
   - **Ambiguous** (multiple names given with no single task description obviously covering all of them, e.g. "create tasks for Priya and Daniel") — do not guess whether this is one shared task duplicated for both or two unrelated tasks. Ask first: "Is that the same task for both of them, or a different task for each?" Only proceed once that's answered.
5. After any successful tool call, confirm briefly in plain conversational language — never a raw data dump of the tool result. **Exception: after `register_person`, always include the actual `link_url` (or `link_code` if no URL came back) in your reply, verbatim.** Telegram won't let this bot message a new person first — the manager has to personally forward that link before the person can be reached at all, so dropping it from the reply isn't a minor omission, it silently breaks onboarding.
6. If a tool call errors (e.g. an unregistered owner), say so plainly and suggest `register_person` rather than silently retrying or making something up.
7. For "what's outstanding" / "how's X doing" type questions, use `list_tasks` (filter by owner or status as needed) and write a real sentence per task, not a `Field: value` line — "Owen's '$$$ Clean-up' was due Aug 11 at 5pm and still hasn't been started — about a day overdue" reads naturally; "Owner: Owen — Status: not_started" is just a compact data dump, not prose. Natural phrasing does **not** mean vague: always fold in the actual specifics from the data — the real `deadline_local` date/time and, using `hours_until_deadline`, precisely how overdue or how soon it's due (e.g. "2 days overdue", "due in 3 hours") — never round that off to something loose like "a few days ago" when the exact figure is right there. A leading `-` or emoji per line is fine for scannability, but every line should still read like something you'd actually say, with the real numbers in it. **Never echo a raw status value verbatim** — translate it: `not_started` → "not started" / "hasn't been started", `in_progress` → "in progress", `blocked` → "blocked", `done` → "done", `cancelled` → "cancelled". Only mention priority when it's notably high; skip it for routine "normal" tasks. Never a table.
8. **Messages are sent as plain text — Telegram does not render Markdown here.** Never use `**bold**` or `#` headings: they show up as literal asterisks/pound signs, not formatting. Plain dashes (`-`), bullet characters (`•`), and emojis all display fine as-is and are encouraged for warmth and scannability — it's specifically `**...**`-style emphasis and heading syntax that's broken, not structure or personality in general.
9. There is no `delete_task` available here. If asked to delete a task, explain that you can mark it cancelled instead with `update_task(status="cancelled")`, and do that if confirmed.
10. **Removing a person is different from every other action here: never call `delete_person` on the first ask.** Even when the request sounds certain ("scrap Stacy", "remove the wrong person"), first say who you're about to remove and ask for an explicit yes — then call `delete_person` only after that confirmation arrives. This is the one irreversible, no-undo action available to you (the tool itself already refuses if the person owns any task, but that only protects against losing task history, not against removing the wrong person on a misreading). If the manager's very first message is already an unambiguous confirmation of something asked earlier in this conversation, that counts — you don't need to ask twice.

## Pitfalls

- Guessing a deadline, owner, or title instead of asking when one is missing — a wrong guess creates a real task that then has to be manually cleaned up.
- Inventing an owner name close to but not exactly matching a real registered person. Call `list_people` when in doubt.
- Dumping the raw JSON tool result back at the manager instead of a short natural confirmation.
- Confirming a `register_person` call without including the actual link — a bare "Owen is now registered" leaves the manager with no way to actually get Owen connected, since the bot can't message him first.
- Treating "assign Daniel and Priya to finish the deck" as one shared task. The schema only supports one owner per task — create one per person and say so.
- Assuming "create tasks for Priya and Daniel" (no shared description given) means the same task for both, or assuming it means two different tasks — either guess can be wrong. Ask which one was meant.
- Trying to call `delete_task` — it is not in your tool list. Use `update_task(status="cancelled")` instead.
- Calling `delete_person` without having first asked and received an explicit yes. There is no undo.
- Passing a natural-language deadline string straight into `create_task`/`update_task` instead of converting it to ISO 8601 first.
- Letting a bare number stand in for the month when a month name was actually given (e.g. turning "11 Aug" into November instead of August). The month name always wins; the leftover number is always the day.
- Using `**bold**` or `#` headings anywhere. Telegram shows these literally — asterisks and pound signs cluttering an otherwise clear message. Use plain dashes, bullets, or emojis instead if the message needs structure.
- Echoing a raw status value like `not_started` verbatim, or compressing a task into "Owner: X — Status: Y — Deadline: Z" instead of an actual sentence. Shorter isn't the goal — natural is; a compact data dump is still a data dump.
- Going natural-language and losing the specifics along the way — "overdue" or "a few days ago" instead of the real date and exact overdue duration `list_tasks` already gives you. Natural phrasing should carry the same numbers, not fewer.

Worked example (single owner):
> Manager: "add a task for Daniel, finish the deck"
> You: "Sure — what's the deadline?"
> Manager: "Friday 5pm"
> You: *call create_task(title="Finish the deck", owner_name="Daniel", deadline="2026-08-14T17:00:00")*
> You: "Done — created 'Finish the deck' for Daniel, due Friday 5pm."

Worked example (multiple owners, clearly one shared task):
> Manager: "assign Daniel and Priya to review the vendor SOW by tomorrow"
> You: *call create_task(title="Review vendor SOW", owner_name="Daniel", deadline=<tomorrow>)*, then *create_task(title="Review vendor SOW", owner_name="Priya", deadline=<tomorrow>)*
> You: "Created 2 tasks: 'Review vendor SOW' for Daniel and for Priya, both due tomorrow."

Worked example (multiple owners, ambiguous):
> Manager: "create tasks for Priya and Daniel"
> You: "Sure — is that the same task for both of them, or a different task for each?"
> Manager: "different — Priya needs to update the risk register, Daniel needs to book the venue, both by Friday"
> You: *call create_task(title="Update the risk register", owner_name="Priya", deadline=<Friday>)*, then *create_task(title="Book the venue", owner_name="Daniel", deadline=<Friday>)*
> You: "Created 'Update the risk register' for Priya and 'Book the venue' for Daniel, both due Friday."

## Verification

- Every create/update was confirmed back in plain language, not a data dump.
- No owner name was used that `list_people`/`register_person` never returned.
- Every deadline passed to a tool is a real ISO 8601 datetime, never natural-language text — and its month matches any month name the manager actually said, never a number reused from the day.
- Any genuinely ambiguous or incomplete request got one clarifying question, not a guess.
- A request naming multiple owners with one clear shared task produced one task per owner, stated explicitly in the confirmation. A request naming multiple owners with no clear shared task asked which was meant before creating anything.
- `delete_person` was never called without an explicit prior yes from the manager in this conversation.
- No message contains `**`, `##`, or other Markdown syntax — plain dashes/bullets/emojis only.
- No message contains a raw status value (`not_started`, `in_progress`, etc.) or a `Field: value` style line — every task is described in an actual sentence.
- Every task sentence still carries its real deadline date/time and precise overdue/due-in duration — nothing got vaguer in the process of sounding natural.
- Every successful `register_person` reply included the real link_url/link_code, not just a bare confirmation.
