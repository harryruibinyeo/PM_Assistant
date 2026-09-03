"""pmchaser.mcp.profiles: which tools each Hermes profile is allowed to
call. Pure-function unit tests; the live, end-to-end version of this
guarantee (does the actual running server for a given profile advertise
exactly this set) is tests/test_mcp_contract.py.
"""

from __future__ import annotations

from pmchaser.mcp.profiles import (
    ALL_TOOLS,
    PMCHASER_BOT_TOOLS,
    TASK_MANAGER_BOT_TOOLS,
    tools_for_profile,
)


def test_pmchaser_bot_gets_exactly_the_tools_its_skill_actually_calls_plus_the_composite():
    """Confirmed by grepping hermes-config/pmchaser-bot/task-chaser/SKILL.md
    for every one of the (pre-Phase-3) 16 tool names - these five were the
    only ones that appeared at all. record_reply_outcome is the Phase 3
    composite tool (pmchaser/services/reply_outcomes.py) that replaces
    step 3's update_task+ack+notify_manager triad - it doesn't appear in
    SKILL.md by that grep (the file predates it), but SKILL.md step 3 was
    updated in the same phase to call it instead."""
    assert set(PMCHASER_BOT_TOOLS) == {
        "get_chase_plan", "update_task", "telegram_send_message",
        "resolve_unmatched", "notify_manager", "record_reply_outcome",
    }


def test_task_manager_bot_gets_everything_except_the_pmchaser_bot_only_tools():
    """Confirmed by grepping hermes-config/task-manager-bot/SOUL.md -
    every other tool name appears at least 3 times; these three appear
    zero times. get_chase_plan is pmchaser-bot's scheduled-sweep-only
    planning tool (task-manager-bot's manual equivalent is chase_now);
    notify_manager exists only so pmchaser-bot, which has no live
    conversation channel, can reach the manager through S.A.M.'s bot
    identity - task-manager-bot IS that channel already; record_reply_outcome
    replaces a 3-call pattern (update_task+ack+notify_manager) that SOUL.md's
    own reply-handling rule (15) never performs in the first place - it
    just answers the manager directly."""
    assert set(TASK_MANAGER_BOT_TOOLS) == set(ALL_TOOLS) - {
        "get_chase_plan", "notify_manager", "record_reply_outcome",
    }


def test_the_two_profiles_and_all_tools_are_internally_consistent():
    assert set(PMCHASER_BOT_TOOLS) <= set(ALL_TOOLS)
    assert set(TASK_MANAGER_BOT_TOOLS) <= set(ALL_TOOLS)
    # Between them, every tool is reachable by at least one profile - if a
    # future tool is added and forgotten from both lists, it would be
    # registered on neither profile-scoped server (silently uncallable,
    # not even an error) despite still being registered under the
    # unset-profile default.
    assert set(PMCHASER_BOT_TOOLS) | set(TASK_MANAGER_BOT_TOOLS) == set(ALL_TOOLS)


def test_no_duplicate_tool_names_within_a_profile():
    assert len(PMCHASER_BOT_TOOLS) == len(set(PMCHASER_BOT_TOOLS))
    assert len(TASK_MANAGER_BOT_TOOLS) == len(set(TASK_MANAGER_BOT_TOOLS))


def test_tools_for_profile_none_returns_everything():
    assert set(tools_for_profile(None)) == set(ALL_TOOLS)


def test_tools_for_profile_known_names():
    assert set(tools_for_profile("pmchaser-bot")) == set(PMCHASER_BOT_TOOLS)
    assert set(tools_for_profile("task-manager-bot")) == set(TASK_MANAGER_BOT_TOOLS)


def test_tools_for_profile_unknown_name_falls_back_to_everything():
    """A typo'd PM_CHASER_TOOL_PROFILE should fail open to the full
    toolset (safe: a tool being unexpectedly available), not fail closed
    to zero tools (unsafe: a production server silently serving no
    tools at all, breaking every conversation with no obvious cause)."""
    assert set(tools_for_profile("not-a-real-profile")) == set(ALL_TOOLS)
