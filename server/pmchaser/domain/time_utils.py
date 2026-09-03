"""Datetime parsing/rendering helpers.

Deliberately pure functions parameterized by `local_tz` (the caller passes
`db_base.LOCAL_TZ`) rather than reaching into a module-level global
directly - keeps these testable in isolation and avoids the "value
captured at import time" trap documented in pmchaser/db/base.py.

Two conventions worth knowing when reading this file:

* Datetimes are stored naive-UTC (see pmchaser/db/base.py). Input is
  converted from local time on the way in, and output carries both a UTC
  and a local rendering plus pre-computed hour deltas - the agent should
  never have to do date arithmetic itself, because LLMs are unreliable at
  it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def parse_dt(value: str | None, local_tz: ZoneInfo) -> datetime | None:
    """Parse an ISO 8601 string to naive UTC.

    A string carrying an explicit offset is converted from it. A naive
    string is interpreted in `local_tz`, NOT as UTC - otherwise "Friday
    5pm" typed by someone in UTC+8 would silently land at 1am Saturday
    their time.
    """
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def iso_utc(dt: datetime | None) -> str | None:
    return (dt.isoformat() + "Z") if dt else None


def iso_local(dt: datetime | None, local_tz: ZoneInfo) -> str | None:
    """Render a stored naive-UTC datetime in `local_tz`."""
    if not dt:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(local_tz).isoformat()


def hours_between(later: datetime | None, earlier: datetime | None) -> float | None:
    if not later or not earlier:
        return None
    return round((later - earlier).total_seconds() / 3600.0, 2)
