---
name: task-digest
description: "Write the manager's summary of task progress and send it to them on Telegram."
version: 2.2.0
license: Proprietary
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [pm-chaser, telegram, digest, summary, manager]
    related_skills: [task-chaser]
---

# Task Digest

## When to Use

The scheduled daily digest, or any request for a summary of where tasks stand. To chase individual people for updates, use the task-chaser skill instead.

## Procedure

1. Execute these steps by actually calling the tools. Never write out what you are about to call — emit the tool call itself.
2. Call telegram_get_updates() first, before reading any task state. It polls Telegram and advances the cursor, so anyone who replied since the last chase run would otherwise be missing from this digest. Call it exactly once per run — a second call returns no new messages by definition.
3. For each item in `replies`: decide what the message actually means and call update_task with the status/progress it implies — same rule as task-chaser step 3: never pass a new `deadline`, an extension request is `status="blocked"` instead. If it was not a status update, leave the task alone. **If it was a status update, send a brief acknowledgment back with telegram_send_message(owner_name, text) — omit task_id, since it isn't a chase.** This poll consumes the reply, so task-chaser's own cron pass will never see it — this is the owner's only signal their reply was actually read; skipping it here means it never happens.
4. For each item in `unmatched`: work out which task it means (list_tasks helps) and call resolve_unmatched(unmatched_id, task_id) — send the same brief acknowledgment if that call changed a task's status; if it's about no task at all, call resolve_unmatched(unmatched_id) alone to dismiss it, no acknowledgment needed.
5. Call get_digest_data(). **Only after every reply/unmatched item from step 2 has actually been handled (steps 3-4 complete) — never batch this call together with telegram_get_updates() in the same turn.** get_digest_data reads whatever is in the database at the exact moment it's called, so calling it early reintroduces the same staleness this whole procedure exists to avoid, just for this one run instead of always. One call gives you the manager to send to, every active task with its state (now including whatever was just updated in steps 3-4), anything blocked and awaiting a decision, who has stopped replying, and who never linked Telegram.
6. Decide what actually matters. Lead with what the manager must act on: anything in `blocked_needing_decision`, then overdue work, anything at risk of slipping, people who have stopped responding, and anyone unreachable. Then note briefly what is on track.
7. For each item in `blocked_needing_decision`, name who is blocked and what they said (`reason_given`), then recommend a concrete next step — extend the deadline, reassign it, or clear the blocker. These are no longer being chased, so they sit untouched until the manager acts. Say so plainly when `deadline_already_passed` is true: a blocked task whose deadline has gone will stay stuck until the date is moved.
8. Write it as short readable prose, not a table or a data dump. Quote the specific blockers people reported rather than saying "some tasks are blocked".
9. If nothing needs attention, say so in one line. A short digest is a good digest.
10. Send it with telegram_send_message(manager_name, digest) and NO task_id. This step is mandatory even on a quiet day ("all clear" still has to actually be sent) — writing the digest text is not the same as sending it.
11. Only after telegram_send_message has actually been called and returned sent:true: this runs unattended, nobody reads a second report, so close with one short line only (e.g. "Digest sent to Jeffrey."). Never write that closing line, or the digest content itself, as your final answer without having called the tool first — a confident-sounding summary that was never sent is a failed run, not a successful one.

## Pitfalls

- Describing a tool call in prose instead of emitting it, or writing the digest out as your final text without ever calling telegram_send_message. The run ends having done nothing, even if the sign-off claims otherwise.
- Passing a task_id when sending the digest. It is not a chase and must not be recorded as a check-in against any task.
- Sending chase messages during a digest run. This job only reports; task-chaser does the chasing.
- Listing every task mechanically instead of leading with what needs a decision.
- Padding a quiet day into a long report.
- Calling the tasks "at risk" only when the data says overdue. Judge risk yourself — a task at 10% due tomorrow is at risk even though nothing is late yet.
- Calling get_digest_data() before telegram_get_updates(), skipping the poll entirely, or batching the two into the same turn instead of handling steps 3-4 in between. A reply sent minutes ago won't be in the digest, and it will read as having missed something that already happened.
- Calling telegram_get_updates() more than once in a run. It advances the cursor, so a second call returns no new messages by definition (same reasoning as task-chaser's get_chase_plan).
- Skipping the acknowledgment for a reply this run interpreted. Since the poll already consumed it, task-chaser's cron pass will never see it — if this run doesn't send the ack, the owner never learns their reply was read.
- Passing task_id on that acknowledgment. Same reasoning as task-chaser: it isn't a chase, and passing task_id would open a new check-in nobody is waiting on.

## Verification

- telegram_get_updates was called exactly once, before get_digest_data.
- Every reply either produced an update_task call or was judged not to be a status update, and every status-changing one got a task_id-less acknowledgment back to its owner.
- Nothing in `unmatched` was left pending unless genuinely ambiguous.
- Exactly one digest message was sent, it went to the manager, and it carried no task_id (owner acknowledgments from steps 3-4 are separate messages and don't count against this).
- Overdue work, unresponsive people and unreachable owners are all named explicitly if present.
- No chase messages were sent during this run.
