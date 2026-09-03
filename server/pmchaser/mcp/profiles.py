"""Which MCP tools each Hermes Agent profile actually needs.

Derived empirically from what hermes-config/task-manager-bot/SOUL.md and
hermes-config/pmchaser-bot/task-chaser/SKILL.md actually reference by
name (confirmed with a grep sweep across both files, not assumption) -
the refactor plan's finding #10 and Phase 3's biggest lever. Both
profiles' full toolset was previously registered on the same single
server regardless of which tools a given profile's own instructions ever
call, so pmchaser-bot (the automated 15-minute chase/reply job) paid for
all 16 tools' schemas in its prompt every run despite using only 5 of
them - the Phase 0 benchmark measured this as a ~3.1x token overpay,
worth an estimated 12+ seconds of pure prefill on a cold KV cache at this
model/hardware's measured ~5.4s-per-1K-uncached-tokens cost.

Registering only a profile's own configured toolset does not change any
tool's behavior - only what's advertised to which server.
tests/test_mcp_contract.py verifies each profile's live server
advertises exactly this set, with matching schemas, and nothing else.
"""

from __future__ import annotations

ALL_TOOLS: tuple[str, ...] = (
    "get_chase_plan", "chase_now", "get_digest_data",
    "create_task", "create_tasks_bulk", "list_tasks", "update_task",
    "reassign_task", "delete_task", "register_person", "delete_person",
    "list_people", "telegram_send_message", "telegram_get_updates",
    "resolve_unmatched", "notify_manager", "record_reply_outcome",
)

# pmchaser-bot: the automated 15-minute chase sweep + instant-reply
# handler (hermes-config/pmchaser-bot/task-chaser/SKILL.md). No live
# conversation, no task creation/editing beyond what a chase run itself
# does (interpreting a reply into a status update).
PMCHASER_BOT_TOOLS: tuple[str, ...] = (
    "get_chase_plan",
    "update_task",
    "telegram_send_message",
    "resolve_unmatched",
    "notify_manager",
    # Phase 3 composite tool: collapses the update_task + ack + notify_manager
    # triad step 3 of task-chaser SKILL.md always performs together for a
    # real status update into one call. See pmchaser/services/reply_outcomes.py.
    "record_reply_outcome",
)

# task-manager-bot: the manager's live conversational assistant, S.A.M.
# (hermes-config/task-manager-bot/SOUL.md). Everything except:
#   - get_chase_plan: pmchaser-bot's scheduled-sweep-only planning tool -
#     the manual equivalent this profile actually uses is chase_now.
#   - notify_manager: exists only so pmchaser-bot, which has no direct
#     conversation channel, can reach the manager through S.A.M.'s bot
#     identity. task-manager-bot IS that channel already; it has no
#     reason to message itself.
#   - record_reply_outcome: SOUL.md rule 15's own reply-handling flow
#     never sends an owner acknowledgment or a manager notification for
#     an interpreted reply in the first place (the manager IS the one
#     asking, and gets a direct answer instead) - this profile has no
#     use for the composite that exists to replace that pattern.
TASK_MANAGER_BOT_TOOLS: tuple[str, ...] = tuple(
    name for name in ALL_TOOLS
    if name not in ("get_chase_plan", "notify_manager", "record_reply_outcome")
)

PROFILES: dict[str, tuple[str, ...]] = {
    "pmchaser-bot": PMCHASER_BOT_TOOLS,
    "task-manager-bot": TASK_MANAGER_BOT_TOOLS,
}


def tools_for_profile(profile: str | None) -> tuple[str, ...]:
    """The tool names to register for `profile`.

    None, or any name not in PROFILES, returns the full 16 - the
    backward-compatible default for a deployment that hasn't opted into
    per-profile splitting (PM_CHASER_TOOL_PROFILE unset - see main.py),
    and for local dev/testing that wants the whole surface in one place.
    """
    if profile is None:
        return ALL_TOOLS
    return PROFILES.get(profile, ALL_TOOLS)
