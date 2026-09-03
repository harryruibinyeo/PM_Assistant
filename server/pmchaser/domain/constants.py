"""Constants that encode business rules - the re-ping cadence, escalation
grouping, and duplicate-creation guard windows. Pulled out of tools.py
unchanged so a future rule change (e.g. a new priority tier) touches one
place, not scattered magic numbers.
"""

from __future__ import annotations

import string

OPEN_STATUSES = ("not_started", "in_progress", "blocked")
CLOSED_STATUSES = ("done", "cancelled")

VALID_PRIORITIES = ("low", "medium", "high")

# Priority drives how often a task gets re-chased (see
# pmchaser/domain/chase_policy.py), not just display ordering.
PRIORITY_CHASE_FLOOR_HOURS = {"high": 1, "medium": 6, "low": 24}

PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}

# How recently an identical (title, owner, deadline) task must have been
# created for a repeat create_task call to be treated as an accidental
# re-submission rather than a deliberate new task. Exists because of a real
# incident: asked to batch-create 4 tasks, the local model looped
# create_task for the same row 4 times in a row before noticing - see
# create_tasks_bulk, which exists specifically so the model never has to
# loop this call itself. This is the safety net for the cases that still
# reach create_task directly (a single ad-hoc "add a task" request, a retry
# after a dropped connection, etc.).
DUPLICATE_TASK_WINDOW_MINUTES = 10

LINK_CODE_ALPHABET = string.ascii_uppercase + string.digits

# Escalate instead of chasing at this many unanswered pings in a row -
# get_chase_plan's default, still overridable per call.
DEFAULT_MAX_UNANSWERED = 3

# How far ahead counts as "due soon" - get_chase_plan/list_tasks/
# get_digest_data's default, still overridable per call.
DEFAULT_DUE_SOON_HOURS = 24
DEFAULT_AT_RISK_HOURS = 24
