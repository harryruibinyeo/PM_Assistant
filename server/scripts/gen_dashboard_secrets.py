#!/usr/bin/env python
"""Generates the two secrets the Phase 5 web dashboard needs, entirely
locally - the password you choose is never transmitted or stored anywhere
but the .env you paste this output into.

    DASHBOARD_PASSWORD_HASH  - bcrypt hash of a password you type here now
    DASHBOARD_SECRET_KEY     - random key used to sign the session cookie

Run from server/:
    .venv/Scripts/python.exe scripts/gen_dashboard_secrets.py

Requires bcrypt, which is a dashboard-only dependency (see the Phase 5
requirements file once it exists) - installed on demand below with a clear
message if missing, rather than bloating the core server's dependencies
for a feature that doesn't exist yet on this branch.
"""

from __future__ import annotations

import getpass
import secrets
import sys


def main() -> None:
    try:
        import bcrypt
    except ImportError:
        print(
            "bcrypt isn't installed. Install it once with:\n"
            "  .venv/Scripts/python.exe -m pip install bcrypt\n"
            "then re-run this script.",
            file=sys.stderr,
        )
        sys.exit(1)

    password = getpass.getpass("Choose the dashboard password (not echoed): ")
    confirm = getpass.getpass("Confirm: ")
    if password != confirm:
        print("Passwords didn't match - nothing generated.", file=sys.stderr)
        sys.exit(1)
    if len(password) < 8:
        print("That's under 8 characters - choose something longer.", file=sys.stderr)
        sys.exit(1)

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    secret_key = secrets.token_urlsafe(32)

    print()
    print("Add these two lines to your .env (never commit them):")
    print()
    print(f"DASHBOARD_PASSWORD_HASH={password_hash}")
    print(f"DASHBOARD_SECRET_KEY={secret_key}")
    print()
    print("The plaintext password above was never written to disk or logged - "
          "it only ever existed in this process's memory.")


if __name__ == "__main__":
    main()
