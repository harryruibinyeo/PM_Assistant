#!/usr/bin/env python
"""Container entrypoint: fixes /data's ownership, drops from root to the
non-root `app` user, then execs the real command.

Why this exists instead of a plain Dockerfile `USER app`: the live
`pm-chaser-data` named Docker volume was created and written to back when
this image had no USER directive at all (ran as root throughout). A named
volume keeps whatever ownership its files already have when mounted - it
is not re-chowned to match the image on every `docker run`, only
populated from the image once, the very first time the volume is created
empty. Switching straight to a non-root user would leave that existing
volume's files owned by root and every SQLite write failing with
PermissionError from the first request on. The chown below is metadata
only - it never touches file content - and is safe (idempotent, cheap:
a handful of small SQLite files) to run on every container start,
against a brand-new empty volume or the existing live one alike.

Needs no extra package (gosu/su-exec aren't in python:3.12-slim by
default and would mean an apt-get layer just for this) - os.setuid/
setgid do the same job, and python is already guaranteed present.
"""

from __future__ import annotations

import os
import pwd
import sys

APP_USER = "app"
DATA_DIR = "/data"


def main() -> None:
    pw = pwd.getpwnam(APP_USER)

    if os.path.isdir(DATA_DIR):
        for root, dirs, files in os.walk(DATA_DIR):
            os.chown(root, pw.pw_uid, pw.pw_gid)
            for name in files:
                os.chown(os.path.join(root, name), pw.pw_uid, pw.pw_gid)

    os.setgid(pw.pw_gid)
    os.setuid(pw.pw_uid)

    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
