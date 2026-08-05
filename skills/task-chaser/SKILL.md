---
name: task-chaser
description: Chase task owners on Telegram for status updates and record what they say
version: 3.0.0
category: project-management
tags: [telegram, task, tasks, chase, chasing, deadline, overdue, blocked, status, reminder]
status: published
confidence: 0.9
source: taught
owner: admin
created: 2026-08-05T08:00:00Z
---

<!-- FORMAT NOTES (for humans editing this file, not for the agent):

     Odysseus auto-injects ONLY description, when_to_use, procedure and
     pitfalls. Verification appears solely via manage_skills action=view, and
     only the four headings When to Use / Procedure / Pitfalls / Verification
     are recognised — any other "## " heading is stripped and absorbed above.

     Keep the injected fields SHORT. v1 carried 18 steps and 11 long pitfalls
     (~6k chars) and the model responded by narrating a plan and emitting no
     tool calls at all. v3 leans on get_chase_plan(), which does the filtering
     in code, so this file only has to cover judgement. Brevity is functional.

     The daily digest lives in the separate task-digest skill. -->

## When to Use

The scheduled chase check, or any request to chase people for status on their tasks. For the manager's daily summary, use the task-digest skill instead.

## Procedure

1. Execute these steps by actually calling the tools. Never write out what you are about to call — emit the tool call itself. Narrating a plan ends the run having done nothing.
2. Call get_chase_plan(). One call gives you everything: replies waiting to be read, who to chase, who to escalate, who is unreachable, and what was filtered out with reasons. Call it once per run.
3. For each item in `replies_to_interpret`: decide what the message actually means and call update_task with the status and progress_pct it implies. If it was not a status update, leave the task alone.
4. For each item in `unmatched_to_resolve`: work out which task it refers to, then call resolve_unmatched(unmatched_id, task_id) and update_task if the status changed. If it is about no task at all, call resolve_unmatched(unmatched_id) alone to dismiss it. If it is a genuine coin flip, message the person to ask which task they meant and leave it unresolved.
5. For each item in `to_chase`: write a short, friendly, specific message naming the task and giving the deadline in plain words ("2 days overdue"), asking one clear question. Vary the wording between runs. Send it with telegram_send_message(owner_name, text, task_id=<task>).
6. For each item in `to_escalate`: these have been ignored repeatedly, so do not ping them again. Send one message to `manager_name` naming the task, its owner, and how many pings went unanswered.
7. If `unreachable` is not empty, tell `manager_name` who they are and include each link code, so they can be asked to send /start to the bot.
8. Glance at `skipped`. The rules are deliberately conservative — if one obviously deserves chasing anyway, chase it and say why you overrode the rule.
9. Report briefly what you sent and to whom.

## Pitfalls

- Describing a tool call in prose instead of emitting it. The run ends having done nothing.
- Calling get_chase_plan more than once in a run. It polls Telegram, and polling advances Telegram's cursor, so a second call returns no new messages by definition.
- Omitting task_id when chasing. The ping goes unrecorded and the person is chased again within the hour, forever.
- Treating someone in `unreachable` as ignoring you. They received nothing. That is a setup problem for the manager, not a missed reply.
- Assuming every entry in `replies_to_interpret` is a status update. A reply is matched whenever one ping is outstanding, so "thanks" can land there. Read it first.
- Sending more than one message to the same person in a run. `to_chase` already holds one task per person; do not add more.

## Verification

- Every task in `to_chase` either got a message with its task_id attached, or was consciously skipped with a reason.
- Nobody received more than one chase message this run.
- Every reply either produced an update_task call or was judged not to be a status update.
- Nothing in `unmatched_to_resolve` was left pending unless you are waiting on a clarifying answer.
- Escalations went to the manager, not to the person being chased.
- get_chase_plan was called exactly once.
