"""get_chase_plan(): the scheduled-sweep planning tool - eligibility,
per-priority re-ping floors, escalation grouping, unreachable owners,
blocked-task exclusion, and one-task-per-person dedup.

Time control mirrors the original server/test_tools.py's technique:
deadlines are set relative to "now" via timedelta, and a check-in's
`sent_at` is backdated directly through the DB to simulate elapsed time
without sleeping (see tests/helpers.py).
"""

from __future__ import annotations

from datetime import timedelta

from tests.helpers import backdate_last_checkin, link, local_iso


def test_plan_chases_the_most_overdue_task_per_person(fresh_db, fake_telegram):
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    link(tools, fake_telegram, "Alice", "555001")

    less_overdue = tools.create_task(
        "Less overdue", "Alice", "high", deadline=local_iso(-timedelta(hours=2))
    )["task_id"]
    more_overdue = tools.create_task(
        "More overdue", "Alice", "high", deadline=local_iso(-timedelta(hours=20))
    )["task_id"]

    plan = tools.get_chase_plan()

    chased_ids = [c["task_id"] for c in plan["to_chase"]]
    assert chased_ids == [more_overdue]
    skipped_ids = [s["task_id"] for s in plan["skipped"]]
    assert less_overdue in skipped_ids


def test_plan_sends_at_most_one_task_per_person(fresh_db, fake_telegram):
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    link(tools, fake_telegram, "Alice", "555001")
    tools.create_task("A", "Alice", "high", deadline=local_iso(-timedelta(hours=2)))
    tools.create_task("B", "Alice", "high", deadline=local_iso(-timedelta(hours=3)))

    plan = tools.get_chase_plan()

    owners_chased = [c["owner_name"] for c in plan["to_chase"]]
    assert owners_chased.count("Alice") == 1


def test_task_with_no_deadline_is_never_chased(fresh_db, fake_telegram):
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    link(tools, fake_telegram, "Alice", "555001")
    tools.create_task("No deadline", "Alice", "high")  # deadline omitted

    plan = tools.get_chase_plan()

    assert plan["to_chase"] == []
    assert plan["skipped"] == []


def test_unlinked_owner_is_reported_as_unreachable_not_chased(fresh_db, fake_telegram):
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    tools.register_person("Carol")  # never linked
    tools.create_task("Task", "Carol", "high", deadline=local_iso(-timedelta(hours=2)))

    plan = tools.get_chase_plan()

    assert plan["to_chase"] == []
    assert any(u["name"] == "Carol" and u["link_code"] for u in plan["unreachable"])


def test_manager_is_resolved_for_the_plan(fresh_db, fake_telegram):
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    link(tools, fake_telegram, "Alice", "555001")
    tools.create_task("Task", "Alice", "high", deadline=local_iso(-timedelta(hours=2)))

    plan = tools.get_chase_plan()

    assert plan["manager_name"] == "Bob"
    assert plan["summary"]  # non-empty, human-readable


def test_a_just_pinged_task_is_not_re_chased(fresh_db, fake_telegram):
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    link(tools, fake_telegram, "Alice", "555001")
    task_id = tools.create_task(
        "Task", "Alice", "high", deadline=local_iso(-timedelta(hours=2))
    )["task_id"]
    tools.telegram_send_message("Alice", "checking in", task_id=task_id)

    plan = tools.get_chase_plan()

    assert plan["to_chase"] == []
    reasons = [s["reason"] for s in plan["skipped"] if s["task_id"] == task_id]
    assert reasons and "floor" in reasons[0]


def test_high_priority_task_past_its_1h_floor_is_chased_again(fresh_db, fake_telegram):
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    link(tools, fake_telegram, "Alice", "555001")
    task_id = tools.create_task(
        "Task", "Alice", "high", deadline=local_iso(-timedelta(hours=2))
    )["task_id"]
    tools.telegram_send_message("Alice", "checking in", task_id=task_id)
    backdate_last_checkin(task_id, hours_ago=2)  # high floor is 1h

    plan = tools.get_chase_plan()

    assert task_id in [c["task_id"] for c in plan["to_chase"]]


def test_medium_priority_task_still_under_its_6h_floor_is_not_rechased(fresh_db, fake_telegram):
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    link(tools, fake_telegram, "Alice", "555001")
    task_id = tools.create_task(
        "Task", "Alice", "medium", deadline=local_iso(-timedelta(hours=2))
    )["task_id"]
    tools.telegram_send_message("Alice", "checking in", task_id=task_id)
    backdate_last_checkin(task_id, hours_ago=2)  # medium floor is 6h - not past it

    plan = tools.get_chase_plan()

    assert task_id not in [c["task_id"] for c in plan["to_chase"]]


def test_3_unanswered_pings_escalates_instead_of_chasing(fresh_db, fake_telegram):
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    link(tools, fake_telegram, "Alice", "555001")
    task_id = tools.create_task(
        "Task", "Alice", "high", deadline=local_iso(-timedelta(hours=10))
    )["task_id"]
    for _ in range(3):
        tools.telegram_send_message("Alice", "checking in", task_id=task_id)
        backdate_last_checkin(task_id, hours_ago=2)

    plan = tools.get_chase_plan(max_unanswered=3)

    assert task_id not in [c["task_id"] for c in plan["to_chase"]]
    escalated_task_ids = [t["task_id"] for e in plan["to_escalate"] for t in e["tasks"]]
    assert task_id in escalated_task_ids


def test_replies_are_surfaced_for_the_agent_not_auto_applied(fresh_db, fake_telegram):
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    link(tools, fake_telegram, "Alice", "555001")
    task_id = tools.create_task(
        "Task", "Alice", "high", deadline=local_iso(-timedelta(hours=2))
    )["task_id"]
    tools.telegram_send_message("Alice", "checking in", task_id=task_id)
    fake_telegram.push("555001", "just finished it")

    plan = tools.get_chase_plan()

    assert len(plan["replies_to_interpret"]) == 1
    # get_chase_plan must not itself call update_task - the task's status
    # is untouched until the agent explicitly decides to change it.
    assert tools.list_tasks()[0]["status"] == "not_started"


def test_blocked_task_is_not_chased(fresh_db, fake_telegram):
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    link(tools, fake_telegram, "Alice", "555001")
    task_id = tools.create_task(
        "Task", "Alice", "high", deadline=local_iso(-timedelta(hours=2))
    )["task_id"]
    tools.update_task(task_id, status="blocked")

    plan = tools.get_chase_plan()

    assert plan["to_chase"] == []
    reasons = [s["reason"] for s in plan["skipped"] if s["task_id"] == task_id]
    assert reasons and "manager" in reasons[0]


def test_plan_with_to_chase_carries_an_action_required_reminder(fresh_db, fake_telegram):
    """Same regression coverage as chase_now's action_required test, for
    the scheduled-sweep path: nothing has actually been sent for a
    to_chase entry until telegram_send_message is called - the reminder
    must say so and name the tool."""
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    link(tools, fake_telegram, "Alice", "555001")
    tools.create_task("Task", "Alice", "high", deadline=local_iso(-timedelta(hours=2)))

    plan = tools.get_chase_plan()

    assert plan["to_chase"]  # sanity: this test's premise actually holds
    assert "action_required" in plan
    assert "telegram_send_message" in plan["action_required"]


def test_plan_with_to_escalate_carries_an_action_required_reminder(fresh_db, fake_telegram):
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    link(tools, fake_telegram, "Alice", "555001")
    task_id = tools.create_task(
        "Task", "Alice", "high", deadline=local_iso(-timedelta(hours=10))
    )["task_id"]
    for _ in range(3):
        tools.telegram_send_message("Alice", "checking in", task_id=task_id)
        backdate_last_checkin(task_id, hours_ago=2)

    plan = tools.get_chase_plan(max_unanswered=3)

    assert plan["to_escalate"]  # sanity: this test's premise actually holds
    assert "action_required" in plan
    assert "notify_manager" in plan["action_required"]


def test_plan_with_nothing_to_chase_or_escalate_has_no_action_required(fresh_db, fake_telegram):
    tools = fresh_db
    tools.register_person("Bob", role="manager")

    plan = tools.get_chase_plan()

    assert plan["to_chase"] == []
    assert plan["to_escalate"] == []
    assert "action_required" not in plan
