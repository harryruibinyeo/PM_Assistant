"""create_task / create_tasks_bulk / list_tasks / update_task /
reassign_task / delete_task.

`create_tasks_bulk` was entirely untested in the original suite (confirmed
by exploration of server/test_tools.py) — covered here for the first time.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# create_task
# ---------------------------------------------------------------------------
def test_create_task_accepts_lowercase_owner_name(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    task = tools.create_task("Q3 report", "alice", "high", deadline="2026-08-10T17:00:00")
    assert "task_id" in task


def test_priority_is_required(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    import pytest

    with pytest.raises(TypeError):
        tools.create_task("No priority", "Alice")


def test_priority_must_be_valid(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    result = tools.create_task("Bad priority", "Alice", "urgent")
    assert "error" in result


def test_naive_deadline_is_stored_as_local_time_not_utc(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    task = tools.create_task("Q3 report", "Alice", "high", deadline="2026-08-10T17:00:00")
    # PM_CHASER_TZ=Asia/Singapore (UTC+8) is set by the fresh_db fixture.
    assert task["deadline_local"].startswith("2026-08-10T17:00:00")
    assert task["deadline_utc"].startswith("2026-08-10T09:00:00")


def test_unlinked_owner_produces_a_warning(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    task = tools.create_task("Q3 report", "Alice", "high", deadline="2026-08-10T17:00:00")
    assert "warning" in task


def test_create_task_for_unknown_owner_errors(fresh_db):
    tools = fresh_db
    result = tools.create_task("X", "Nobody", "medium")
    assert "error" in result


def test_create_task_deduplicates_within_the_window(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    first = tools.create_task("Finish deck", "Alice", "high", deadline="2026-08-10T17:00:00")
    second = tools.create_task("Finish deck", "Alice", "high", deadline="2026-08-10T17:00:00")
    assert second["task_id"] == first["task_id"]
    assert second.get("duplicate_of_existing") is True


# ---------------------------------------------------------------------------
# create_tasks_bulk (previously untested)
# ---------------------------------------------------------------------------
def test_create_tasks_bulk_creates_every_ready_row(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    tools.register_person("Bob")

    result = tools.create_tasks_bulk([
        {"title": "Row 1", "owner_name": "Alice", "priority": "high"},
        {"title": "Row 2", "owner_name": "Bob", "priority": "low"},
    ])

    assert len(result["created"]) == 2
    assert result["failed"] == []
    assert result["summary"] == "2 created"


def test_create_tasks_bulk_reports_failures_without_aborting_the_batch(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")

    result = tools.create_tasks_bulk([
        {"title": "Good row", "owner_name": "Alice", "priority": "high"},
        {"title": "Bad row - unregistered owner", "owner_name": "Nobody", "priority": "medium"},
        {"title": "Bad row - bad priority", "owner_name": "Alice", "priority": "urgent"},
    ])

    assert len(result["created"]) == 1
    assert len(result["failed"]) == 2
    assert result["summary"] == "1 created, 2 failed"
    # Every failure carries back the original row so the caller can fix and
    # retry only that row, per create_tasks_bulk's own docstring contract.
    for failure in result["failed"]:
        assert "input" in failure
        assert "error" in failure


def test_create_tasks_bulk_does_not_create_any_row_more_than_once(fresh_db):
    """Guards the exact incident create_tasks_bulk exists to prevent (see
    PROJECT_MANAGEMENT.md): the same batch called once should never produce
    duplicate rows even when two rows in the same call are identical."""
    tools = fresh_db
    tools.register_person("Alice")

    rows = [{"title": "Same task", "owner_name": "Alice", "priority": "high",
             "deadline": "2026-08-10T17:00:00"}] * 4
    result = tools.create_tasks_bulk(rows)

    # The in-tools duplicate guard (10-minute window, see
    # _DUPLICATE_TASK_WINDOW_MINUTES) collapses repeats within one batch to
    # a single real row plus `duplicate_of_existing` markers - it must
    # never silently create four separate tasks for one batch of repeats.
    all_tasks = tools.list_tasks(owner_name="Alice")
    assert len(all_tasks) == 1


# ---------------------------------------------------------------------------
# list_tasks
# ---------------------------------------------------------------------------
def test_list_tasks_carries_full_chase_state(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    tools.create_task("Q3 report", "Alice", "high", deadline="2026-08-10T17:00:00")

    tasks = tools.list_tasks()
    assert len(tasks) == 1
    task = tasks[0]
    assert task["owner_is_linked"] is False
    assert task["total_checkins"] == 0
    assert task["last_checkin_answered"] is None


def test_list_tasks_active_excludes_done_and_cancelled(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    t1 = tools.create_task("Done task", "Alice", "medium")["task_id"]
    t2 = tools.create_task("Cancelled task", "Alice", "medium")["task_id"]
    t3 = tools.create_task("Open task", "Alice", "medium")["task_id"]
    tools.update_task(t1, status="done")
    tools.update_task(t2, status="cancelled")

    active_titles = {t["title"] for t in tools.list_tasks(filter="active")}
    assert active_titles == {"Open task"}
    assert len(tools.list_tasks(filter="all")) == 3


# ---------------------------------------------------------------------------
# update_task
# ---------------------------------------------------------------------------
def test_update_task_changes_fields_left_others_untouched(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    tools.register_person("Bob")
    task_id = tools.create_task("Original title", "Alice", "medium")["task_id"]

    tools.update_task(task_id, deadline="2026-09-01T09:00:00")
    tools.update_task(task_id, priority="high")
    result = tools.update_task(task_id, title="New title")

    assert result["title"] == "New title"
    assert result["priority"] == "high"
    assert result["deadline_local"].startswith("2026-09-01T09:00:00")


def test_update_task_can_reassign_owner(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    tools.register_person("Bob")
    task_id = tools.create_task("Task", "Alice", "medium")["task_id"]
    result = tools.update_task(task_id, owner_name="Bob")
    assert result["owner_name"] == "Bob"


def test_status_done_forces_progress_to_100(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    task_id = tools.create_task("Task", "Alice", "medium")["task_id"]
    result = tools.update_task(task_id, status="done")
    assert result["progress_pct"] == 100


def test_100_percent_without_done_is_left_alone(fresh_db):
    """100% while still in_progress is legitimate (finished, awaiting
    review) - only the reverse direction (done => 100%) is enforced."""
    tools = fresh_db
    tools.register_person("Alice")
    task_id = tools.create_task("Task", "Alice", "medium")["task_id"]
    result = tools.update_task(task_id, progress_pct=100)
    assert result["status"] == "not_started"
    assert result["progress_pct"] == 100


def test_update_task_on_unknown_id_errors(fresh_db):
    tools = fresh_db
    assert "error" in tools.update_task(99999, status="done")


# ---------------------------------------------------------------------------
# reassign_task
# ---------------------------------------------------------------------------
def test_reassign_task_moves_ownership_and_reports_origin(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    tools.register_person("Bob")
    task_id = tools.create_task("Task", "Alice", "medium")["task_id"]

    result = tools.reassign_task(task_id, "Bob")

    assert result["owner_name"] == "Bob"
    assert result["reassigned_from"] == "Alice"


def test_reassign_task_does_not_touch_other_fields(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    tools.register_person("Bob")
    task_id = tools.create_task("Original title", "Alice", "high")["task_id"]

    result = tools.reassign_task(task_id, "Bob")

    assert result["title"] == "Original title"
    assert result["priority"] == "high"


def test_reassign_task_unknown_task_errors(fresh_db):
    tools = fresh_db
    tools.register_person("Bob")
    assert "error" in tools.reassign_task(99999, "Bob")


def test_reassign_task_unregistered_new_owner_errors(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    task_id = tools.create_task("Task", "Alice", "medium")["task_id"]
    assert "error" in tools.reassign_task(task_id, "Nobody")


# ---------------------------------------------------------------------------
# delete_task
# ---------------------------------------------------------------------------
def test_delete_task_removes_it(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    task_id = tools.create_task("Task", "Alice", "medium")["task_id"]

    result = tools.delete_task(task_id)

    assert result["deleted"] is True
    assert tools.list_tasks() == []


def test_delete_task_twice_errors(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    task_id = tools.create_task("Task", "Alice", "medium")["task_id"]
    tools.delete_task(task_id)
    assert "error" in tools.delete_task(task_id)


def test_delete_task_uses_exact_match_not_substring_for_unmatched_cleanup(fresh_db, fake_telegram):
    """Regression test for the refactor plan's finding #1: deleting task 1
    must not delete an unmatched message whose real candidates are
    [11, 21] just because "1" is a substring of both id's text form -
    the original code matched with `candidate_task_ids.like(f"%{task_id}%")`
    against the JSON-encoded column."""
    from tests.helpers import link

    tools = fresh_db
    link(tools, fake_telegram, "Alice", "555001")

    task_1 = tools.create_task("Target task", "Alice", "medium")["task_id"]
    assert task_1 == 1

    for i in range(9):
        tools.create_task(f"Filler {i}", "Alice", "medium")
    task_11 = tools.create_task("Task eleven", "Alice", "medium")["task_id"]
    assert task_11 == 11

    for i in range(9, 18):
        tools.create_task(f"Filler {i}", "Alice", "medium")
    task_21 = tools.create_task("Task twenty-one", "Alice", "medium")["task_id"]
    assert task_21 == 21

    tools.telegram_send_message("Alice", "ping 11", task_id=task_11)
    tools.telegram_send_message("Alice", "ping 21", task_id=task_21)

    fake_telegram.push("555001", "done with one of these")
    unmatched = tools.telegram_get_updates()["unmatched"]
    assert len(unmatched) == 1
    assert set(unmatched[0]["candidate_task_ids"]) == {11, 21}

    tools.delete_task(task_1)

    fake_telegram.push("555001", "unrelated noise, just to poll again")
    still_pending = tools.telegram_get_updates()["unmatched"]
    # The message about tasks 11/21 must survive - task 1 was never a real
    # candidate, only a substring of "11" and "21".
    assert any(set(u["candidate_task_ids"]) == {11, 21} for u in still_pending)
