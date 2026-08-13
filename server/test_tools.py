"""Local, no-Kubernetes-required smoke test for the tools.

Run with:
  rm -f /tmp/pm_chaser_test.db
  PM_CHASER_DB_PATH=/tmp/pm_chaser_test.db PM_CHASER_TZ=Asia/Singapore \
    .venv/bin/python test_tools.py

Telegram is stubbed out (no real network, no bot token needed) so the
reply-matching logic — the trickiest part of this service — can be exercised
directly: threaded replies, ambiguous replies, spontaneous updates, strangers,
and Telegram's own bare /start.
"""

import sys

failures = []


def check(label, condition):
    print(f"[{'ok' if condition else 'FAIL'}] {label}")
    if not condition:
        failures.append(label)


# ---------------------------------------------------------------------------
# Telegram stub
# ---------------------------------------------------------------------------
class FakeTelegram:
    def __init__(self):
        self.queue = []
        self.sent = []
        self._message_id = 100
        self._update_id = 1000

    def send_message(self, chat_id, text):
        self._message_id += 1
        self.sent.append({"chat_id": chat_id, "text": text, "message_id": self._message_id})
        return {"ok": True, "result": {"message_id": self._message_id}}

    def get_updates(self, offset=None, timeout=0):
        out = [u for u in self.queue if offset is None or u["update_id"] >= offset]
        return out

    def push(self, chat_id, text, reply_to=None):
        self._update_id += 1
        msg = {"chat": {"id": int(chat_id)}, "text": text}
        if reply_to is not None:
            msg["reply_to_message"] = {"message_id": reply_to}
        self.queue.append({"update_id": self._update_id, "message": msg})

    def last_message_id(self):
        return self.sent[-1]["message_id"]


def main():
    import telegram_client
    from models import init_db
    import tools

    fake = FakeTelegram()
    telegram_client.send_message = fake.send_message
    telegram_client.get_updates = fake.get_updates

    init_db()

    ALICE_CHAT = "555001"
    CAROL_CHAT = "555002"
    STRANGER_CHAT = "999999"

    # ── people ────────────────────────────────────────────────────────────
    alice = tools.register_person("Alice", telegram_username="alice_tg")
    tools.register_person("Bob", role="manager")
    carol = tools.register_person("Carol")
    check("register_person returns a link_code", "link_code" in alice)
    check("duplicate registration is rejected", "error" in tools.register_person("Alice"))
    check("duplicate check is case-insensitive", "error" in tools.register_person("alice"))

    people = tools.list_people()
    check("list_people sees all three", len(people) == 3)
    check("nobody is linked yet", all(not p["is_linked"] for p in people))
    managers = tools.list_people(role="manager")
    check("list_people(role=manager) finds the manager", len(managers) == 1 and managers[0]["name"] == "Bob")

    # ── timezone ──────────────────────────────────────────────────────────
    # Naive input must be read as LOCAL time, not UTC.
    task = tools.create_task("Q3 report", "alice", "high", deadline="2026-08-10T17:00:00")
    check("create_task accepts a lowercase owner name", "task_id" in task)
    try:
        tools.create_task("No priority", "Alice")
        check("priority is required", False)
    except TypeError:
        check("priority is required", True)
    check("priority must be a valid value", "error" in tools.create_task("Bad priority", "Alice", "urgent"))
    check(
        "naive deadline is stored as local 17:00, not UTC",
        task["deadline_local"].startswith("2026-08-10T17:00:00"),
    )
    check(
        "the same deadline in UTC is offset by the timezone",
        task["deadline_utc"].startswith("2026-08-10T09:00:00"),
    )
    check("unlinked owner produces a warning", "warning" in task)
    task_a = task["task_id"]

    task_b = tools.create_task("Slide deck", "Alice", "medium", deadline="2026-08-11T12:00:00")["task_id"]
    check("create_task for unknown owner errors", "error" in tools.create_task("X", "Nobody", "medium"))

    # ── linking ───────────────────────────────────────────────────────────
    fake.push(ALICE_CHAT, "/start")                      # Telegram's own auto-message
    fake.push(STRANGER_CHAT, "hello who is this")        # someone unregistered
    fake.push(ALICE_CHAT, f"/start {alice['link_code']}")
    res = tools.telegram_get_updates()
    check("bare /start is discarded", not any("/start" == u["text"] for u in res["unmatched"]))
    check("stranger messages are discarded", not any(u["text"].startswith("hello who") for u in res["unmatched"]))
    check("Alice is linked", any(p["name"] == "Alice" for p in res["linked"]))
    check("list_people reflects the link", tools.list_people(role="team_member")[0]["is_linked"])

    # ── sending a chase ───────────────────────────────────────────────────
    unlinked = tools.telegram_send_message("Carol", "hi")
    check("messaging an unlinked person is flagged, not counted as ignored", unlinked.get("needs_linking"))

    sent = tools.telegram_send_message("Alice", "How's the Q3 report?", task_id=task_a)
    check("sending with task_id records a check-in atomically", sent.get("checkin_id") is not None)
    check("the Telegram message id is captured", sent.get("telegram_message_id") is not None)
    ping_a = sent["telegram_message_id"]

    wrong_owner = tools.telegram_send_message("Carol", "hi", task_id=task_a)
    check("sending someone else's task is rejected", "error" in wrong_owner)

    tasks = tools.list_tasks(filter="all", owner_name="Alice")
    a = next(t for t in tasks if t["task_id"] == task_a)
    check("hours_since_last_checkin is populated", a["hours_since_last_checkin"] is not None)
    check("unanswered_checkin_count is 1 after one ping", a["unanswered_checkin_count"] == 1)
    check("last_checkin_answered is False", a["last_checkin_answered"] is False)
    check("owner_is_linked is True", a["owner_is_linked"] is True)
    b = next(t for t in tasks if t["task_id"] == task_b)
    check("an unpinged task has no check-in history", b["hours_since_last_checkin"] is None)

    # ── unambiguous reply (only one ping outstanding) ─────────────────────
    fake.push(ALICE_CHAT, "about 80% done")
    res = tools.telegram_get_updates()
    check("a reply with one ping outstanding is matched", len(res["replies"]) == 1)
    check("it matched the right task", res["replies"][0]["task_id"] == task_a)

    # ── ambiguous reply (two pings outstanding, no threading) ─────────────
    tools.telegram_send_message("Alice", "And the Q3 report?", task_id=task_a)
    ping_b = tools.telegram_send_message("Alice", "How's the deck?", task_id=task_b)["telegram_message_id"]
    fake.push(ALICE_CHAT, "nearly there")
    res = tools.telegram_get_updates()
    check("an ambiguous reply is NOT guessed at", len(res["replies"]) == 0)
    amb = next((u for u in res["unmatched"] if u["text"] == "nearly there"), None)
    check("an ambiguous reply becomes unmatched", amb is not None)
    check("both candidate tasks are offered", amb and sorted(amb["candidate_task_ids"]) == sorted([task_a, task_b]))

    # ── threaded reply resolves ambiguity ─────────────────────────────────
    fake.push(ALICE_CHAT, "deck is done", reply_to=ping_b)
    res = tools.telegram_get_updates()
    check("a threaded reply is matched even with several pings open", len(res["replies"]) == 1)
    check("threading picked the replied-to task", res["replies"][0]["task_id"] == task_b)

    # ── unmatched persists until resolved ─────────────────────────────────
    check("the earlier ambiguous message is still pending", any(u["unmatched_id"] == amb["unmatched_id"] for u in res["unmatched"]))

    resolved = tools.resolve_unmatched(amb["unmatched_id"], task_id=task_a)
    check("resolve_unmatched attaches it to a task", resolved.get("attached"))
    res = tools.telegram_get_updates()
    check("a resolved message stops being returned", not any(u["unmatched_id"] == amb["unmatched_id"] for u in res["unmatched"]))
    check("resolving twice is rejected", "error" in tools.resolve_unmatched(amb["unmatched_id"], task_id=task_a))

    # ── spontaneous update, then dismissal ────────────────────────────────
    # Close the one ping still outstanding first — otherwise the next message
    # legitimately matches it, which is correct behaviour but not what this
    # case is testing.
    fake.push(ALICE_CHAT, "that one's finished too")
    res = tools.telegram_get_updates()
    check("the last outstanding ping gets answered", len(res["replies"]) == 1)

    fake.push(ALICE_CHAT, "morning!")
    res = tools.telegram_get_updates()
    spont = next((u for u in res["unmatched"] if u["text"] == "morning!"), None)
    check("a spontaneous message becomes unmatched", spont is not None)
    check("its reason says no ping was outstanding", spont and "spontaneous" in spont["reason"])
    check("it can be dismissed", tools.resolve_unmatched(spont["unmatched_id"]).get("dismissed"))

    # ── update_task ───────────────────────────────────────────────────────
    u = tools.update_task(task_a, deadline="2026-09-01T09:00:00", priority="low", title="Q3 report v2")
    check("update_task can change the deadline", u["deadline_local"].startswith("2026-09-01T09:00:00"))
    check("update_task can change priority", u["priority"] == "low")
    check("update_task can change the title", u["title"] == "Q3 report v2")

    u = tools.update_task(task_a, owner_name="Carol")
    check("update_task can reassign the owner", u["owner_name"] == "Carol")
    tools.update_task(task_a, owner_name="Alice")

    # ── reassign_task ────────────────────────────────────────────────────
    r = tools.reassign_task(task_a, new_owner_name="Carol")
    check("reassign_task moves ownership", r["owner_name"] == "Carol")
    check("reassign_task reports who it came from", r["reassigned_from"] == "Alice")
    check("reassign_task doesn't touch other fields", r["title"] == "Q3 report v2")
    tools.reassign_task(task_a, new_owner_name="Alice")

    check("reassign_task on an unknown task errors", "error" in tools.reassign_task(999999, new_owner_name="Alice"))
    check("reassign_task to an unregistered person errors", "error" in tools.reassign_task(task_a, new_owner_name="Nobody"))

    u = tools.update_task(task_a, status="done", progress_pct=40)
    check("status=done forces progress to 100", u["progress_pct"] == 100)
    u = tools.update_task(task_b, progress_pct=100, status="in_progress")
    check("100% without done is left alone (awaiting review is legitimate)", u["status"] == "in_progress")

    check("update_task on an unknown id errors", "error" in tools.update_task(999999, status="done"))

    # ── filters ───────────────────────────────────────────────────────────
    check("done tasks are excluded from 'active'", all(t["task_id"] != task_a for t in tools.list_tasks(filter="active")))
    tools.update_task(task_b, status="cancelled")
    check("cancelled tasks are excluded from 'active'", len(tools.list_tasks(filter="active")) == 0)
    check("'all' still shows everything", len(tools.list_tasks(filter="all")) == 2)

    # ── delete ────────────────────────────────────────────────────────────
    check("delete_task removes it", tools.delete_task(task_b).get("deleted"))
    check("it's gone from listings", all(t["task_id"] != task_b for t in tools.list_tasks(filter="all")))
    check("deleting twice errors", "error" in tools.delete_task(task_b))

    # ── delete_person ─────────────────────────────────────────────────────
    tools.register_person("Mistake")
    check("delete_person removes a task-free person", tools.delete_person("Mistake").get("deleted"))
    check("they're gone from listings", all(p["name"] != "Mistake" for p in tools.list_people()))
    check("deleting an unknown person errors", "error" in tools.delete_person("Nobody"))
    check("a person owning a task is refused", "error" in tools.delete_person("Alice"))
    check("...and Alice is untouched", any(p["name"] == "Alice" for p in tools.list_people()))

    # ── get_chase_plan ────────────────────────────────────────────────────
    # Fresh cast so the plan's filtering can be checked in isolation.
    for who in ("Dana", "Erin"):
        p = tools.register_person(who)
        fake.push(f"5560{ord(who[0])}", f"/start {p['link_code']}")
    tools.telegram_get_updates()

    from datetime import datetime as _dt, timedelta as _td
    from models import LOCAL_TZ as _tz
    # Must be inside the 24h due-soon window to be a candidate at all, so it is
    # computed relative to now rather than hard-coded to a date that drifts.
    soon = (_dt.now(_tz) + _td(hours=12)).replace(microsecond=0).isoformat()

    t_late = tools.create_task("Very late thing", "Dana", "high", deadline="2020-01-01T09:00:00")["task_id"]
    t_soon = tools.create_task("Due shortly", "Dana", "medium", deadline=soon)["task_id"]
    t_erin = tools.create_task("Erin's late thing", "Erin", "medium", deadline="2021-01-01T09:00:00")["task_id"]
    t_none = tools.create_task("No deadline at all", "Erin", "low")["task_id"]
    t_carol = tools.create_task("Carol is unlinked", "Carol", "medium", deadline="2020-06-01T09:00:00")["task_id"]

    plan = tools.get_chase_plan()
    chase_ids = [c["task_id"] for c in plan["to_chase"]]
    check("plan chases the most overdue task per person", t_late in chase_ids and t_erin in chase_ids)
    check("plan sends at most one task per person", len(chase_ids) == len(set(c["owner_name"] for c in plan["to_chase"])))
    check("the less urgent task for the same person is skipped, with a reason",
          any(s["task_id"] == t_soon and "more urgent" in s["reason"] for s in plan["skipped"]))
    check("a task with no deadline is never chased", t_none not in chase_ids)
    check("an unlinked owner's task is not chased", t_carol not in chase_ids)
    check("the unlinked owner is reported as unreachable", any(u["name"] == "Carol" for u in plan["unreachable"]))
    check("their link code is included so they can be onboarded", any(u["link_code"] for u in plan["unreachable"]))
    check("the manager is looked up for escalations", plan["manager_name"] == "Bob")
    check("most overdue is chased first", plan["to_chase"][0]["task_id"] == t_late)
    check("summary line is populated", "to chase" in plan["summary"])

    # The re-ping floor must hold even though the task is still overdue.
    tools.telegram_send_message("Dana", "any news?", task_id=t_late)
    plan = tools.get_chase_plan()
    check("a just-pinged task is not re-chased", t_late not in [c["task_id"] for c in plan["to_chase"]])
    check("...and the reason names the floor", any(s["task_id"] == t_late and "floor" in s["reason"] for s in plan["skipped"]))

    # ── priority-differentiated floor (high: 1h, medium: 6h) ───────────────
    # Fresh, otherwise-untouched owners — not Dana/Erin, whose outstanding-
    # ping state later tests (reply-matching, escalation) depend on staying
    # exactly as it is.
    frank = tools.register_person("Frank")
    fake.push("556070", f"/start {frank['link_code']}")
    grace = tools.register_person("Grace")
    fake.push("556071", f"/start {grace['link_code']}")
    tools.telegram_get_updates()

    t_hi = tools.create_task("Urgent thing", "Frank", "high", deadline="2020-01-01T09:00:00")["task_id"]
    t_med = tools.create_task("Routine thing", "Grace", "medium", deadline="2020-01-01T09:00:00")["task_id"]
    tools.telegram_send_message("Frank", "ping", task_id=t_hi)
    tools.telegram_send_message("Grace", "ping", task_id=t_med)

    from sqlalchemy import select as _select
    from models import CheckIn as _CheckIn, session_scope as _session_scope, utcnow as _utcnow
    with _session_scope() as _s:
        for _tid in (t_hi, t_med):
            _ci = _s.execute(_select(_CheckIn).where(_CheckIn.task_id == _tid)).scalars().first()
            _ci.sent_at = _utcnow() - _td(hours=2)

    plan = tools.get_chase_plan()
    chase_ids2 = [c["task_id"] for c in plan["to_chase"]]
    check("high-priority task past its 1h floor is chased again", t_hi in chase_ids2)
    check("medium-priority task still under its 6h floor is not", t_med not in chase_ids2)

    # ── chase_now: manual, floor-bypassing chase of one named person ───────
    forced = tools.chase_now("Dana")
    check("chase_now bypasses the floor for a just-pinged task", any(t["task_id"] == t_late for t in forced["tasks"]))
    check("chase_now reports the owner as reachable", forced["reachable"] is True)
    unreachable_chase = tools.chase_now("Carol")
    check("chase_now flags an unlinked owner instead of silently returning nothing", unreachable_chase["reachable"] is False)
    check("...with their link code included", unreachable_chase.get("link_code"))
    check("chase_now on an unknown person errors", "error" in tools.chase_now("Nobody At All"))

    # Escalation instead of a fourth ping — needs to reach max_unanswered (3).
    for _ in range(3):
        tools.telegram_send_message("Erin", "checking in", task_id=t_erin)
    plan = tools.get_chase_plan()
    # to_escalate is grouped one entry per owner, each holding a list of tasks.
    check(
        "3 unanswered pings escalates instead of chasing",
        any(t["task_id"] == t_erin for e in plan["to_escalate"] for t in e["tasks"]),
    )
    check("...and it is no longer in to_chase", t_erin not in [c["task_id"] for c in plan["to_chase"]])

    # Replies surface for interpretation rather than being auto-applied.
    fake.push("5560" + str(ord("D")), "still blocked on procurement")
    plan = tools.get_chase_plan()
    check("replies are surfaced for the agent to interpret", any(r["task_id"] == t_late for r in plan["replies_to_interpret"]))

    # ── blocked tasks: not chased, surfaced to the manager instead ────────
    tools.update_task(t_late, status="blocked")
    plan = tools.get_chase_plan()
    check("a blocked task is not chased", t_late not in [c["task_id"] for c in plan["to_chase"]])
    check("...and the reason says it needs the manager",
          any(s["task_id"] == t_late and "manager" in s["reason"] for s in plan["skipped"]))
    forced_blocked = tools.chase_now("Dana")
    check("chase_now also excludes a blocked task, even with the floor bypassed",
          all(t["task_id"] != t_late for t in forced_blocked["tasks"]))
    check("...and says why", any(s["task_id"] == t_late and "manager" in s["reason"] for s in forced_blocked["skipped"]))

    d = tools.get_digest_data()
    bl = next((b for b in d["blocked_needing_decision"] if b["task_id"] == t_late), None)
    check("the digest surfaces it for a decision", bl is not None)
    check("...carrying the owner's own words", bl and bl["reason_given"] == "still blocked on procurement")
    check("...and flagging that the deadline already passed", bl and bl["deadline_already_passed"] is True)
    tools.update_task(t_late, status="in_progress")

    # ── get_digest_data ───────────────────────────────────────────────────
    d = tools.get_digest_data()
    check("digest looks the manager up rather than guessing", d["manager_name"] == "Bob")
    check("digest counts overdue tasks", d["overdue_count"] >= 2)
    check("digest lists active tasks", d["active_count"] >= 3)
    check("digest sorts most urgent first", d["active_tasks"][0]["hours_until_deadline"] < 0)
    check("digest flags unresponsive people", any(u["owner_name"] == "Erin" for u in d["unresponsive"]))
    check("digest flags unreachable people", any(p["name"] == "Carol" for p in d["unreachable_people"]))
    check("digest does not pre-judge what is 'at risk'", "at_risk" not in d)

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
