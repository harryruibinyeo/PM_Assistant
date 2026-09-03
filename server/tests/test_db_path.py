"""Guards a real regression risk from the Phase 1 module split: the
default DB path (used only when PM_CHASER_DB_PATH is unset - Docker
always sets it explicitly, so this only matters for an ad-hoc local run)
was computed relative to `__file__`. Moving that computation from
server/models.py into server/pmchaser/db/base.py changes how many
directories up "server/" actually is, and every other test in this suite
always sets PM_CHASER_DB_PATH explicitly, so nothing else would have
caught a wrong default silently pointing at a new location.

Run as a subprocess (not through the fresh_db fixture, which always sets
PM_CHASER_DB_PATH) so the module is imported fresh with the env var
genuinely absent - the only way to observe the true default.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).parent.parent


def test_default_db_path_resolves_to_server_directory():
    env = dict(os.environ)
    env.pop("PM_CHASER_DB_PATH", None)

    result = subprocess.run(
        [sys.executable, "-c", "from pmchaser.db.base import DB_PATH; print(DB_PATH)"],
        cwd=str(SERVER_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    resolved = Path(result.stdout.strip())
    assert resolved == SERVER_ROOT / "pm_chaser.db", (
        f"default DB_PATH resolved to {resolved}, expected "
        f"{SERVER_ROOT / 'pm_chaser.db'} - the original server/models.py "
        f"default was 'wherever this file lives', which was server/ "
        f"itself; pmchaser/db/base.py must still resolve to server/, not "
        f"its own (nested) directory."
    )
