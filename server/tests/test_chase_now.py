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
