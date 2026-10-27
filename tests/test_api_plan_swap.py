"""Browse companies (#120): `POST /api/plan/{entryId}/swap` puts a chosen company in the slot of one of today's picks.
The replaced pick goes back to the bench: it is not a Not now (no `pick_passed` event, no cooldown), and a swap uses
no room in the 7-day limit. It follows the pick rules and refuses in plain words. Scratch store, test client only."""
import json
from datetime import date

from outreach.plan.config import default_plan_config
from outreach.plan.fresh import load_passes
from outreach.ranking.common import canonical_ids
from outreach.store import log_touch
from tests import apiseed as s
from tests.test_api_plan_browse import BrowseCase, _context
from tests.test_api_plan_picks import DAY


class SwapCase(BrowseCase):
    def swap(self, pick, company, *, key=None, revision=1, entry=None, company_id=None):
        eid = entry if entry is not None else self.entries[pick]
        cid = company_id if company_id is not None else self.ids[company]
        return self.post(f"/api/plan/{eid}/swap", {"companyId": cid, "idempotencyKey": key or f"pick-swap:{eid}:{cid}",
                                                   "revision": revision})

    def ok(self, *args, **kw):
        res = self.swap(*args, **kw)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def refused(self, res, words):
        self.assertEqual((res.status_code, res.json()["code"]), (409, "not_allowed"), res.text)
        self.assertIn(words, res.json()["message"])

    def counts(self):
        return [self.count(f"SELECT count(*) FROM {t}") for t in ("event", "daily_list_entry", "no_go", "daily_list")]

    def fill_the_week(self):
        pid = s.person(self.conn, self.ids["Iris"], "Bo Fry", channel="linkedin")
        for n in range(140):
            log_touch(self.conn, person_id=pid, company_id=self.ids["Iris"], channel="linkedin_request",
                      sent_at=f"2026-11-0{6 + n % 4}T1{n % 10}:{n % 60:02d}:00Z", context=_context(),
                      idempotency_key=f"seed:room:{n}")


class SwapTest(SwapCase):
    def test_swap_takes_the_picks_rank_and_returns_the_pick_to_the_bench(self):
        top = self.count("SELECT max(rowid) FROM daily_list_entry")
        body = self.ok("Beacon", "Ember")
        self.assertEqual(body["text"], "Swapped in Ember for Beacon.")
        self.assertEqual((body["row"]["company"]["name"], body["replaced"]["name"]), ("Ember", "Beacon"))
        self.assertEqual(body["replacedEntryId"], self.entries["Beacon"])
        self.assertGreater(body["row"]["entryId"], top)  # a new row id, never the old one
        self.assertEqual(self.count("SELECT rank FROM daily_list_entry WHERE rowid = ?", body["row"]["entryId"]), 1)
        self.assertEqual(self.count("SELECT count(*) FROM daily_list_entry WHERE company_id = ?", self.ids["Beacon"]), 0)
        self.assertEqual(self.names(self.view()["rows"]), ["Aster", "Ember", "Corvid"])
        bench = self.names(self.bench()["companies"])
        self.assertIn("Beacon", bench)
        self.assertNotIn("Ember", bench)
        self.assertNotIn("Ember", self.listed())
        self.assertEqual(self.get("/api/shell").json()["plan"]["total"], 3)

    def test_swap_writes_no_pick_passed_and_no_cooldown(self):
        self.ok("Beacon", "Ember")
        self.assertEqual(self.count("SELECT count(*) FROM event WHERE kind = 'pick_passed' AND company_id = ?",
                                    self.ids["Beacon"]), 0)
        self.assertEqual(self.view()["passed"], 0)
        passes = load_passes(self.conn, canonical_ids(self.conn), date(2026, 11, 10), default_plan_config())
        self.assertNotIn(self.ids["Beacon"], passes)
        self.assertIn(self.ids["Gale"], passes)  # a real Not now still is one
        beacon = next(c for c in self.browse()["companies"] if c["company"]["name"] == "Beacon")
        self.assertIsNone(beacon["blocked"])
        # He can bring it straight back.
        self.assertEqual(self.add("Beacon").status_code, 200)

    def test_swap_uses_no_room(self):
        before = self.bench()
        self.ok("Beacon", "Ember")
        after = self.bench()
        self.assertEqual((after["room"], after["roomText"]), (before["room"], before["roomText"]))
        self.assertEqual(before["room"], 34)

    def test_swap_works_when_there_is_no_room(self):
        self.fill_the_week()
        self.assertEqual(self.bench()["room"], 0)
        self.refused(self.add("Ember"), "No room to add more this week")
        body = self.ok("Beacon", "Ember")
        self.assertEqual(body["row"]["company"]["name"], "Ember")
        self.assertEqual(self.bench()["room"], 0)
        self.assertIn("Beacon", self.names(self.bench()["companies"]))

    def test_swap_records_an_event_on_each_company(self):
        body = self.ok("Beacon", "Ember")
        new = self.conn.execute("SELECT * FROM event WHERE kind = 'pick_swapped'").fetchall()
        self.assertEqual(len(new), 1)
        ev = new[0]
        self.assertEqual((ev["entity"], ev["entity_id"], ev["company_id"], ev["actor"]),
                         ("company", self.ids["Ember"], self.ids["Ember"], "devanshu"))
        d = json.loads(ev["detail"])
        self.assertEqual({k: d[k] for k in ("list_date", "rank", "entry_id", "replaced_entry_id", "replaced_company_id",
                                            "replaced_company")},
                         {"list_date": DAY, "rank": 1, "entry_id": body["row"]["entryId"],
                          "replaced_entry_id": self.entries["Beacon"], "replaced_company_id": self.ids["Beacon"],
                          "replaced_company": "Beacon"})
        out = self.conn.execute("SELECT * FROM event WHERE kind = 'pick_swapped_out'").fetchall()
        self.assertEqual([(e["company_id"], e["actor"]) for e in out], [(self.ids["Beacon"], "devanshu")])
        texts = {c: [e["text"] for e in self.get(f"/api/companies/{self.ids[c]}").json()["events"]]
                 for c in ("Ember", "Beacon")}
        self.assertIn("Swapped in for Beacon on today's list.", texts["Ember"])
        self.assertIn("Swapped out for Ember on today's list. It is back on the bench.", texts["Beacon"])

    def test_swapping_back_works(self):
        first = self.ok("Beacon", "Ember")["row"]
        again = self.ok(None, "Beacon", entry=first["entryId"], revision=first["revision"])
        self.assertEqual(again["row"]["company"]["name"], "Beacon")
        self.assertEqual(self.names(self.view()["rows"]), ["Aster", "Beacon", "Corvid"])
        self.assertIn("Ember", self.names(self.bench()["companies"]))
        self.assertEqual(self.count("SELECT count(*) FROM event WHERE kind = 'pick_passed' AND company_id IN (?, ?)",
                                    self.ids["Beacon"], self.ids["Ember"]), 0)

    def test_a_swap_between_pages_never_skips_or_repeats(self):
        first = self.browse(limit=2)
        self.ok("Beacon", "Ember")
        rest, after = [], first["nextAfter"]
        while after is not None:
            page = self.browse(limit=2, after=after)
            rest += self.names(page["companies"])
            after = page["nextAfter"]
        self.assertEqual(self.names(first["companies"]) + rest, ["Gale", "Haven", "Beacon", "Dune", "Fable"])

    def test_the_same_key_answers_the_same_and_writes_once(self):
        first = self.ok("Beacon", "Ember")
        counts = self.counts()
        again = self.swap("Beacon", "Ember")
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json(), first)
        self.assertEqual(self.counts(), counts)

    def test_a_stale_revision_conflicts_and_writes_nothing(self):
        counts = self.counts()
        res = self.swap("Beacon", "Ember", revision=2)
        self.assertEqual((res.status_code, res.json()["code"]), (409, "conflict"))
        self.assertEqual(self.counts(), counts)

    def test_a_merged_company_answers_as_its_survivor(self):
        old = s.company(self.conn, "Ember Old", status="removed", remove_reason="merged", merged_into=self.ids["Ember"])
        body = self.ok("Beacon", None, company_id=old)
        self.assertEqual(body["row"]["company"]["name"], "Ember")
        self.refused(self.swap(None, None, entry=body["row"]["entryId"], company_id=old, revision=1,
                               key="pick-swap:other"), "Ember is already on today's list")


class RefusalTest(SwapCase):
    def test_a_no_go_company_is_refused(self):
        counts = self.counts()
        self.refused(self.swap("Beacon", "Jolt"), "Jolt is on your no-go list")
        self.assertEqual(self.counts(), counts)

    def test_a_company_in_flight_is_refused_with_its_reason(self):
        counts = self.counts()
        self.refused(self.swap("Beacon", "Iris"), "Iris is in flight: Request sent")
        self.assertEqual(self.counts(), counts)

    def test_a_held_back_company_is_refused_with_the_return_date(self):
        self.refused(self.swap("Beacon", "Gale"), "You said Not now to Gale on Nov 7. It comes back on Nov 21")
        self.refused(self.swap("Beacon", "Haven"), "You applied to Haven on Oct 20. It comes back with a new role.")

    def test_a_company_already_on_the_list_is_refused(self):
        self.refused(self.swap("Beacon", "Corvid"), "Corvid is already on today's list.")
        self.refused(self.swap("Beacon", "Beacon"), "Beacon is already on today's list.")

    def test_a_company_with_a_removed_row_today_is_refused(self):
        self.assertEqual(self.pass_("Corvid").status_code, 200)
        self.refused(self.swap("Beacon", "Corvid"), "Corvid was on today's list earlier")

    def test_a_closed_or_unranked_company_is_refused(self):
        self.conn.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?",
                          (self.ids["Ember"],))
        self.refused(self.swap("Beacon", "Ember"), "Ember is not free to swap in")
        nobody = s.company(self.conn, "Nobody Inc")
        self.refused(self.swap("Beacon", None, company_id=nobody), "Nobody Inc is not in the ranked list")

    def test_a_pick_he_reached_is_not_replaced(self):
        pid = s.person(self.conn, self.ids["Beacon"], "Pat Lee", channel="linkedin")
        log_touch(self.conn, person_id=pid, company_id=self.ids["Beacon"], channel="linkedin_request",
                  sent_at="2026-11-10T14:00:00Z", context=_context(), idempotency_key="seed:beacon")
        counts = self.counts()
        self.refused(self.swap("Beacon", "Ember"), "You already reached Beacon today, so it stays on the list.")
        self.assertEqual(self.counts(), counts)

    def test_a_carried_pick_is_not_replaced(self):
        self.conn.execute("UPDATE daily_list_entry SET state = 'backlog', decided_at = '2026-11-10T14:00:00Z' "
                          "WHERE rowid = ?", (self.entries["Beacon"],))
        self.refused(self.swap("Beacon", "Ember"), "Beacon is carried to tomorrow, so it stays on the list.")

    def test_a_row_that_is_gone_or_not_todays_is_refused(self):
        self.assertEqual(self.swap(None, "Ember", entry=9999).status_code, 404)
        self.pass_("Beacon")
        self.refused(self.swap("Beacon", "Ember", revision=2), "Beacon is already off today's list.")

    def test_an_unknown_company_is_not_found(self):
        self.assertEqual(self.swap("Beacon", None, company_id=99999).status_code, 404)
