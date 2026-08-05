---
name: task-digest
description: Write the manager's summary of task progress and send it to them on Telegram
version: 2.0.0
category: project-management
tags: [digest, summary, report, manager, standup, progress, roundup]
status: published
confidence: 0.9
source: taught
owner: admin
created: 2026-08-05T08:00:00Z
---

<!-- Kept separate from task-chaser so neither job's instructions bloat the
     other's injected context. Only description, when_to_use, procedure and
     pitfalls reach the agent automatically — keep them terse. -->

## When to Use

The scheduled daily digest, or any request for a summary of where tasks stand. To chase individual people for updates, use the task-chaser skill instead.

## Procedure

1. Execute these steps by actually calling the tools. Never write out what you are about to call — emit the tool call itself.
2. Call get_digest_data(). One call gives you the manager to send to, every active task with its state, anything blocked and awaiting a decision, who has stopped replying, and who never linked Telegram.
3. Decide what actually matters. Lead with what the manager must act on: anything in `blocked_needing_decision`, then overdue work, anything at risk of slipping, people who have stopped responding, and anyone unreachable. Then note briefly what is on track.
4. For each item in `blocked_needing_decision`, name who is blocked and what they said (`reason_given`), then recommend a concrete next step — extend the deadline, reassign it, or clear the blocker. These are no longer being chased, so they sit untouched until the manager acts. Say so plainly when `deadline_already_passed` is true: a blocked task whose deadline has gone will stay stuck until the date is moved.
5. Write it as short readable prose, not a table or a data dump. Quote the specific blockers people reported rather than saying "some tasks are blocked".
6. If nothing needs attention, say so in one line. A short digest is a good digest.
7. Send it with telegram_send_message(manager_name, digest) and NO task_id.

## Pitfalls

- Describing a tool call in prose instead of emitting it. The run ends having done nothing.
- Passing a task_id when sending the digest. It is not a chase and must not be recorded as a check-in against any task.
- Sending chase messages during a digest run. This job only reports; task-chaser does the chasing.
- Listing every task mechanically instead of leading with what needs a decision.
- Padding a quiet day into a long report.
- Calling the tasks "at risk" only when the data says overdue. Judge risk yourself — a task at 10% due tomorrow is at risk even though nothing is late yet.

## Verification

- Exactly one message was sent, it went to the manager, and it carried no task_id.
- Overdue work, unresponsive people and unreachable owners are all named explicitly if present.
- No chase messages were sent during this run.
