"""chase_now(): the manual, floor-and-window-bypassing override."""

from __future__ import annotations

from datetime import timedelta

from tests.helpers import link, local_iso


def test_chase_now_bypasses_the_reping_floor(fresh_db, fake_telegram):
    tools = fresh_db
    link(tools, fake_telegram, "Alice", "555001")
    task_id = tools.create_task(
        "Task", "Alice", "high", deadline=local_iso(-timedelta(hours=2))
    )["task_id"]
    tools.telegram_send_message("Alice", "just pinged", task_id=task_id)

    result = tools.chase_now("Alice")

    assert task_id in [t["task_id"] for t in result["tasks"]]


def test_chase_now_reports_reachable_owner(fresh_db, fake_telegram):
    tools = fresh_db
    link(tools, fake_telegram, "Alice", "555001")
    tools.create_task("Task", "Alice", "high", deadline=local_iso(-timedelta(hours=2)))

    result = tools.chase_now("Alice")

    assert result["reachable"] is True


def test_chase_now_flags_unlinked_owner_with_link_code(fresh_db, fake_telegram):
    tools = fresh_db
    tools.register_person("Carol")

    result = tools.chase_now("Carol")

    assert result["reachable"] is False
    assert result["link_code"]


def test_chase_now_unknown_person_errors(fresh_db, fake_telegram):
    tools = fresh_db
    assert "error" in tools.chase_now("Nobody")


def test_chase_now_includes_a_task_30_days_out(fresh_db, fake_telegram):
    """The scheduled sweep's due-soon window doesn't apply to an explicit
    manual request - the manager already decided now is the right time."""
    tools = fresh_db
    link(tools, fake_telegram, "Alice", "555001")
    task_id = tools.create_task(
        "Far future", "Alice", "medium", deadline=local_iso(timedelta(days=30))
    )["task_id"]

    result = tools.chase_now("Alice")

    assert task_id in [t["task_id"] for t in result["tasks"]]


def test_chase_now_includes_a_task_with_no_deadline_at_all(fresh_db, fake_telegram):
    tools = fresh_db
    link(tools, fake_telegram, "Alice", "555001")
    task_id = tools.create_task("No deadline", "Alice", "medium")["task_id"]

    result = tools.chase_now("Alice")

    assert task_id in [t["task_id"] for t in result["tasks"]]


def test_chase_now_still_excludes_a_blocked_task(fresh_db, fake_telegram):
    tools = fresh_db
    link(tools, fake_telegram, "Alice", "555001")
    task_id = tools.create_task(
        "Task", "Alice", "high", deadline=local_iso(-timedelta(hours=2))
    )["task_id"]
    tools.update_task(task_id, status="blocked")

    result = tools.chase_now("Alice")

    assert task_id not in [t["task_id"] for t in result["tasks"]]
    assert any(s["task_id"] == task_id for s in result["skipped"])


def test_the_scheduled_sweep_correctly_ignores_the_same_far_future_task(fresh_db, fake_telegram):
    """Sanity check that get_chase_plan and chase_now genuinely differ on
    the same data, not just in isolation from each other."""
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    link(tools, fake_telegram, "Alice", "555001")
    task_id = tools.create_task(
        "Far future", "Alice", "medium", deadline=local_iso(timedelta(days=30))
    )["task_id"]

    plan = tools.get_chase_plan()
    manual = tools.chase_now("Alice")

    assert task_id not in [c["task_id"] for c in plan["to_chase"]]
    assert task_id in [t["task_id"] for t in manual["tasks"]]


def test_chase_now_with_matched_tasks_carries_an_action_required_reminder(fresh_db, fake_telegram):
    """Regression test for a real live incident: the model called
    chase_now, got real matched tasks back, then told the manager a
    message had been sent without ever calling telegram_send_message -
    "confident-but-false-confirmation." This reminder is a second,
    proximate line of defense on top of SOUL.md rule 12, added directly
    to the response the model reads at that exact decision point. Must
    name the specific tool call and the most urgent task's id."""
    tools = fresh_db
    link(tools, fake_telegram, "Alice", "555001")
    task_id = tools.create_task(
        "Task", "Alice", "high", deadline=local_iso(-timedelta(hours=2))
    )["task_id"]

    result = tools.chase_now("Alice")

    assert "action_required" in result
    assert "telegram_send_message" in result["action_required"]
    assert f"task_id={task_id}" in result["action_required"]
    assert "Alice" in result["action_required"]


def test_chase_now_with_no_matched_tasks_has_no_action_required(fresh_db, fake_telegram):
    """No task to chase means nothing needs sending - the field must not
    appear (its absence is itself meaningful: "nothing left to do")."""
    tools = fresh_db
    link(tools, fake_telegram, "Alice", "555001")
    # No tasks created for Alice at all.

    result = tools.chase_now("Alice")

    assert "action_required" not in result
    assert "message" in result  # the existing "nothing to chase" explanation


def test_chase_now_unreachable_owner_has_no_action_required(fresh_db, fake_telegram):
    """An unreachable owner can't be sent anything regardless - the field
    would be actively misleading here, so it must not appear."""
    tools = fresh_db
    tools.register_person("Carol")  # never linked
    tools.create_task("Task", "Carol", "medium", deadline=local_iso(-timedelta(hours=1)))

    result = tools.chase_now("Carol")

    assert result["reachable"] is False
    assert "action_required" not in result


def test_chase_now_blocked_only_tasks_has_no_action_required(fresh_db, fake_telegram):
    """Every open task blocked means nothing left to chase - same as the
    empty-tasks case, the field must not appear."""
    tools = fresh_db
    link(tools, fake_telegram, "Alice", "555001")
    task_id = tools.create_task(
        "Task", "Alice", "high", deadline=local_iso(-timedelta(hours=2))
    )["task_id"]
    tools.update_task(task_id, status="blocked")

    result = tools.chase_now("Alice")

    assert result["tasks"] == []
    assert "action_required" not in result
