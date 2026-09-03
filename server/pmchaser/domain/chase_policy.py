"""Shared pure logic between get_chase_plan (the scheduled sweep) and
chase_now (the manual override) - see pmchaser/services/chase.py.

Deliberately narrow: only the pieces that are genuinely, verifiably
identical between the two call sites are extracted here (the
overdue-hours arithmetic and the urgency sort key - confirmed byte-for-
byte identical in the original tools.py). get_chase_plan and chase_now
differ in real, non-cosmetic ways beyond this: different eligibility
windows, different skip-reason wording even for the same underlying
"blocked" case, and a different per-task output shape (owner_name vs
manager_name). Those stay as separate, explicit code in chase.py rather
than being forced through one over-parameterized function - that would
trade real clarity for a superficial dedup metric.
"""

from __future__ import annotations

from pmchaser.domain.constants import PRIORITY_CHASE_FLOOR_HOURS, PRIORITY_RANK


def hours_overdue(hours_until_deadline: float | None) -> float:
    """0 if not overdue (or no deadline); positive hours past due otherwise."""
    if hours_until_deadline is not None and hours_until_deadline < 0:
        return round(-hours_until_deadline, 2)
    return 0


def is_overdue(hours_until_deadline: float | None) -> bool:
    return hours_until_deadline is not None and hours_until_deadline < 0


def is_due_soon(hours_until_deadline: float | None, due_soon_hours: float) -> bool:
    return hours_until_deadline is not None and 0 <= hours_until_deadline <= due_soon_hours


def reping_floor_hours(priority: str) -> int:
    return PRIORITY_CHASE_FLOOR_HOURS.get(priority, 6)


def sort_by_urgency(entries: list[dict]) -> list[dict]:
    """Most overdue first, then by priority - the exact sort both
    get_chase_plan's `to_chase` and chase_now's `tasks` use."""
    return sorted(entries, key=lambda e: (-e["hours_overdue"], PRIORITY_RANK.get(e["priority"], 1)))
