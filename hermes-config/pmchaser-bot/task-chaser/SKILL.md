---
name: task-chaser
description: "Chase task owners on Telegram for status updates and record what they say."
version: 3.5.0
license: Proprietary
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [pm-chaser, telegram, tasks, chase, deadline, overdue]
    related_skills: [task-digest]
---

# Task Chaser

## When to Use

The scheduled chase check, or any request to chase people for status on their tasks. For the manager's daily summary, use the task-digest skill instead.

## Procedure

1. Execute these steps by actually calling the tools. Never write out what you are about to call — emit the tool call itself. Narrating a plan ends the run having done nothing.
2. Call get_chase_plan(). One call gives you everything: replies waiting to be read, who to chase, who to escalate, who is unreachable, and what was filtered out with reasons. Call it once per run.
3. For each item in `replies_to_interpret`: decide what the message actually means and call update_task with the status and progress_pct it implies. If it was not a status update, leave the task alone. **If the reply asks for more time or a later deadline, never pass a new `deadline` to update_task** — the owner cannot grant themselves an extension. Instead call update_task(status="blocked") so it routes to the manager's digest for a decision, same as any other blocker. **After the update_task call, send a brief acknowledgment back to the owner** with telegram_send_message(owner_name, text) — **omit task_id on this call**, since it isn't a chase and passing task_id would create a new open check-in nobody is waiting on. One short line is enough ("Got it, marked as done — nice work." / "Thanks — flagging that for Jeffrey to review, he'll follow up on the new deadline."); it's the only signal the owner ever gets that their reply was actually read. **Also call `notify_manager`** with a short line naming who replied, on which task, and what changed ("Henry marked 'Submit vendor report' done." / "Jeffyeo's 'Clean up room' is now blocked — she said she's waiting on the key."). This is separate from and in addition to the acknowledgment to the owner, and separate from the escalation notification in step 6 below — it fires immediately for every real status change, not just repeated silence. Skip it only when the reply was not a status update (nothing changed, so nothing to tell the manager).
4. For each item in `unmatched_to_resolve`: work out which task it refers to, then call resolve_unmatched(unmatched_id, task_id) and update_task if the status changed (same extension-request rule, acknowledgment step, and `notify_manager` step as step 3 apply here too, only when the status actually changed). If it is about no task at all, call resolve_unmatched(unmatched_id) alone to dismiss it — no acknowledgment or manager notification needed, it wasn't a status update. **`resolve_unmatched` only ever records a decision in the database — it takes `unmatched_id` and optionally `task_id`, nothing else, and it never sends anything to anyone, regardless of what other arguments you pass it.** If it is a genuine coin flip: do **not** call `resolve_unmatched` at all — call `telegram_send_message(owner_name, text)` (no task_id, same as an acknowledgment) to actually ask the person which task they meant, and leave the item genuinely unresolved in the database so it surfaces again next run once they answer. Calling `resolve_unmatched` in this branch, for any reason, ends it permanently — it will never be offered to you again, answered or not.
5. For each item in `to_chase`: write a short, friendly, specific message naming the task and giving the deadline in plain words ("2 days overdue"), asking one clear question. **End it with a short prompt to use Telegram's reply feature on this specific message** ("reply directly to this one so I know exactly what it's about") — varied in wording like the rest of the message, but always present. A reply-to answer is matched to the right task with certainty regardless of how many other pings are outstanding; a fresh message from someone with more than one open ping has to be guessed at from text alone instead, which is exactly the kind of ambiguity that's landed in `unmatched_to_resolve` and gone wrong before. Vary the wording between runs. Send it with telegram_send_message(owner_name, text, task_id=<task>).
6. `to_escalate` is grouped one entry per (owner, manager) — every overdue task of theirs, under that specific manager, already combined together, since they've been ignored repeatedly and should not be pinged again. For each entry, send exactly **one message** naming every task in it and how overdue each is, then ask what to do next, via **`notify_manager`** — escalations go out through S.A.M.'s own bot identity, not the employee-facing chase bot (the manager only ever talks to S.A.M. directly; the chase bot is employee-facing only). `notify_manager` always resolves the single registered manager itself, so `manager_name` on the `to_escalate` entry is only for telling entries apart when an owner has tasks under two different managers (two separate entries, two separate calls) — it is not a recipient you pick between. **Address the manager as "boss" in the greeting, not by his first name** — "Jeffrey" is his record in the system, "boss" is how he's actually addressed. Real example, worth matching the tone and shape of: "Hi boss, escalating 3 tasks from Henry who hasn't replied to 3 check-ins each:\n1. Finish the Q3 board deck (overdue by ~35h)\n2. Review vendor SOW (due today)\n3. Confirm the venue booking for the offsite (overdue by ~34h)\nPlease advise on next steps."
7. If `unreachable` is not empty, tell `manager_name` who they are and include each link code, so they can be asked to send /start to the bot.
8. Glance at `skipped`. The rules are deliberately conservative — if one obviously deserves chasing anyway, chase it and say why you overrode the rule.
9. This runs unattended — nobody reads a narrative report. After the last tool call, close with one short line only, stating exactly and only what happened *this* run — never copy names, counts, or phrasing from an example. Only mention chasing if `to_chase` was non-empty, and only mention escalating if `to_escalate` was non-empty; do not force both into one sentence if only one occurred. Do not write a summary of what each message said or why.

## Pitfalls

- Describing a tool call in prose instead of emitting it. The run ends having done nothing.
- Calling get_chase_plan more than once in a run. It polls Telegram, and polling advances Telegram's cursor, so a second call returns no new messages by definition.
- Omitting task_id when chasing. The ping goes unrecorded and the person is chased again within the hour, forever.
- Treating someone in `unreachable` as ignoring you. They received nothing. That is a setup problem for the manager, not a missed reply.
- Assuming every entry in `replies_to_interpret` is a status update. A reply is matched whenever one ping is outstanding, so "thanks" can land there. Read it first.
- Sending more than one *chase* message to the same person in a run. `to_chase` already holds one task per person; do not add more. (An acknowledgment for an interpreted reply is a different kind of message and doesn't count against this — but still only one ack per reply, never more.)
- Passing task_id on an acknowledgment message. That creates a new open check-in for a message that was never a chase, which is exactly the kind of dangling unanswered check-in that later makes a genuine reply ambiguous.
- **Resending a message to "fix" a mistake noticed after telegram_send_message already returned success.** A real incident: the first send included task_id by mistake, the model noticed and tried again — still with task_id included — then a third time without it, so the same acknowledgment reached the owner three times. Once a send has succeeded, it is already in the recipient's chat and cannot be unsent — sending a corrected version does not replace it, it adds a duplicate on top. If you spot an error immediately after a successful send, say so plainly in your own final summary and let it stand; do not call telegram_send_message again for the same acknowledgment. Get it right before the call, not after.
- Sending a separate escalation message per task instead of per `to_escalate` entry. Each entry already covers everything for one owner — send one message per entry, not one per task inside it.
- Overriding the skip on a blocked task. The owner cannot fix a blocker and has usually already explained it, so chasing again is pure noise — it needs the manager to unblock it or move the date, and the digest raises it there.
- Calling update_task(deadline=...) off an owner's reply. A deadline change is the manager's call only — an owner asking for one is a blocker (status="blocked"), not an approved extension.
- Forgetting `notify_manager` after a reply that actually changed a task's status, or calling it for a reply that didn't (pure chatter with no real update). Also: this is a separate call from the owner's acknowledgment and from a step-6 escalation — don't skip it thinking the escalation or the ack already covers it, and don't send it twice for the same reply.
- **Passing a `text` argument to `resolve_unmatched`, believing it sends a clarifying question to the person.** A real incident: Henry sent "I completed task 1" while 3 of his tasks had outstanding pings; the model called `resolve_unmatched(unmatched_id=14, text="which task did you mean?")` with no `task_id`. `resolve_unmatched` has no `text` parameter and no ability to message anyone — the extra argument was silently dropped, `task_id` defaulted to `None`, and the message was dismissed as if it were noise. Henry never received the question, the task never got updated, and the item was gone for good (see below). Asking a clarifying question is always a real, separate `telegram_send_message` call — `resolve_unmatched` cannot do it for you no matter what you pass it.
- Calling `resolve_unmatched` at all on a genuine coin-flip you intend to ask about. Any call to it — dismiss or attach — marks the item handled and it will never be surfaced by `get_chase_plan` again. If you're asking rather than deciding, don't call it this run.

## Verification

- Every task in `to_chase` either got a message with its task_id attached, or was consciously skipped with a reason.
- Nobody received more than one chase message this run.
- Every reply either produced an update_task call or was judged not to be a status update.
- Every reply that did produce an update_task call also got a brief acknowledgment sent back, without task_id, AND a `notify_manager` call summarizing the change.
- Nothing in `unmatched_to_resolve` was left pending unless you are waiting on a clarifying answer — and if you are, a real `telegram_send_message` was actually sent asking it, and `resolve_unmatched` was NOT called for that item.
- Escalations went to the manager, not to the person being chased.
- get_chase_plan was called exactly once.
- The final sign-off names only people and counts from *this* run — not from an example.
