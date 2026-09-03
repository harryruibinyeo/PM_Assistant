"""The single deterministic scenario used for golden-master snapshotting.

Not a test module itself (no test_ prefix) - imported by both the
one-time snapshot generator and by tests/test_golden_master.py, so the
generator and the assertion can never drift apart from each other.

Determinism: every timestamp-dependent field (hours_until_deadline,
hours_since_last_checkin, etc.) depends on wall-clock "now" at call time,
so the caller MUST wrap this in `freeze_time(FROZEN_INSTANT)` - see
test_golden_master.py.
"""

from __future__ import annotations

import random

FROZEN_INSTANT = "2026-08-14T04:00:00+00:00"  # = 2026-08-14T12:00:00 in Asia/Singapore (UTC+8)
RANDOM_SEED = 20260814  # register_person's link codes go through random.choices;
                          # without a fixed seed here, the golden snapshot would
                          # never replay identically.


def run_scenario(tools, fake_telegram, fake_manager_send: list[dict]) -> dict:
    """Exercises every one of the 16 tools at least once, in a fixed order,
    against a fixed scenario. Returns an ordered dict of
    {step_label: tool_return_value} suitable for JSON snapshotting.

    `fake_manager_send` is the list a fake TelegramClient.send_message
    patch appends to (see test_notify_manager.py's fake_manager_bot for
    the same pattern) - passed in so notify_manager can be exercised too.
    """
    random.seed(RANDOM_SEED)

    out: dict = {}

    out["register_bob_manager"] = tools.register_person("Bob", role="manager")
    out["register_alice"] = tools.register_person("Alice", telegram_username="alice_tg")
    out["register_carol"] = tools.register_person("Carol")  # deliberately left unlinked

    # Link Bob (manager) and Alice via the shared /start flow.
    fake_telegram.push("700001", f"/start {out['register_bob_manager']['link_code']}")
    fake_telegram.push("700002", f"/start {out['register_alice']['link_code']}")
    out["initial_updates"] = tools.telegram_get_updates()

    out["create_task_overdue_high"] = tools.create_task(
        "Finish Q3 board deck", "Alice", "high",
        deadline="2026-08-13T09:00:00",  # already overdue vs. the frozen instant
        description="Board deck for the Q3 review",
    )
    task_id = out["create_task_overdue_high"]["task_id"]

    out["create_task_low_far_future"] = tools.create_task(
        "Archive old invoices", "Alice", "low", deadline="2026-09-15T09:00:00",
    )

    out["create_task_unreachable_owner"] = tools.create_task(
        "Carol's task", "Carol", "medium", deadline="2026-08-13T09:00:00",
    )

    out["create_tasks_bulk"] = tools.create_tasks_bulk([
        {"title": "Book the venue", "owner_name": "Alice", "priority": "medium",
         "deadline": "2026-08-20T09:00:00"},
        {"title": "Bad row", "owner_name": "Nobody", "priority": "medium"},
    ])

    out["send_chase_for_overdue_task"] = tools.telegram_send_message(
        "Alice", "Quick check-in on the Q3 board deck - how's it coming?", task_id=task_id,
    )
    # A second outstanding ping (no reply-to threading used below) makes
    # Alice's next reply genuinely ambiguous, which is what exercises
    # resolve_unmatched further down - without this, a single outstanding
    # ping matches unambiguously and resolve_unmatched never fires.
    out["send_chase_for_low_priority_task"] = tools.telegram_send_message(
        "Alice", "Also, any update on archiving the old invoices?",
        task_id=out["create_task_low_far_future"]["task_id"],
    )

    fake_telegram.push("700002", "waiting on the finance numbers from Priya")
    out["get_chase_plan"] = tools.get_chase_plan()

    out["chase_now_alice"] = tools.chase_now("Alice")

    # Resolved BEFORE blocking the task, so update_task_to_blocked and
    # get_digest_data below reflect a task that genuinely carries the
    # owner's real words - not the narrower "blocked with no reason yet"
    # transient the reverse order would snapshot.
    unmatched = out["get_chase_plan"]["unmatched_to_resolve"]
    if unmatched:
        out["resolve_unmatched"] = tools.resolve_unmatched(
            unmatched[0]["unmatched_id"], task_id=task_id,
        )

    out["update_task_to_blocked"] = tools.update_task(task_id, status="blocked")

    out["get_digest_data"] = tools.get_digest_data()

    out["reassign_low_priority_task"] = tools.reassign_task(
        out["create_task_low_far_future"]["task_id"], "Carol",
    )

    out["list_tasks_all"] = tools.list_tasks(filter="all")
    out["list_people_all"] = tools.list_people()

    out["notify_manager"] = tools.notify_manager("Golden-master scenario check-in.")
    out["notify_manager_fake_sent"] = list(fake_manager_send)

    out["delete_task_low_priority"] = tools.delete_task(
        out["create_task_low_far_future"]["task_id"],
    )
    out["delete_person_carol_refused"] = tools.delete_person("Carol")

    return out
