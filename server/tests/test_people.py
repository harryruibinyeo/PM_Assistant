"""register_person / list_people / delete_person.

Ported from the "people" and "delete_person" sections of the original
server/test_tools.py, as real pytest assertions instead of the script's
check()/failures pattern.
"""

from __future__ import annotations


def test_register_person_returns_link_code(fresh_db):
    tools = fresh_db
    alice = tools.register_person("Alice", telegram_username="alice_tg")
    assert "link_code" in alice
    assert alice["role"] == "team_member"


def test_duplicate_registration_is_rejected(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    assert "error" in tools.register_person("Alice")


def test_duplicate_check_is_case_insensitive(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    assert "error" in tools.register_person("alice")


def test_list_people_sees_everyone(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    tools.register_person("Bob", role="manager")
    tools.register_person("Carol")
    people = tools.list_people()
    assert len(people) == 3
    assert all(not p["is_linked"] for p in people)


def test_list_people_filters_by_role(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    tools.register_person("Bob", role="manager")
    managers = tools.list_people(role="manager")
    assert len(managers) == 1
    assert managers[0]["name"] == "Bob"


def test_delete_person_removes_a_task_free_person(fresh_db):
    tools = fresh_db
    tools.register_person("Carol")
    result = tools.delete_person("Carol")
    assert result["deleted"] is True
    assert tools.list_people() == []


def test_delete_unknown_person_errors(fresh_db):
    tools = fresh_db
    assert "error" in tools.delete_person("Nobody")


def test_delete_person_owning_a_task_is_refused(fresh_db):
    tools = fresh_db
    tools.register_person("Alice")
    tools.create_task("Q3 report", "Alice", "high", deadline="2026-08-10T17:00:00")

    result = tools.delete_person("Alice")

    assert "error" in result
    # Refusal must not have removed the person as a side effect.
    assert any(p["name"] == "Alice" for p in tools.list_people())
