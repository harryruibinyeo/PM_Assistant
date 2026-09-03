"""Pure validation/generation helpers with no DB or Telegram dependency."""

from __future__ import annotations

import secrets

from pmchaser.domain.constants import LINK_CODE_ALPHABET, VALID_PRIORITIES


def validate_priority(priority: str) -> str | None:
    """None if valid, else an error message."""
    if priority not in VALID_PRIORITIES:
        return f"priority must be one of {VALID_PRIORITIES}, got '{priority}'."
    return None


def generate_link_code(length: int = 6) -> str:
    """A link code is a real credential - it's what someone types (or
    taps, via build_link_url) to bind their own Telegram account to a
    person record with no further authentication. Phase 2 fix (refactor
    plan finding #3): the original used `random.choices`, seeded from
    Python's non-cryptographic Mersenne Twister PRNG - fine for picking a
    random UI color, not for something that grants account access.
    `secrets` draws from the OS CSPRNG instead. See
    pmchaser/services/people.py's register_person for the collision-retry
    loop this alone doesn't provide - `secrets` makes a code
    unpredictable, not unique; the DB's own UNIQUE constraint is still
    the source of truth for uniqueness.
    """
    return "".join(secrets.choice(LINK_CODE_ALPHABET) for _ in range(length))
