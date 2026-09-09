"""record_reply_outcome: a Phase 3 composite tool for pmchaser-bot only.

Collapses the three separate calls task-chaser SKILL.md step 3 requires
for every reply that turns out to be a real status update -
update_task(...), an acknowledgment to the owner via
telegram_send_message(owner_name, text) with no task_id, and
notify_manager(text) - into one. Existing tools are unchanged; this is
purely additive, matching the plan's "composite tools to cut round
trips" item.

Why this matters beyond token savings: a real live incident (see
pmchaser/services/chase.py's action_required fix, added the same day)
showed this exact local model completing part of a multi-call sequence
and then narrating the rest instead of emitting it. task-chaser SKILL.md
step 3 is the *same* structural shape - one call whose result matters,
then two more calls the model must remember to make on its own - so it
carries the identical risk. Reducing three calls to one does not make
skipping a call impossible (the model could still skip calling this
composite entirely), but it removes the specific "I already did
something, that feels like progress" failure point PROJECT_MANAGEMENT.md
already documents for multi-step chains with this model.

Deliberately scoped to *only* the update+ack+notify triad task-chaser
SKILL.md step 3 describes - not resolve_unmatched (step 4's matching
decision is a separate concern with its own irreversibility rules) and
not chase_now/get_chase_plan's send-a-chase flow (that already has its
own action_required reminder, added separately). Only registered for the
pmchaser-bot profile (see pmchaser/mcp/profiles.py) - task-manager-bot's
own reply-handling rule (SOUL.md rule 15) never sends an owner
acknowledgment or a manager notification for this flow in the first
place, so it has no use for this tool.
"""

from __future__ import annotations

from pmchaser.services.messaging import telegram_send_message
from pmchaser.services.notifications import notify_manager
from pmchaser.services.tasks import update_task


def record_reply_outcome(
    task_id: int,
    reply_text: str,
    ack_text: str,
    manager_note: str,
    status: str | None = None,
    progress_pct: int | None = None,
) -> dict:
    """Update a task from an interpreted reply, acknowledge the owner, and
    notify the manager - the three actions task-chaser SKILL.md step 3
    always performs together for a genuine status update - in one call.

    Only call this when the reply genuinely was a status update. If it
    wasn't, don't call this at all - there is nothing to record, ack, or
    notify.

    `reply_text` is the owner's own words, quoted verbatim in the manager
    notification structurally - not left to manager_note to restate. Real
    incident this closes: the manager reported never seeing what an
    employee actually said, because manager_note was always a model-
    authored paraphrase ("Henry marked X done") and nothing forced the
    original text into it. Composing the quote here, in code, means the
    manager sees Henry's real words on every call, not only on the calls
    where the model happened to think to include them.
    """
    task_result = update_task(task_id, status=status, progress_pct=progress_pct)
    if "error" in task_result:
        # The task lookup itself failed - nothing to acknowledge or
        # notify about, and attempting either would reference a task
        # that was never actually updated.
        return {"task": task_result}

    owner_name = task_result.get("owner_name")
    ack_result = (
        telegram_send_message(owner_name, ack_text)
        if owner_name
        else {"error": "Task has no resolvable owner_name - cannot send an acknowledgment."}
    )

    task_title = task_result.get("title")
    quote_line = (
        f'{owner_name} replied on "{task_title}": "{reply_text}"'
        if owner_name
        else f'Reply on "{task_title}": "{reply_text}"'
    )
    manager_result = notify_manager(f"{quote_line}\n\n{manager_note}")

    return {
        "task": task_result,
        "ack": ack_result,
        "manager_notification": manager_result,
    }
