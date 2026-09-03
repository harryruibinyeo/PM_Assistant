#!/usr/bin/env python
"""Phase 0 performance baseline: per-tool wall time, SQL query counts, and
tool-schema token counts, measured (not guessed) against real data and,
where reachable, the real local LLM.

Run from server/:
    .venv/Scripts/python.exe tests/perf/benchmark.py [--tasks 200] [--people 50]

Every later phase re-runs this against the same seed and reports the delta
- see the plan's Phase 2/3 verification requirements. This script makes no
production code changes and asserts nothing; it only measures and prints.
"""

from __future__ import annotations

import argparse
import inspect
import json
import random
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

LM_STUDIO_URL = "http://192.168.1.8:1234/v1"


# ---------------------------------------------------------------------------
# Setup: an isolated scratch DB with realistic seed data, and a query counter
# ---------------------------------------------------------------------------
def setup(n_people: int, n_tasks: int):
    import os

    db_path = Path(tempfile.gettempdir()) / f"pm_chaser_bench_{uuid.uuid4().hex}.db"
    os.environ["PM_CHASER_DB_PATH"] = str(db_path)
    os.environ["PM_CHASER_TZ"] = "Asia/Singapore"
    os.environ.pop("TELEGRAM_BOT_TOKEN", None)

    from pmchaser.db import base as db_base
    from pmchaser.db.models import Person
    from pmchaser.integrations import telegram as telegram_integration
    from pmchaser.mcp import tools

    db_base.configure_for_testing(str(db_path), tz_name="Asia/Singapore")
    db_base.init_db()

    class _FakeTelegram:
        def __init__(self):
            self._mid = 100

        def send_message(self, chat_id, text):
            self._mid += 1
            return {"ok": True, "result": {"message_id": self._mid}}

        def get_updates(self, offset=None, timeout=0):
            return []

    fake = _FakeTelegram()
    telegram_integration.send_message = fake.send_message
    telegram_integration.get_updates = fake.get_updates

    tools.register_person("Bob", role="manager")
    people = []
    for i in range(n_people):
        p = tools.register_person(f"Person{i}")
        # Half linked, half not - exercises both the "chase" and
        # "unreachable" branches at realistic scale.
        if i % 2 == 0:
            with db_base.session_scope() as session:
                from sqlalchemy import select
                row = session.execute(
                    select(Person).where(Person.id == p["person_id"])
                ).scalar_one()
                row.telegram_chat_id = f"90000{i}"
        people.append(p)

    priorities = ["high", "medium", "low"]
    for i in range(n_tasks):
        owner = people[i % len(people)]["name"]
        days_offset = random.choice([-3, -1, 0, 2, 10])
        deadline = f"2026-08-{max(1, 14 + days_offset):02d}T09:00:00"
        task = tools.create_task(
            f"Task {i}", owner, priorities[i % 3], deadline=deadline,
        )
        if "task_id" in task and i % 5 == 0:
            tools.telegram_send_message(owner, "checking in", task_id=task["task_id"])

    return db_base, tools, db_path


class QueryCounter:
    """Counts SQL statements executed via SQLAlchemy's event hooks -
    the concrete number behind finding #6 (N+1 queries)."""

    def __init__(self, engine):
        self.engine = engine
        self.count = 0
        self._listener = None

    def __enter__(self):
        from sqlalchemy import event

        def _before_cursor_execute(*args, **kwargs):
            self.count += 1

        self._listener = _before_cursor_execute
        event.listen(self.engine, "before_cursor_execute", self._listener)
        return self

    def __exit__(self, *exc):
        from sqlalchemy import event

        event.remove(self.engine, "before_cursor_execute", self._listener)


def time_and_count(db_base, label, fn, *args, **kwargs):
    with QueryCounter(db_base.engine) as qc:
        start = time.perf_counter()
        result = fn(*args, **kwargs)
        elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"  {label:<28} {elapsed_ms:8.2f} ms   {qc.count:4d} SQL statements")
    return result


# ---------------------------------------------------------------------------
# Prompt-token accounting (approximate - see the printed caveat)
# ---------------------------------------------------------------------------
def tool_schema_char_counts(tools_mod) -> dict:
    names_by_profile = {
        "pmchaser-bot (5 tools it actually uses)": [
            "get_chase_plan", "update_task", "telegram_send_message",
            "resolve_unmatched", "notify_manager",
        ],
        "task-manager-bot (all 16, current)": [
            "get_chase_plan", "chase_now", "get_digest_data", "create_task",
            "create_tasks_bulk", "list_tasks", "update_task", "reassign_task",
            "delete_task", "register_person", "delete_person", "list_people",
            "telegram_send_message", "telegram_get_updates",
            "resolve_unmatched", "notify_manager",
        ],
        "pmchaser-bot (all 16, CURRENT - the bug)": [
            "get_chase_plan", "chase_now", "get_digest_data", "create_task",
            "create_tasks_bulk", "list_tasks", "update_task", "reassign_task",
            "delete_task", "register_person", "delete_person", "list_people",
            "telegram_send_message", "telegram_get_updates",
            "resolve_unmatched", "notify_manager",
        ],
    }
    out = {}
    for profile, names in names_by_profile.items():
        total_chars = 0
        for name in names:
            fn = getattr(tools_mod, name)
            doc = inspect.getdoc(fn) or ""
            sig = str(inspect.signature(fn))
            total_chars += len(doc) + len(sig) + len(name)
        out[profile] = total_chars
    return out


def probe_lm_studio_latency(trials: int = 3) -> None:
    """Best-effort real round-trip against the configured LM Studio
    endpoint: a small prompt vs. a large one, repeated over several trials
    with a random nonce in every prompt.

    The nonce matters: an early version of this probe reused the literal
    same prompt text across runs and got wildly inconsistent numbers
    (743 ms/1K tokens on one run, 5.4 ms/1K on the next, from the same
    machine and model) because LM Studio's own KV-prefix caching was
    reusing the previous run's cached prefill for the repeated text - a
    real, live demonstration of the "KV-cache stability" latency lever in
    the refactor plan, not just server noise. Forcing unique content per
    trial measures actual cold prefill cost instead of a cache hit.
    Skips cleanly if unreachable."""
    try:
        import httpx2
    except ImportError:
        print("  (httpx2 not available for the LM Studio probe - skipped)")
        return

    def _time_completion(prompt: str) -> float | None:
        try:
            start = time.perf_counter()
            resp = httpx2.post(
                f"{LM_STUDIO_URL}/chat/completions",
                json={
                    "model": "qwen/qwen3.6-35b-a3b",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 8,
                    "temperature": 0,
                },
                timeout=60,
            )
            resp.raise_for_status()
            return (time.perf_counter() - start) * 1000
        except Exception as exc:  # noqa: BLE001 - best-effort probe
            print(f"  LM Studio unreachable/errored ({exc}) - skipping probe")
            return None

    deltas_per_1k = []
    for i in range(trials):
        nonce = random.randint(100000, 999999)
        small_prompt = f"Reply with only the word OK. nonce={nonce}"
        # ~1700 unique tokens of filler, never repeated across trials.
        filler = " ".join(f"filler{random.randint(0, 999999)}" for _ in range(1200))
        large_prompt = f"{small_prompt} {filler}"

        small_ms = _time_completion(small_prompt)
        if small_ms is None:
            return
        large_ms = _time_completion(large_prompt)
        if large_ms is None:
            return

        delta_per_1k = (large_ms - small_ms) / 1.7  # ~1700 extra tokens
        deltas_per_1k.append(delta_per_1k)
        print(
            f"  trial {i + 1}: small {small_ms:7.1f} ms, "
            f"large(+~1.7K unique tok) {large_ms:8.1f} ms, "
            f"delta/1K {delta_per_1k:7.1f} ms"
        )

    if deltas_per_1k:
        median = sorted(deltas_per_1k)[len(deltas_per_1k) // 2]
        print(f"  median cold-prefill cost: ~{median:.0f} ms per 1K uncached prompt tokens")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--people", type=int, default=50)
    parser.add_argument("--tasks", type=int, default=200)
    parser.add_argument("--skip-lm-studio", action="store_true")
    args = parser.parse_args()

    random.seed(42)
    print(f"Seeding {args.people} people / {args.tasks} tasks into a scratch DB...")
    db_base, tools, db_path = setup(args.people, args.tasks)

    print("\n== Tool wall time + SQL query counts ==")
    time_and_count(db_base, "list_tasks(filter=all)", tools.list_tasks, filter="all")
    time_and_count(db_base, "get_chase_plan()", tools.get_chase_plan)
    time_and_count(db_base, "get_digest_data()", tools.get_digest_data)
    time_and_count(db_base, "chase_now(one person)", tools.chase_now, "Person0")

    print("\n== Tool-schema size per profile (docstring + signature chars, proxy for prompt tokens) ==")
    print("   Caveat: not the real qwen3.6 tokenizer - char count / 4 is a rough,")
    print("   commonly-used approximation. Directionally correct, not exact.")
    for profile, chars in tool_schema_char_counts(tools).items():
        print(f"  {profile:<42} ~{chars:6d} chars  (~{chars // 4:5d} tokens, approx.)")

    if not args.skip_lm_studio:
        print(f"\n== Real LM Studio probe ({LM_STUDIO_URL}) ==")
        probe_lm_studio_latency()

    db_base.engine.dispose()
    for suffix in ("", "-journal", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    main()
