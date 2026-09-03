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


def test_pmchaser_bot_gets_exactly_the_five_tools_its_skill_actually_calls():
    """Confirmed by grepping hermes-config/pmchaser-bot/task-chaser/SKILL.md
    for every one of the 16 tool names - these five are the only ones
    that appear at all."""
    assert set(PMCHASER_BOT_TOOLS) == {
        "get_chase_plan", "update_task", "telegram_send_message",
        "resolve_unmatched", "notify_manager",
    }


def test_task_manager_bot_gets_everything_except_get_chase_plan_and_notify_manager():
    """Confirmed by grepping hermes-config/task-manager-bot/SOUL.md -
    every other tool name appears at least 3 times; these two appear
    zero times. get_chase_plan is pmchaser-bot's scheduled-sweep-only
    planning tool (task-manager-bot's manual equivalent is chase_now);
    notify_manager exists only so pmchaser-bot, which has no live
    conversation channel, can reach the manager through S.A.M.'s bot
    identity - task-manager-bot IS that channel already."""
    assert set(TASK_MANAGER_BOT_TOOLS) == set(ALL_TOOLS) - {"get_chase_plan", "notify_manager"}


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
