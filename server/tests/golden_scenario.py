"""The single deterministic scenario used for golden-master snapshotting.

Not a test module itself (no test_ prefix) - imported by both the
one-time snapshot generator and by tests/test_golden_master.py, so the
generator and the assertion can never drift apart from each other.

Determinism: every timestamp-dependent field (hours_until_deadline,
hours_since_last_checkin, etc.) depends on wall-clock "now" at call time,
so the caller MUST wrap this in `freeze_time(FROZEN_INSTANT)` - see
test_golden_master.py.

link_code is a deliberate, permanent exception to full determinism as of
the Phase 2 fix for the refactor plan's finding #3: generate_link_code
moved from `random.choices` (seedable, and originally seeded right here)
to `secrets.choice` (the OS CSPRNG - a real credential shouldn't be
predictable, including by a fixed test seed). `normalize_for_comparison`
below masks every link_code value before comparison so the golden
snapshot still checks everything else byte-for-byte.
"""

from __future__ import annotations

FROZEN_INSTANT = "2026-08-14T04:00:00+00:00"  # = 2026-08-14T12:00:00 in Asia/Singapore (UTC+8)

_LINK_CODE_PLACEHOLDER = "<LINK_CODE>"


def _collect_link_codes(value, found: set[str]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "link_code" and isinstance(v, str):
                found.add(v)
            _collect_link_codes(v, found)
    elif isinstance(value, list):
        for v in value:
            _collect_link_codes(v, found)


def _mask_codes(value, codes: set[str]):
    if isinstance(value, dict):
        return {k: _mask_codes(v, codes) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_codes(v, codes) for v in value]
    if isinstance(value, str):
        masked = value
        for code in codes:
            masked = masked.replace(code, _LINK_CODE_PLACEHOLDER)
        return masked
    return value


def normalize_for_comparison(value):
    """Masks every occurrence of a generated link_code, wherever it
    appears - not just the `link_code` dict field itself, but also its
    embedded appearance inside free text like register_person's
    `instructions` ("...send /start <code> to the bot") and, were a real
    bot configured, `link_url`. A first pass collects every value found
    under a `link_code` key; a second pass replaces every occurrence of
    those exact strings anywhere in the structure. Applied independently
    to both the live result and the loaded golden file before comparing -
    see this module's docstring for why link_code can never be pinned to
    a literal value."""
    codes: set[str] = set()
    _collect_link_codes(value, codes)
    return _mask_codes(value, codes)


def run_scenario(tools, fake_telegram, fake_manager_send: list[dict]) -> dict:
    """Exercises every one of the 17 tools at least once, in a fixed order,
    against a fixed scenario. Returns an ordered dict of
    {step_label: tool_return_value} suitable for JSON snapshotting.

    `fake_manager_send` is the list a fake TelegramClient.send_message
    patch appends to (see test_notify_manager.py's fake_manager_bot for
    the same pattern) - passed in so notify_manager can be exercised too.
    """
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

    # A separate task+reply so record_reply_outcome's three-in-one
    # (update_task + ack + notify_manager) has something real to act on,
    # without touching the already-tuned narrative above.
    out["create_task_for_reply_outcome"] = tools.create_task(
        "Submit vendor report", "Alice", "medium", deadline="2026-08-20T09:00:00",
    )
    out["record_reply_outcome"] = tools.record_reply_outcome(
        out["create_task_for_reply_outcome"]["task_id"],
        reply_text="Yep, submitted it this morning.",
        ack_text="Got it, marked as done - nice work.",
        manager_note="Marked done.",
        status="done",
        progress_pct=100,
    )

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
