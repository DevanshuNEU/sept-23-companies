"""The lean Today (until #15's daily plan): the list built from the ranked list, stored per New York day, and the
pick, unpick, backlog, remove and restore writes on its rows."""
import json
from datetime import datetime, timezone

from outreach.sending.config import load_config as load_send_config
from outreach.sending.status import sending_snapshot
from tests import apiseed as s
from tests.apitest import NOW, ApiCase

TODAY = "2026-11-10"  # NOW is Tuesday 11:14 in New York
NEXT_DAY = "2026-11-11T15:14:00Z"
SATURDAY = "2026-11-14T15:14:00Z"
CONTEXT = json.dumps({"source": "own", "lane": "own", "roleKind": "engineer", "roleAgeDays": 0, "rankerVersion": "r1",
                      "signals": {}, "roleNamed": False})


class TodayCase(ApiCase):
    def ranked(self, name: str, points: float = 5.0, *, lanes=("level_role",), status="candidate", role=True,
               hide=None, **cols) -> int:
        cid = s.company(self.conn, name, status, **cols)
        posting = s.posting(self.conn, cid, f"{name} Engineer, New Grad") if role else None
        if posting:
            s.decision(self.conn, posting)
        s.score(self.conn, cid, total=points, lanes=lanes, best_posting_id=posting, hide=hide,
                line=f"{name} is hiring a new grad engineer")
        return cid

    def view(self) -> dict:
        res = self.get("/api/today")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def names(self, key="proposed") -> list[str]:
        return [e["company"]["name"] for e in self.view()[key]]

    def entry(self, cid, day=TODAY):
        return self.conn.execute("SELECT * FROM daily_list_entry WHERE list_date = ? AND company_id = ?",
                                 (day, cid)).fetchone()

    def company(self, cid):
        return self.conn.execute("SELECT * FROM company WHERE company_id = ?", (cid,)).fetchone()

    def revision(self, cid) -> int:
        return self.company(cid)["revision"]

    def pick(self, cid, revision=None, key=None):
        return self.post(f"/api/companies/{cid}/pick", {"revision": revision or self.revision(cid)}, key=key)

    def unpick(self, cid, revision=None, key=None):
        return self.post(f"/api/companies/{cid}/unpick", {"revision": revision or self.revision(cid)}, key=key)

    def backlog(self, cid, revision=None, key=None):
        return self.post(f"/api/companies/{cid}/backlog", {"revision": revision or self.revision(cid)}, key=key)

    def remove(self, cid, reason="not_a_fit", note="Too big", revision=None, key=None):
        return self.post(f"/api/companies/{cid}/remove",
                         {"reason": reason, "note": note, "revision": revision or self.revision(cid)}, key=key)

    def restore(self, cid, reason="Talked to a friend there", revision=None, key=None):
        return self.post(f"/api/companies/{cid}/restore",
                         {"reason": reason, "revision": revision or self.revision(cid)}, key=key)

    def events(self, kind):
        return self.conn.execute("SELECT * FROM event WHERE kind = ?", (kind,)).fetchall()


class Build(TodayCase):
    def test_empty_store_is_not_built_and_stores_nothing(self):
        v = self.view()
        self.assertEqual((v["proposed"], v["backlog"], v["stillEmailing"], v["picked"], v["builtAt"]), ([], [], [], 0, None))
        self.assertEqual(v["notReady"]["reason"], "not_built")
        self.assertEqual((v["date"], v["asOf"]), (TODAY, NOW))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM daily_list").fetchone()[0], 0)

    def test_first_read_of_a_weekday_builds_and_stores_the_list_in_score_order(self):
        low, high, mid = self.ranked("Low", 2.0), self.ranked("High", 9.0), self.ranked("Mid", 5.0)
        v = self.view()
        self.assertIsNone(v["notReady"])
        self.assertEqual([e["company"]["name"] for e in v["proposed"]], ["High", "Mid", "Low"])
        self.assertEqual([e["rank"] for e in v["proposed"]], [1, 2, 3])
        self.assertEqual(v["builtAt"], NOW)
        stored = self.conn.execute("SELECT * FROM daily_list").fetchall()
        self.assertEqual([(r["list_date"], r["built_at"], r["capacity"]) for r in stored], [(TODAY, NOW, v["capacity"])])
        rows = self.conn.execute("SELECT company_id, state, rank, decided_at FROM daily_list_entry ORDER BY rank").fetchall()
        self.assertEqual([(r["company_id"], r["state"], r["rank"], r["decided_at"]) for r in rows],
                         [(high, "proposed", 1, None), (mid, "proposed", 2, None), (low, "proposed", 3, None)])

    def test_the_list_does_not_reshuffle_during_the_day(self):
        a, b = self.ranked("A", 5.0), self.ranked("B", 4.0)
        self.assertEqual(self.names(), ["A", "B"])
        self.ranked("Newcomer", 99.0)
        s.score(self.conn, b, total=50.0, version="rk-2", at="2026-11-10T16:00:00Z", lanes=("level_role",))
        self.assertEqual(self.names(), ["A", "B"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM daily_list_entry").fetchone()[0], 2)

    def test_who_is_left_off_the_list(self):
        self.ranked("Shown", 1.0)
        self.ranked("NoLane", 9.0, lanes=())
        self.ranked("Hidden", 9.0, hide={"kind": "over_size", "reason": "800 people", "provisional": False})
        gone = self.ranked("NoGo", 9.0)
        self.conn.execute("INSERT INTO no_go(company_id, reason, source, created_at) VALUES (?, 'other', 'devanshu', ?)",
                          (gone, s.T0))
        held_company = self.ranked("Held", 9.0, status="working")
        self.ranked("Closed", 9.0, status="closed")
        self.ranked("Removed", 9.0, status="removed")
        self.ranked("Working", 9.0, status="working")
        touched = self.ranked("Touched", 9.0)
        s._insert(self.conn, "outreach_touch", {
            "person_id": None, "company_id": touched, "channel": "application", "sent_at": s.T0, "context": CONTEXT,
            "created_at": s.T0})
        mailed = self.ranked("Mailed", 9.0)
        s.send_first(self.conn, s.touch(self.conn, s.person(self.conn, mailed, "Pat")))
        dup_of = self.ranked("Keeper", 0.5)
        self.ranked("Duplicate", 9.0, status="removed", merged_into=dup_of)
        held_by = s.touch(self.conn, s.person(self.conn, held_company, "Hal"))
        self.conn.execute("UPDATE company SET held_by_touch = ?, held_at = ? WHERE company_id = ?",
                          (held_by, s.T0, held_company))
        self.assertEqual(self.names(), ["Shown", "Keeper"])

    def test_entry_carries_the_role_facts_reason_and_the_company_revision(self):
        cid = self.ranked("Acme", 5.0, lanes=("intern_junior", "any_us_role"))
        s.evidence(self.conn, cid, "size", "25")
        s.evidence(self.conn, cid, "stage", "seed", confidence="estimate")
        s.evidence(self.conn, cid, "domain", "acme.example")
        s.evidence(self.conn, cid, "hq", "Boston, MA")
        s.mx(self.conn, "acme.example", "ok")
        s.person(self.conn, cid, "Pat")
        pid = s.person(self.conn, cid, "Sam")
        s.message(self.conn, s.touch(self.conn, pid), 0, "drafted")
        self.conn.execute("UPDATE company SET revision = 4 WHERE company_id = ?", (cid,))
        [e] = self.view()["proposed"]
        self.assertEqual((e["listGroup"], e["state"], e["revision"], e["firstProposedOn"]), ("intern_junior", "proposed", 4, TODAY))
        self.assertEqual((e["company"]["id"], e["company"]["domain"], e["company"]["hq"]), (cid, "acme.example", "Boston, MA"))
        self.assertEqual(e["role"]["title"], "Acme Engineer, New Grad")
        self.assertEqual((e["size"]["value"], e["stage"]["value"], e["mailServerOk"]), ("25", "seed", True))
        self.assertEqual((e["peopleCount"], e["draftCount"]), (2, 1))
        self.assertIn("Acme is hiring a new grad engineer", e["why"])

    def test_capacity_is_seven_linkedin_plus_the_senders_email_slots(self):
        self.ranked("A")
        cfg = load_send_config()
        snap = sending_snapshot(self.conn, datetime.strptime(NOW, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc), cfg)
        self.assertGreater(snap.limit_today, 0)
        v = self.view()
        self.assertEqual(v["capacity"], 7 + snap.limit_today - snap.sent_today)

    def test_a_paused_sender_gives_no_email_slots(self):
        self.ranked("A")
        revision = self.get("/api/sending").json()["revision"]
        self.assertEqual(self.post("/api/sending/pause", {"revision": revision}).status_code, 200)
        self.assertEqual(self.view()["capacity"], 7)

    def test_a_weekend_has_no_list_and_still_shows_the_backlog(self):
        a = self.ranked("A")
        self.view()
        self.assertEqual(self.backlog(a).status_code, 200)
        self.set_now(SATURDAY)
        v = self.view()
        self.assertEqual((v["notReady"]["reason"], v["proposed"], v["picked"], v["builtAt"]), ("weekend", [], 0, None))
        self.assertEqual([e["company"]["name"] for e in v["backlog"]], ["A"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM daily_list").fetchone()[0], 1)

    def test_a_backlog_row_carries_to_the_next_day_and_unpicked_rows_do_not(self):
        a, b = self.ranked("A", 5.0), self.ranked("B", 4.0)
        self.view()
        self.assertEqual(self.backlog(a).status_code, 200)
        self.set_now(NEXT_DAY)
        v = self.view()
        self.assertEqual([e["company"]["name"] for e in v["backlog"]], ["A"])
        self.assertEqual([e["company"]["name"] for e in v["proposed"]], ["B"])  # ranked again, not carried
        self.assertEqual(v["backlog"][0]["firstProposedOn"], TODAY)
        self.assertEqual(self.entry(a, "2026-11-11")["state"], "backlog")

    def test_a_backlog_row_is_not_carried_once_the_company_is_barred_or_no_longer_in_the_backlog(self):
        a, b, c = self.ranked("A", 5.0), self.ranked("B", 4.0), self.ranked("C", 3.0)
        self.view()
        for cid in (a, b, c):
            self.assertEqual(self.backlog(cid).status_code, 200)
        self.conn.execute("INSERT INTO no_go(company_id, reason, source, created_at) VALUES (?, 'other', 'devanshu', ?)",
                          (a, s.T0))
        self.conn.execute("UPDATE company SET status = 'candidate' WHERE company_id = ?", (b,))
        self.set_now(NEXT_DAY)
        v = self.view()
        self.assertEqual([e["company"]["name"] for e in v["backlog"]], ["C"])
        self.assertEqual([e["company"]["name"] for e in v["proposed"]], ["B"])  # B is a candidate again: ranked, not carried

    def test_still_emailing_lists_companies_picked_earlier_with_their_state(self):
        a = self.ranked("A", 5.0)
        self.view()
        self.assertEqual(self.pick(a).status_code, 200)
        pat = s.person(self.conn, a, "Pat")
        sam = s.person(self.conn, a, "Sam")
        s.send_first(self.conn, s.touch(self.conn, pat), "2026-11-10T16:00:00Z")
        s.message(self.conn, s.touch(self.conn, sam), 0, "drafted")
        self.assertEqual(self.view()["stillEmailing"], [])  # picked today: not yet "earlier"
        self.set_now(NEXT_DAY)
        [row] = self.view()["stillEmailing"]
        self.assertEqual((row["company"]["name"], row["day"], row["days"], row["emailed"], row["budget"], row["state"]),
                         ("A", 2, 3, 1, 2, "emailing"))
        self.conn.execute("UPDATE company SET paused_at = ? WHERE company_id = ?", (NEXT_DAY, a))
        self.assertEqual(self.view()["stillEmailing"][0]["state"], "paused")


class Pick(TodayCase):
    def setUp(self):
        super().setUp()
        self.a, self.b = self.ranked("A", 5.0), self.ranked("B", 4.0)
        self.view()

    def test_pick_moves_to_working_and_counts_against_capacity(self):
        res = self.pick(self.a, key="pick-key-1")
        self.assertEqual(res.status_code, 200, res.text)
        e = res.json()
        self.assertEqual((e["state"], e["company"]["status"], e["revision"]), ("picked", "working", 2))
        row = self.entry(self.a)
        self.assertEqual((row["state"], row["decided_at"]), ("picked", NOW))
        self.assertEqual((self.company(self.a)["status"], self.company(self.a)["revision"]), ("working", 2))
        v = self.view()
        self.assertEqual(v["picked"], 1)
        self.assertEqual([x["state"] for x in v["proposed"]], ["picked", "proposed"])
        [ev] = self.events("picked")
        self.assertEqual((ev["entity"], ev["entity_id"], ev["company_id"], ev["actor"], ev["idempotency_key"]),
                         ("company", self.a, self.a, "devanshu", "pick-key-1:0"))
        self.assertEqual(self.get(f"/api/companies/{self.a}").json()["company"]["pickedOn"], TODAY)

    def test_pick_past_capacity_is_the_capacity_error_naming_the_limit(self):
        self.conn.execute("UPDATE daily_list SET capacity = 1")
        self.assertEqual(self.pick(self.a).status_code, 200)
        res = self.pick(self.b)
        self.assertEqual((res.status_code, res.json()["code"]), (409, "capacity"))
        self.assertIn("1 compan", res.json()["message"])
        self.assertEqual(self.company(self.b)["status"], "candidate")
        self.assertEqual(self.entry(self.b)["state"], "proposed")

    def test_pick_stale_revision_is_a_conflict_and_changes_nothing(self):
        res = self.pick(self.a, revision=9)
        self.assertEqual((res.status_code, res.json()["code"]), (409, "conflict"))
        self.assertEqual((self.company(self.a)["status"], self.entry(self.a)["state"]), ("candidate", "proposed"))
        self.assertEqual(self.events("picked"), [])

    def test_pick_refuses_a_no_go_company_and_a_held_one(self):
        self.conn.execute("INSERT INTO no_go(company_id, reason, source, created_at) VALUES (?, 'other', 'devanshu', ?)",
                          (self.a, s.T0))
        res = self.pick(self.a)
        self.assertEqual((res.status_code, res.json()["code"]), (409, "not_allowed"))
        self.assertIn("no-go", res.json()["message"])
        touch = s.touch(self.conn, s.person(self.conn, self.b, "Bo"))
        self.conn.execute("UPDATE company SET held_by_touch = ?, held_at = ? WHERE company_id = ?", (touch, s.T0, self.b))
        res = self.pick(self.b)
        self.assertEqual((res.status_code, res.json()["code"]), (409, "not_allowed"))
        self.assertEqual(self.events("picked"), [])

    def test_pick_on_a_weekend_is_not_allowed(self):
        self.set_now(SATURDAY)
        res = self.pick(self.a)
        self.assertEqual((res.status_code, res.json()["code"]), (409, "not_allowed"))

    def test_unpick_and_backlog_round_trip_with_events(self):
        self.assertEqual(self.pick(self.a).status_code, 200)
        res = self.unpick(self.a)
        self.assertEqual((res.status_code, res.json()["state"], res.json()["company"]["status"]), (200, "proposed", "candidate"))
        row = self.entry(self.a)
        self.assertEqual((row["state"], row["decided_at"]), ("proposed", None))
        self.assertEqual(self.view()["picked"], 0)
        res = self.backlog(self.a)
        self.assertEqual((res.status_code, res.json()["state"], res.json()["company"]["status"]), (200, "backlog", "backlog"))
        v = self.view()
        self.assertEqual(([e["company"]["name"] for e in v["backlog"]], [e["company"]["name"] for e in v["proposed"]]),
                         (["A"], ["B"]))
        self.assertEqual(self.pick(self.a).json()["state"], "picked")  # a backlog row can be picked
        self.assertEqual(self.backlog(self.a).json()["state"], "backlog")  # and a picked row moved to the backlog
        self.assertEqual(self.company(self.a)["status"], "backlog")
        self.assertEqual([len(self.events(k)) for k in ("picked", "unpicked", "moved_to_backlog")], [2, 1, 2])

    def test_unpick_a_row_that_is_not_picked_is_not_allowed(self):
        res = self.unpick(self.a)
        self.assertEqual((res.status_code, res.json()["code"]), (409, "not_allowed"))

    def test_unpick_and_backlog_refuse_a_company_with_mail_approved_or_sent(self):
        self.assertEqual(self.pick(self.a).status_code, 200)
        pat = s.person(self.conn, self.a, "Pat")
        mid = s.message(self.conn, s.touch(self.conn, pat), 0, "approved")
        for call in (self.unpick, self.backlog):
            res = call(self.a)
            self.assertEqual((res.status_code, res.json()["code"]), (409, "not_allowed"), res.text)
        self.assertEqual((self.company(self.a)["status"], self.entry(self.a)["state"]), ("working", "picked"))
        self.assertTrue(mid)

    def test_unpick_refuses_a_paused_company(self):
        self.assertEqual(self.pick(self.a).status_code, 200)
        self.conn.execute("UPDATE company SET paused_at = ? WHERE company_id = ?", (NOW, self.a))
        res = self.unpick(self.a)
        self.assertEqual((res.status_code, res.json()["code"]), (409, "not_allowed"))
        self.assertEqual(self.company(self.a)["status"], "working")

    def test_double_click_pick_picks_once_and_two_tabs_second_is_not_allowed(self):
        first, again = self.pick(self.a, 1, key="same-key-aa"), self.pick(self.a, 1, key="same-key-aa")
        self.assertEqual((first.status_code, again.status_code), (200, 200))
        self.assertEqual(again.headers.get("Idempotent-Replay"), "true")
        self.assertEqual(again.json(), first.json())
        self.assertEqual(len(self.events("picked")), 1)
        second_tab = self.pick(self.a, revision=1, key="other-tab-1")
        self.assertEqual((second_tab.status_code, second_tab.json()["code"]), (409, "not_allowed"))
        self.assertEqual(len(self.events("picked")), 1)

    def test_entry_revision_equals_the_company_revision(self):
        self.conn.execute("UPDATE company SET revision = 3 WHERE company_id = ?", (self.b,))
        [x, y] = self.view()["proposed"]
        self.assertEqual((x["revision"], y["revision"]), (1, 3))
        self.assertEqual(self.pick(self.b, revision=3).json()["revision"], 4)


class RemoveRestore(TodayCase):
    def setUp(self):
        super().setUp()
        self.a, self.b = self.ranked("A", 5.0), self.ranked("B", 4.0)
        self.view()

    def test_remove_sets_the_reason_and_lists_it_in_no_go(self):
        res = self.remove(self.a, "bigger_than_it_looks", "About 600 on LinkedIn", key="remove-key-1")
        self.assertEqual((res.status_code, res.json()), (200, {}))
        row = self.company(self.a)
        self.assertEqual((row["status"], row["remove_reason"], row["status_note"]),
                         ("removed", "bigger_than_it_looks", "About 600 on LinkedIn"))
        self.assertEqual(self.entry(self.a)["state"], "removed")
        self.assertEqual(self.names(), ["B"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM no_go").fetchone()[0], 0)  # no no_go row is written
        [removed] = self.get("/api/no-go").json()["companies"]
        self.assertEqual((removed["kind"], removed["company"]["id"], removed["removeReason"]),
                         ("removed", self.a, "bigger_than_it_looks"))
        [ev] = self.events("removed")
        self.assertEqual((ev["idempotency_key"], ev["actor"]), ("remove-key-1:0", "devanshu"))

    def test_a_removed_company_is_not_proposed_again(self):
        self.assertEqual(self.remove(self.a).status_code, 200)
        self.set_now(NEXT_DAY)
        self.assertEqual(self.names(), ["B"])

    def test_remove_a_picked_company_takes_it_off_the_count(self):
        self.assertEqual(self.pick(self.a).status_code, 200)
        self.assertEqual(self.remove(self.a).status_code, 200)
        self.assertEqual(self.view()["picked"], 0)

    def test_remove_refuses_a_company_with_mail_out_or_a_closed_one(self):
        pat = s.person(self.conn, self.a, "Pat")
        s.send_first(self.conn, s.touch(self.conn, pat))
        res = self.remove(self.a)
        self.assertEqual((res.status_code, res.json()["code"]), (409, "not_allowed"))
        self.assertEqual(self.company(self.a)["status"], "candidate")
        self.conn.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?", (self.b,))
        self.assertEqual(self.remove(self.b).json()["code"], "not_allowed")

    def test_remove_stale_revision_is_a_conflict(self):
        res = self.remove(self.a, revision=7)
        self.assertEqual((res.status_code, res.json()["code"]), (409, "conflict"))
        self.assertEqual(self.company(self.a)["status"], "candidate")

    def test_restore_needs_a_reason_and_returns_the_row_to_backlog(self):
        self.assertEqual(self.remove(self.a).status_code, 200)
        for reason in ("", "  "):
            res = self.restore(self.a, reason)
            self.assertEqual((res.status_code, res.json()["code"]), (400, "bad_request"), res.text)
        self.assertEqual(self.company(self.a)["status"], "removed")
        res = self.restore(self.a, "A friend works there", key="restore-key-1")
        self.assertEqual((res.status_code, res.json()), (200, {}))
        row = self.company(self.a)
        self.assertEqual((row["status"], row["remove_reason"], row["status_note"]), ("backlog", None, ""))
        self.assertEqual(self.entry(self.a)["state"], "backlog")
        self.assertEqual([e["company"]["name"] for e in self.view()["backlog"]], ["A"])
        self.assertEqual(self.get("/api/no-go").json()["companies"], [])
        [ev] = self.events("restored")
        self.assertEqual(json.loads(ev["detail"])["reason"], "A friend works there")

    def test_restore_refuses_a_company_that_is_not_removed_and_a_merged_duplicate(self):
        res = self.restore(self.b)
        self.assertEqual((res.status_code, res.json()["code"]), (409, "not_allowed"))
        dup = s.company(self.conn, "Dup", "removed", merged_into=self.b)
        res = self.restore(dup)
        self.assertEqual((res.status_code, res.json()["code"]), (409, "not_allowed"))
        self.assertEqual(self.company(dup)["status"], "removed")

    def test_restore_on_a_weekend_keeps_the_row_for_the_next_build(self):
        self.assertEqual(self.remove(self.a).status_code, 200)
        self.set_now(SATURDAY)
        self.assertEqual(self.restore(self.a).status_code, 200)
        self.assertEqual([e["company"]["name"] for e in self.view()["backlog"]], ["A"])
        self.set_now("2026-11-16T15:14:00Z")
        self.assertEqual([e["company"]["name"] for e in self.view()["backlog"]], ["A"])


class Repeats(TodayCase):
    """Every write: the same key replays and writes once; a second key is refused and writes nothing."""

    def test_each_write_replays_and_a_second_key_changes_nothing(self):
        a = self.ranked("A", 5.0)
        self.view()
        steps = [
            ("picked", lambda key, rev: self.pick(a, rev, key=key)),
            ("unpicked", lambda key, rev: self.unpick(a, rev, key=key)),
            ("moved_to_backlog", lambda key, rev: self.backlog(a, rev, key=key)),
            ("removed", lambda key, rev: self.remove(a, revision=rev, key=key)),
            ("restored", lambda key, rev: self.restore(a, revision=rev, key=key)),
        ]
        for n, (kind, call) in enumerate(steps):
            with self.subTest(write=kind):
                rev = self.revision(a)
                first = call(f"repeat-key-{n}-a", rev)
                self.assertEqual(first.status_code, 200, first.text)
                replay = call(f"repeat-key-{n}-a", rev)
                self.assertEqual((replay.status_code, replay.headers.get("Idempotent-Replay")), (200, "true"))
                self.assertEqual(replay.json(), first.json())
                self.assertEqual(len(self.events(kind)), 1)
                after = self.company(a)["revision"]
                second = call(f"repeat-key-{n}-b", after)  # a new key for the same action: it is already done
                self.assertEqual(second.status_code, 409, second.text)
                self.assertEqual(len(self.events(kind)), 1)
                self.assertEqual(self.company(a)["revision"], after)
