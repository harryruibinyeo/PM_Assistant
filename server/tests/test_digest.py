"""get_digest_data(): everything the manager's digest needs, pre-gathered
but not pre-judged."""

from __future__ import annotations

from datetime import timedelta

from tests.helpers import backdate_last_checkin, link, local_iso


def test_blocked_task_surfaces_in_the_digest_with_the_owners_own_words(fresh_db, fake_telegram):
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    link(tools, fake_telegram, "Alice", "555001")
    task_id = tools.create_task(
        "Task", "Alice", "high", deadline=local_iso(-timedelta(hours=5))
    )["task_id"]
    tools.telegram_send_message("Alice", "checking in", task_id=task_id)
    fake_telegram.push("555001", "waiting on procurement")
    tools.telegram_get_updates()
    tools.update_task(task_id, status="blocked")

    digest = tools.get_digest_data()

    blocked = digest["blocked_needing_decision"]
    assert len(blocked) == 1
    assert blocked[0]["reason_given"] == "waiting on procurement"
    assert blocked[0]["deadline_already_passed"] is True


def test_digest_looks_up_the_manager_rather_than_guessing(fresh_db, fake_telegram):
    tools = fresh_db
    tools.register_person("Bob", role="manager")

    digest = tools.get_digest_data()

    assert digest["manager_name"] == "Bob"


def test_digest_counts_overdue_and_lists_active_sorted_most_urgent_first(fresh_db, fake_telegram):
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    link(tools, fake_telegram, "Alice", "555001")
    urgent = tools.create_task(
        "Urgent", "Alice", "high", deadline=local_iso(-timedelta(hours=5))
    )["task_id"]
    later = tools.create_task(
        "Later", "Alice", "low", deadline=local_iso(timedelta(hours=48))
    )["task_id"]

    digest = tools.get_digest_data()

    assert digest["overdue_count"] == 1
    assert [t["task_id"] for t in digest["active_tasks"]] == [urgent, later]


def test_digest_flags_unresponsive_people(fresh_db, fake_telegram):
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    link(tools, fake_telegram, "Erin", "555003")
    task_id = tools.create_task(
        "Task", "Erin", "high", deadline=local_iso(-timedelta(hours=20))
    )["task_id"]
    for _ in range(2):
        tools.telegram_send_message("Erin", "checking in", task_id=task_id)
        backdate_last_checkin(task_id, hours_ago=2)

    digest = tools.get_digest_data()

    assert any(p["owner_name"] == "Erin" for p in digest["unresponsive"])


def test_digest_flags_unreachable_people_who_own_open_work(fresh_db, fake_telegram):
    tools = fresh_db
    tools.register_person("Bob", role="manager")
    tools.register_person("Carol")  # never linked
    tools.create_task("Task", "Carol", "medium", deadline=local_iso(-timedelta(hours=1)))

    digest = tools.get_digest_data()

    assert any(p["name"] == "Carol" for p in digest["unreachable_people"])


def test_digest_does_not_pre_judge_what_is_at_risk(fresh_db, fake_telegram):
    """get_digest_data gathers and computes hours_until_deadline etc, but
    deliberately leaves the judgment of what's 'at risk' to the model -
    confirmed by exploration of server/test_tools.py: no `at_risk` key."""
    tools = fresh_db
    tools.register_person("Bob", role="manager")

    digest = tools.get_digest_data()

    assert "at_risk" not in digest
