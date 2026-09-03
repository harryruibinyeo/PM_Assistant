"""Guards the single hardest constraint of this refactor: the 16 MCP tool
docstrings ARE the prompt the agent reasons from, and SOUL.md/SKILL.md
reference tools by exact name, argument name, and return-key name.

`tests/golden/tool_contract.json` was captured from the untouched, original
tools.py on this branch (see the plan's Phase 0). This test asserts every
tool's name, parameter list (name/annotation/default/kind), return
annotation, and full docstring text stay byte-identical through every later
phase of the refactor.

This is a *detector*, not a freeze: a later phase that deliberately changes
a docstring for latency (see the plan's Phase 3) updates this snapshot
explicitly, with the diff visible in that commit - never as a silent
side effect of a "harmless" rename or reformat.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

GOLDEN_PATH = Path(__file__).parent / "golden" / "tool_contract.json"

TOOL_NAMES = [
    "get_chase_plan", "chase_now", "get_digest_data",
    "create_task", "create_tasks_bulk", "list_tasks", "update_task",
    "reassign_task", "delete_task", "register_person", "delete_person",
    "list_people", "telegram_send_message", "telegram_get_updates",
    "resolve_unmatched", "notify_manager",
]


def _capture_contract(tools_mod) -> dict:
    contract = {}
    for name in TOOL_NAMES:
        fn = getattr(tools_mod, name)
        sig = inspect.signature(fn)
        params = []
        for pname, p in sig.parameters.items():
            default = p.default
            default_repr = None if default is inspect.Parameter.empty else repr(default)
            ann = None if p.annotation is inspect.Parameter.empty else str(p.annotation)
            params.append({
                "name": pname, "annotation": ann, "default": default_repr,
                "kind": str(p.kind),
            })
        contract[name] = {
            "params": params,
            "return_annotation": (
                None if sig.return_annotation is inspect.Signature.empty
                else str(sig.return_annotation)
            ),
            "docstring": inspect.getdoc(fn),
        }
    return contract


def test_all_16_tools_are_still_registered(fresh_db):
    tools = fresh_db
    for name in TOOL_NAMES:
        assert hasattr(tools, name), f"tool {name!r} is missing from tools.py"


def test_tool_contract_is_byte_identical_to_the_golden_snapshot(fresh_db):
    tools = fresh_db
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    current = _capture_contract(tools)

    assert current.keys() == golden.keys(), (
        "tool set changed - if intentional, regenerate "
        "tests/golden/tool_contract.json and call it out in the commit"
    )

    for name in TOOL_NAMES:
        assert current[name] == golden[name], (
            f"tool contract for {name!r} drifted from the golden snapshot. "
            f"This tool's signature/docstring is part of the agent's live "
            f"prompt (SOUL.md/SKILL.md reference it by name) - if this "
            f"change is deliberate, regenerate the golden file and show "
            f"the diff in the commit; otherwise this is an accidental "
            f"regression.\n\ngolden:\n{json.dumps(golden[name], indent=2)}\n\n"
            f"current:\n{json.dumps(current[name], indent=2)}"
        )


def test_mcp_endpoint_registers_all_16_tools_with_matching_names():
    """Confirms main.py's default (PM_CHASER_TOOL_PROFILE unset) registers
    exactly the tool functions this contract snapshot covers - catches a
    tool being silently dropped from (or added to) the default,
    all-tools server without a matching contract update.

    As of Phase 3, main.py registers tools via a loop over
    pmchaser.mcp.profiles.tools_for_profile(...) rather than one static
    mcp.add_tool(tools.xxx) call per tool - a per-tool AST scan of
    main.py's source (this test's original approach) can no longer see
    16 individual calls to check, so this instead calls the actual
    function that decides what gets registered. The equivalent guarantee
    for the per-profile (non-default) case lives in
    tests/test_mcp_contract.py, which boots real servers with
    PM_CHASER_TOOL_PROFILE set and checks their live advertised schema -
    a stronger check than parsing source, and the only way to verify a
    profile's server actually serves what pmchaser/mcp/profiles.py claims.
    """
    from pmchaser.mcp.profiles import tools_for_profile

    registered = set(tools_for_profile(None))

    assert registered == set(TOOL_NAMES), (
        f"tools_for_profile(None) returns {sorted(registered)}, which "
        f"doesn't match the expected 16-tool contract {sorted(TOOL_NAMES)} "
        f"- main.py's default (no PM_CHASER_TOOL_PROFILE set) registers "
        f"exactly this set."
    )
