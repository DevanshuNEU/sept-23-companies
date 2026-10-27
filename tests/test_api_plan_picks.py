"""Top picks on `GET /api/plan` (the owner's spec and rulings, 2026-10-26): the opening each pick leads with, the
fresh lane first, Not now (`POST /api/plan/{entryId}/pass`), the bench (`GET /api/plan/bench`) and Add to today
(`POST /api/plan/bench/{companyId}/add`). Scratch store, no network; the app never fetches LinkedIn.
"""
import json

from outreach.outcomes.context import build_context
from outreach.store import log_touch
from tests import apiseed as s
from tests.apitest import ApiCase

DAY = "2026-11-10"  # NOW is 2026-11-10T15:14:00Z, Tuesday 10:14 in New York


def _context():
    return build_context(source="plan", lane="level_role", role_kind="engineer", role_age_days=3,
                         ranker_version="rk-1", signals={}, role_named=False)


class PicksCase(ApiCase):
    def setUp(self):
        super().setUp()
        c = self.conn
        c.execute("INSERT INTO daily_list(list_date, built_at, capacity) VALUES (?, '2026-11-10T13:00:00Z', 8)", (DAY,))
        self.ids, self.posts = {}, {}
        # On today's list: Beacon (an old role), Corvid (no role), and Aster (a new grad role first seen 6 hours ago)
        # at rank 3: the page shows Aster first.
        self.ids["Beacon"] = self.company("Beacon", 70, title="Platform Engineer", seen="2026-11-06T12:00:00Z",
                                          posted="2026-11-04")
        self.ids["Corvid"] = self.company("Corvid", 60)
        self.ids["Aster"] = self.company("Aster", 50, title="Software Engineer, New Grad",
                                         seen="2026-11-10T09:14:00Z", domain="aster.example")
        self.entries = {name: self.entry(name, rank) for rank, name in enumerate(("Beacon", "Corvid", "Aster"), 1)}
        # The bench pool.
        self.ids["Dune"] = self.company("Dune", 30, title="Software Engineer I", seen="2026-11-09T19:14:00Z",
                                        tier="swe1")
        self.ids["Ember"] = self.company("Ember", 80)
        self.ids["Fable"] = self.company("Fable", 20, title="Software Engineer II", seen="2026-11-10T13:14:00Z",
                                         tier="swe2")
        self.ids["Gale"] = self.company("Gale", 85)  # passed 3 days ago
        s.event(c, self.ids["Gale"], "pick_passed", "2026-11-07T15:00:00Z", actor="devanshu",
                detail={"reason": "too_big"})
        self.ids["Haven"] = self.company("Haven", 84)  # he applied there
        c.execute("INSERT INTO application(company_id, applied_on) VALUES (?, '2026-10-20')", (self.ids["Haven"],))
        self.ids["Iris"] = self.company("Iris", 83)  # a wave out 2 days ago
        pid = s.person(c, self.ids["Iris"], "Ana Bell", channel="linkedin")
        log_touch(c, person_id=pid, company_id=self.ids["Iris"], channel="linkedin_request",
                  sent_at="2026-11-08T15:00:00Z", context=_context(), idempotency_key="seed:iris")
        self.ids["Jolt"] = self.company("Jolt", 82)  # a no-go
        c.execute("INSERT INTO no_go(company_id, reason, source, created_at) VALUES (?, 'no_sponsorship', 'devanshu', "
                  "'2026-11-01T12:00:00Z')", (self.ids["Jolt"],))

    def company(self, name, score, *, title=None, seen=None, posted=None, tier="new_grad", domain=None):
        cid = s.company(self.conn, name)
        post = None
        if title is not None:
            post = s.posting(self.conn, cid, title, first_seen_at=seen, last_seen_at="2026-11-10T15:00:00Z",
                             url=f"https://jobs.example/{name.lower()}")
            if posted:
                self.conn.execute("UPDATE posting SET posted_on = ? WHERE posting_id = ?", (posted, post))
            s.decision(self.conn, post, level_tier=tier)
            self.posts[name] = post
        if domain:
            s.evidence(self.conn, cid, "domain", domain)
        s.score(self.conn, cid, {"live_role": float(score)}, best_posting_id=post)
        return cid

    def entry(self, name, rank):
        why = {"lane": "level_role", "reason": "Ranked high", "reason_sources": [], "signals": [], "role": None,
               "person": None, "channel_reason": "find_someone", "draft_id": None, "find_someone": None,
               "carried_from": None, "first_proposed": DAY, "rotation": None, "score": 50.0, "ranker_version": "rk-1"}
        return self.conn.execute(
            "INSERT INTO daily_list_entry(list_date, company_id, list_group, rank, state, posting_id, why, proposed_at, "
            "channel) VALUES (?, ?, 'level_role', ?, 'proposed', ?, ?, '2026-11-10T13:00:00Z', 'linkedin_request')",
            (DAY, self.ids[name], rank, self.posts.get(name), json.dumps(why))).lastrowid

    def view(self):
        res = self.get("/api/plan")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def bench(self):
        res = self.get("/api/plan/bench")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def names(self, rows):
        return [r["company"]["name"] for r in rows]

    def pass_(self, name, reason="too_big", *, key=None, revision=1, request_id=None):
        eid = self.entries[name]
        return self.post(f"/api/plan/{eid}/pass", {"reason": reason, "idempotencyKey": key or f"plan:{eid}:pass",
                                                   "revision": revision}, key=request_id)

    def add(self, name, *, key=None, request_id=None):
        cid = self.ids[name]
        return self.post(f"/api/plan/bench/{cid}/add", {"idempotencyKey": key or f"bench:{cid}:add"}, key=request_id)

    def count(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()[0]


class OpeningTest(PicksCase):
    def test_a_fresh_pick_leads_the_list_with_its_opening(self):
        rows = self.view()["rows"]
        self.assertEqual(self.names(rows), ["Aster", "Beacon", "Corvid"])
        self.assertEqual([r["position"] for r in rows], [1, 2, 3])
        q = rows[0]["queue"]
        self.assertEqual(q["opening"], {
            "postingId": self.posts["Aster"], "title": "Software Engineer, New Grad", "url": "https://jobs.example/aster",
            "level": "New grad role", "age": "first seen 6 hours ago", "openedAt": "2026-11-10T09:14:00Z",
            "fresh": True, "earlyFit": True,
            "freshTag": "New opening, 6 hours ago", "appliedNote": None})
        self.assertEqual(q["whyToday"], "New grad role, first seen 6 hours ago")
        self.assertEqual(q["websiteUrl"], "https://aster.example")
        self.assertEqual(q["linkedinCompanyUrl"], "https://www.linkedin.com/search/results/companies/?keywords=Aster")

    def test_an_old_role_says_its_level_and_age_and_is_not_fresh(self):
        beacon = self.view()["rows"][1]["queue"]
        self.assertEqual((beacon["opening"]["fresh"], beacon["opening"]["freshTag"]), (False, None))
        self.assertEqual(beacon["whyToday"], "New grad role, posted 6 days ago")
        self.assertIsNone(beacon["websiteUrl"])
        corvid = self.view()["rows"][2]["queue"]
        self.assertIsNone(corvid["opening"])
        self.assertEqual(corvid["whyToday"], "Ranked high")  # no signals stored: the row's reason

    def test_a_failed_visa_gate_drops_the_new_tag(self):
        self.conn.execute("UPDATE decision SET sponsorship = 'unclear', sponsorship_quote = 'maybe' "
                          "WHERE posting_id = ?", (self.posts["Aster"],))
        rows = self.view()["rows"]
        self.assertEqual(self.names(rows), ["Beacon", "Corvid", "Aster"])  # the plan's own order
        self.assertFalse(rows[2]["queue"]["opening"]["fresh"])


class BenchTest(PicksCase):
    def test_fresh_fits_then_fresh_others_then_rank_and_nothing_held(self):
        b = self.bench()
        self.assertEqual(self.names(b["companies"]), ["Dune", "Fable", "Ember"])
        self.assertEqual([(c["fresh"], c["earlyFit"]) for c in b["companies"]],
                         [(True, True), (True, False), (False, False)])
        self.assertEqual(b["companies"][0]["queue"]["opening"]["freshTag"], "New opening, 20 hours ago")
        self.assertEqual(b["companies"][1]["queue"]["opening"]["level"], "SWE II role")
        self.assertEqual(b["companies"][0]["queue"]["linkedinCompanyUrl"],
                         "https://www.linkedin.com/search/results/companies/?keywords=Dune")
        # 150 a week left, 4 a wave: 37 waves, less the 3 picks still to do.
        self.assertEqual((b["room"], b["roomText"]), (34, "Room to add 34 more companies this week."))

    def test_a_new_opening_brings_back_a_company_he_passed_and_one_he_applied_to(self):
        for name, seen in (("Gale", "2026-11-10T12:14:00Z"), ("Haven", "2026-11-09T12:14:00Z")):
            p = s.posting(self.conn, self.ids[name], "New Grad Engineer", first_seen_at=seen,
                          last_seen_at="2026-11-10T15:00:00Z")
            s.decision(self.conn, p)
        b = self.bench()["companies"]
        self.assertEqual(self.names(b)[:3], ["Gale", "Dune", "Haven"])
        haven = next(c for c in b if c["company"]["name"] == "Haven")
        self.assertEqual(haven["queue"]["opening"]["appliedNote"], "You applied on Oct 20; this is a new role.")


class PassTest(PicksCase):
    def test_not_now_slides_in_the_bench_head_and_keeps_the_company(self):
        before_no_go = self.count("SELECT count(*) FROM no_go")
        status = self.count("SELECT status FROM company WHERE company_id = ?", self.ids["Beacon"])
        top = self.count("SELECT max(rowid) FROM daily_list_entry")
        res = self.pass_("Beacon")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["text"], "Passed. Added Dune.")
        self.assertEqual(body["company"]["name"], "Beacon")
        self.assertEqual(body["added"]["company"]["name"], "Dune")
        self.assertGreater(body["added"]["entryId"], top)
        rows = self.view()["rows"]
        self.assertEqual(self.names(rows), ["Aster", "Dune", "Corvid"])  # both new: the newest opening first
        # Dune took Beacon's rank; Beacon's row is off today's list, the company is untouched.
        self.assertEqual(self.count("SELECT rank FROM daily_list_entry WHERE rowid = ?", body["added"]["entryId"]), 1)
        self.assertEqual(self.count("SELECT state FROM daily_list_entry WHERE rowid = ?", self.entries["Beacon"]),
                         "removed")
        self.assertEqual(self.count("SELECT count(*) FROM no_go"), before_no_go)
        self.assertEqual(self.count("SELECT status FROM company WHERE company_id = ?", self.ids["Beacon"]), status)
        ev = self.conn.execute("SELECT * FROM event WHERE kind = 'pick_passed' AND company_id = ?",
                               (self.ids["Beacon"],)).fetchone()
        self.assertEqual((ev["entity"], ev["entity_id"], ev["actor"]), ("company", self.ids["Beacon"], "devanshu"))
        detail = json.loads(ev["detail"])
        self.assertEqual({k: detail[k] for k in ("reason", "list_date", "rank", "entry_id", "added_company_id")},
                         {"reason": "too_big", "list_date": DAY, "rank": 1, "entry_id": self.entries["Beacon"],
                          "added_company_id": self.ids["Dune"]})
        self.assertEqual(detail["signals"], {"stage": None, "size_band": None, "fresh": False,
                                             "sponsorship": "Sponsorship not stated"})
        # The company page still shows it; the bench no longer does (the cooldown).
        self.assertEqual(self.get(f"/api/companies/{self.ids['Beacon']}").status_code, 200)
        self.assertNotIn("Beacon", self.names(self.bench()["companies"]))
        self.assertEqual(self.get("/api/shell").json()["plan"]["total"], 3)

    def test_the_same_key_answers_again_and_writes_nothing(self):
        first = self.pass_("Beacon").json()
        events = self.count("SELECT count(*) FROM event")
        entries = self.count("SELECT count(*) FROM daily_list_entry")
        again = self.pass_("Beacon")
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json(), first)
        self.assertEqual(self.count("SELECT count(*) FROM event"), events)
        self.assertEqual(self.count("SELECT count(*) FROM daily_list_entry"), entries)

    def test_no_reason_is_fine_and_a_wrong_reason_is_refused(self):
        self.assertEqual(self.pass_("Corvid", reason=None).status_code, 200)
        self.assertEqual(self.pass_("Beacon", reason="hate_it").status_code, 400)

    def test_a_stale_revision_conflicts(self):
        res = self.pass_("Beacon", revision=2)
        self.assertEqual((res.status_code, res.json()["code"]), (409, "conflict"))

    def test_a_row_he_reached_stays_and_one_with_an_application_passes(self):
        pid = s.person(self.conn, self.ids["Beacon"], "Pat Lee", channel="linkedin")
        log_touch(self.conn, person_id=pid, company_id=self.ids["Beacon"], channel="linkedin_request",
                  sent_at="2026-11-10T14:00:00Z", context=_context(), idempotency_key="seed:beacon")
        res = self.pass_("Beacon")
        self.assertEqual((res.status_code, res.json()["code"]), (409, "not_allowed"))
        self.assertIn("already reached Beacon", res.json()["message"])
        self.conn.execute("INSERT INTO application(company_id, posting_id, applied_on) VALUES (?, ?, ?)",
                          (self.ids["Aster"], self.posts["Aster"], DAY))
        self.assertEqual(self.pass_("Aster").status_code, 200)

    def test_an_empty_bench_passes_and_adds_nothing(self):
        for name in ("Dune", "Ember", "Fable"):
            self.conn.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?",
                              (self.ids[name],))
        body = self.pass_("Beacon").json()
        self.assertIsNone(body["added"])
        self.assertEqual(body["text"], "Passed on Beacon. Nothing else is free to add right now.")

    def test_a_passed_row_cannot_be_passed_again(self):
        self.pass_("Beacon")
        res = self.pass_("Beacon", key="plan:other:pass", revision=2)
        self.assertEqual(res.json()["code"], "not_allowed")
        self.assertIn("already off today's list", res.json()["message"])


class AddTest(PicksCase):
    def test_add_appends_a_bench_company(self):
        res = self.add("Ember")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["company"]["name"], "Ember")
        self.assertEqual(self.names(self.view()["rows"]), ["Aster", "Beacon", "Corvid", "Ember"])
        self.assertEqual(self.count("SELECT rank FROM daily_list_entry WHERE company_id = ?", self.ids["Ember"]), 4)
        self.assertEqual(self.count("SELECT count(*) FROM event WHERE kind = 'pick_added'"), 1)
        again = self.add("Ember")
        self.assertEqual(again.json()["entryId"], res.json()["entryId"])
        self.assertEqual(self.count("SELECT count(*) FROM event WHERE kind = 'pick_added'"), 1)

    def test_refusals_say_why(self):
        for name, words in (("Beacon", "Beacon is already on today's list."),
                            ("Gale", "You said Not now to Gale on Nov 7. It comes back on Nov 21"),
                            ("Haven", "You applied to Haven on Oct 20. It comes back with a new role."),
                            ("Iris", "Iris is in flight"),
                            ("Jolt", "Jolt is not in today's ranked list")):
            with self.subTest(name=name):
                res = self.add(name)
                self.assertEqual((res.status_code, res.json()["code"]), (409, "not_allowed"), res.text)
                self.assertIn(words, res.json()["message"])

    def test_no_room_this_week_refuses(self):
        pid = s.person(self.conn, self.ids["Iris"], "Bo Fry", channel="linkedin")
        for n in range(140):
            log_touch(self.conn, person_id=pid, company_id=self.ids["Iris"], channel="linkedin_request",
                      sent_at=f"2026-11-0{6 + n % 4}T1{n % 10}:{n % 60:02d}:00Z", context=_context(),
                      idempotency_key=f"seed:room:{n}")
        self.assertEqual(self.bench()["room"], 0)
        res = self.add("Ember")
        self.assertEqual(res.json()["code"], "not_allowed")
        self.assertIn("No room to add more this week", res.json()["message"])
