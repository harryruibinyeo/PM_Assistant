"""Pure validation/generation helpers with no DB or Telegram dependency."""

from __future__ import annotations

import random

from pmchaser.domain.constants import LINK_CODE_ALPHABET, VALID_PRIORITIES


def validate_priority(priority: str) -> str | None:
    """None if valid, else an error message."""
    if priority not in VALID_PRIORITIES:
        return f"priority must be one of {VALID_PRIORITIES}, got '{priority}'."
    return None


def generate_link_code(length: int = 6) -> str:
    return "".join(random.choices(LINK_CODE_ALPHABET, k=length))
