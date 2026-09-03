#!/usr/bin/env bash
# Phase 4: a real, tested backup of the live pm-chaser database.
#
# Uses SQLite's Online Backup API (sqlite3.Connection.backup() in
# Python's stdlib - the same mechanism as the sqlite3 CLI's `.backup`
# dot-command) rather than copying the .db file directly. A plain file
# copy of a WAL-mode database can capture the main file mid-write while
# missing the -wal file's not-yet-checkpointed pages, silently producing
# a corrupt or stale-looking backup - exactly the failure mode WAL mode
# (see server/pmchaser/db/base.py's _set_sqlite_pragmas) was adopted to
# avoid causing in the first place. The Online Backup API is safe to run
# against a live writer - it's the same guarantee `docker exec`-ing the
# app container mid-request already relies on implicitly.
#
# Doesn't touch the running pm-chaser-mcp container at all: runs a
# throwaway python:3.12-slim container (the same base image the app
# already uses, so it's already pulled locally) with the SAME named
# volume mounted read-only, plus this host directory mounted for the
# output file. No new dependency (no sqlite3 CLI package to install
# anywhere) - Python's stdlib sqlite3 module is enough.
#
# Usage:
#   ./backup.sh [destination-directory]     # defaults to ./backups
#
# This only ever WRITES a new timestamped file - it never deletes an
# old one. Pruning old backups is a deliberate decision left to whoever
# runs this (e.g. a cron line piping to `find ... -mtime +30 -delete`),
# not something this script does on your behalf.

set -euo pipefail

VOLUME_NAME="pm-chaser-data"
DB_PATH_IN_VOLUME="pm_chaser.db"
DEST_DIR="${1:-./backups}"

mkdir -p "$DEST_DIR"
DEST_DIR_ABS="$(cd "$DEST_DIR" && pwd)"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
OUT_FILENAME="pm_chaser_${TIMESTAMP}.db"

if ! docker volume inspect "$VOLUME_NAME" >/dev/null 2>&1; then
    echo "error: Docker volume '$VOLUME_NAME' not found - is pm-chaser-mcp deployed on this host?" >&2
    exit 1
fi

docker run --rm \
    -v "${VOLUME_NAME}:/data:ro" \
    -v "${DEST_DIR_ABS}:/backup" \
    python:3.12-slim \
    python3 -c "
import sqlite3
src = sqlite3.connect('/data/${DB_PATH_IN_VOLUME}')
dst = sqlite3.connect('/backup/${OUT_FILENAME}')
with dst:
    src.backup(dst)
src.close()
dst.close()
print('backup written: /backup/${OUT_FILENAME}')
"

echo "Backup complete: ${DEST_DIR_ABS}/${OUT_FILENAME} ($(du -h "${DEST_DIR_ABS}/${OUT_FILENAME}" | cut -f1))"
