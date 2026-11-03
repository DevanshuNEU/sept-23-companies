"""Pass and Go ahead on the job list (#156): `POST /api/jobs/{postingId}/pass` and `/go-ahead`. Scratch store, no network.

Pass hides one posting and is never a company Not now. Go ahead puts the company on today's list as his own pick:
size, the 7 day room and a Not now cooldown never stop it; a no-go company, one already listed and one in flight do.
"""
from datetime import date

from outreach.pipeline.joblist import passed_posting_ids
from outreach.plan.build import build_plan
from outreach.plan.config import default_plan_config
from outreach.plan.fresh import load_passed_postings, load_passes
from outreach.ranking.common import canonical_ids
from outreach.store import log_touch
from tests import apiseed as s
from tests.ranking_store import decision, evidence, posting
from tests.test_api_plan_picks import DAY, PicksCase, _context


class WritesCase(PicksCase):
    def job(self, name, title="Software Engineer", *, posted="2026-11-09", tier="new_grad", ok=1):
        pid = posting(self.conn, self.ids[name], title, posted_on=posted, first_seen_at="2026-11-01T12:00:00Z",
                      last_seen_at="2026-11-10T12:00:00Z")
        decision(self.conn, pid, level_tier=tier, level_ok=ok)
        return pid

    def passed(self, pid, **kw):
        return self.post(f"/api/jobs/{pid}/pass", {}, **kw)

    def go(self, pid, **kw):
        return self.post(f"/api/jobs/{pid}/go-ahead", {}, **kw)

    def jobs(self, **params):
        res = self.get("/api/jobs", params=params)
        self.assertEqual(res.status_code, 200, res.text)
        return [r["postingId"] for r in res.json()["rows"]]

    def plan_names(self):
        return [r["company"]["name"] for r in self.view()["rows"]]

    def clear_plan(self):
        self.conn.execute("DELETE FROM daily_list_entry")
        self.conn.execute("DELETE FROM daily_list")


class PassTest(WritesCase):
    def test_pass_hides_one_posting_not_the_company(self):
        a, b = self.job("Ember"), self.job("Ember", "Backend Engineer")
        self.assertEqual(sorted(self.jobs(q="Ember")), sorted([a, b]))
        res = self.passed(a)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"postingId": a, "companyId": self.ids["Ember"], "passed": True})
        self.assertEqual(self.jobs(q="Ember"), [b])
        self.assertEqual(self.count("SELECT status FROM company WHERE company_id = ?", self.ids["Ember"]), "candidate")
        self.assertEqual(self.count("SELECT count(*) FROM no_go WHERE company_id = ?", self.ids["Ember"]), 0)

    def test_pass_writes_the_event_the_list_reads(self):
        a = self.job("Ember")
        self.passed(a)
        row = self.conn.execute("SELECT * FROM event WHERE kind = 'posting_passed'").fetchone()
        self.assertEqual((row["entity"], row["entity_id"], row["actor"], row["company_id"]),
                         ("posting", a, "devanshu", self.ids["Ember"]))
        self.assertEqual(self.conn.execute("SELECT json_extract(detail, '$.posting_id'), "
                                           "json_extract(detail, '$.company_id') FROM event "
                                           "WHERE kind = 'posting_passed'").fetchone()[:], (a, self.ids["Ember"]))
        self.assertIn(a, passed_posting_ids(self.conn))

    def test_pass_is_not_a_company_not_now(self):
        a = self.job("Ember")
        self.passed(a)
        cfg = default_plan_config()
        passes = load_passes(self.conn, canonical_ids(self.conn), date.fromisoformat(DAY), cfg)
        self.assertNotIn(self.ids["Ember"], passes)
        self.assertIn(a, load_passed_postings(self.conn))
        # and Ember is still a free bench company
        self.assertIn("Ember", [c["company"]["name"] for c in self.bench()["companies"]])

    def test_passing_twice_writes_one_event(self):
        a = self.job("Ember")
        self.assertEqual(self.passed(a).status_code, 200)
        self.assertEqual(self.passed(a).status_code, 200)
        self.assertEqual(self.count("SELECT count(*) FROM event WHERE kind = 'posting_passed'"), 1)

    def test_pass_replays_the_same_key(self):
        a = self.job("Ember")
        first = self.passed(a, key="pass-key-0001")
        again = self.passed(a, key="pass-key-0001")
        self.assertEqual(again.headers["Idempotent-Replay"], "true")
        self.assertEqual(first.json(), again.json())
        self.assertEqual(self.count("SELECT count(*) FROM event WHERE kind = 'posting_passed'"), 1)

    def test_a_posting_that_is_not_there_is_a_404(self):
        res = self.passed(999999)
        self.assertEqual((res.status_code, res.json()["code"]), (404, "not_found"))

    def test_a_body_with_a_stray_field_is_a_400(self):
        a = self.job("Ember")
        self.assertEqual(self.post(f"/api/jobs/{a}/pass", {"company": 1}).status_code, 400)

    def test_a_pass_shows_in_the_next_list_read(self):
        a = self.job("Ember")
        self.assertEqual(self.jobs(q="Ember"), [a])
        self.passed(a)
        self.assertEqual(self.jobs(q="Ember"), [])  # the write moved the store, so the cached page is not reused


class GoAheadTest(WritesCase):
    def test_go_ahead_adds_a_big_company(self):
        titan = s.company(self.conn, "Titan")
        self.ids["Titan"] = titan
        evidence(self.conn, titan, "size", num_low=5000, num_high=5000)
        s.score(self.conn, titan, {"live_role": 40.0}, hide={"reason": "too_big"})  # the ranker hides it
        pid = self.job("Titan")
        res = self.go(pid)
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual((body["state"], body["rank"], body["postingId"], body["companyId"]),
                         ("added", 4, pid, titan))
        self.assertEqual(body["listDate"], DAY)
        self.assertIn("Titan", body["text"])
        rows = self.view()["rows"]
        row = next(r for r in rows if r["company"]["name"] == "Titan")
        self.assertEqual(row["entryId"], body["entryId"])
        self.assertEqual(self.plan_names()[-1], "Titan")
        self.assertEqual(self.count("SELECT posting_id FROM daily_list_entry WHERE company_id = ?", titan), pid)

    def test_go_ahead_on_an_unscored_company(self):
        co = s.company(self.conn, "Fresh Co")
        self.ids["Fresh Co"] = co
        pid = self.job("Fresh Co")
        self.assertEqual(self.go(pid).json()["state"], "added")
        self.assertIn("Fresh Co", self.plan_names())

    def test_go_ahead_ignores_room_and_cooldown(self):
        pid_gale = self.job("Gale")  # said Not now to 3 days ago
        pid_ember = self.job("Ember")
        pid = s.person(self.conn, self.ids["Iris"], "Bo Fry", channel="linkedin")
        for n in range(140):
            log_touch(self.conn, person_id=pid, company_id=self.ids["Iris"], channel="linkedin_request",
                      sent_at=f"2026-11-0{6 + n % 4}T1{n % 10}:{n % 60:02d}:00Z", context=_context(),
                      idempotency_key=f"seed:room:{n}")
        self.assertEqual(self.bench()["room"], 0)
        self.assertEqual(self.add("Ember").status_code, 409)  # Add to today stops at the room
        self.assertEqual(self.go(pid_gale).json()["state"], "added")  # Go ahead does not
        self.assertEqual(self.go(pid_ember).json()["state"], "added")
        self.assertEqual(self.plan_names()[-2:], ["Gale", "Ember"])

    def test_go_ahead_does_not_pass_or_hide_anything_else(self):
        pid = self.job("Ember")
        self.go(pid)
        self.assertEqual(self.count("SELECT count(*) FROM event WHERE kind IN ('pick_passed', 'posting_passed')"), 1)
        # (the one is Gale's seeded Not now)
        self.assertEqual(self.jobs(q="Ember"), [pid])

    def test_go_ahead_refuses_duplicate(self):
        pid = self.job("Ember")
        self.assertEqual(self.go(pid).status_code, 200)
        again = self.go(self.job("Ember", "Backend Engineer"))
        self.assertEqual((again.status_code, again.json()["code"]), (409, "not_allowed"))
        self.assertIn("Ember is already on today's list.", again.json()["message"])
        self.assertEqual(self.count("SELECT count(*) FROM daily_list_entry WHERE company_id = ?", self.ids["Ember"]), 1)

    def test_go_ahead_refuses_a_company_already_on_the_list_in_any_state(self):
        beacon = self.job("Beacon")
        res = self.go(beacon)
        self.assertEqual(res.status_code, 409)
        self.assertIn("Beacon is already on today's list.", res.json()["message"])
        self.pass_("Beacon")  # now removed from today's list
        res = self.go(beacon)
        self.assertEqual(res.status_code, 409)
        self.assertIn("was on today's list earlier", res.json()["message"])

    def test_go_ahead_refuses_a_no_go_company(self):
        res = self.go(self.job("Jolt"))
        self.assertEqual((res.status_code, res.json()["code"]), (409, "not_allowed"))
        self.assertIn("Jolt is on your no-go list", res.json()["message"])

    def test_go_ahead_refuses_a_company_with_someone_contacted_and_waiting(self):
        res = self.go(self.job("Iris"))
        self.assertEqual(res.status_code, 409)
        self.assertIn("Iris is in flight", res.json()["message"])
        self.assertIn("Nothing was saved.", res.json()["message"])

    def test_a_refusal_writes_nothing(self):
        before = self.count("SELECT count(*) FROM event")
        self.go(self.job("Jolt"))
        self.assertEqual(self.count("SELECT count(*) FROM event"), before)
        self.assertEqual(self.count("SELECT count(*) FROM daily_list_entry"), 3)

    def test_go_ahead_replays_the_same_key(self):
        pid = self.job("Ember")
        first = self.go(pid, key="goahead-key-1")
        again = self.go(pid, key="goahead-key-1")
        self.assertEqual(again.headers["Idempotent-Replay"], "true")
        self.assertEqual(first.json(), again.json())
        self.assertEqual(self.count("SELECT count(*) FROM daily_list_entry WHERE company_id = ?", self.ids["Ember"]), 1)
        self.assertEqual(self.count("SELECT count(*) FROM event WHERE kind = 'own_pick_requested'"), 1)

    def test_a_posting_that_is_not_there_is_a_404(self):
        self.assertEqual(self.go(999999).status_code, 404)

    def test_go_ahead_follows_a_merged_company_to_its_survivor(self):
        dupe = s.company(self.conn, "Ember Inc", "removed", merged_into=self.ids["Ember"])
        self.ids["Ember Inc"] = dupe
        pid = self.job("Ember Inc")
        body = self.go(pid).json()
        self.assertEqual(body["companyId"], self.ids["Ember"])
        self.assertEqual(self.count("SELECT company_id FROM daily_list_entry WHERE rank = 4"), self.ids["Ember"])


class BeforeThePlanTest(WritesCase):
    def setUp(self):
        super().setUp()
        self.clear_plan()

    def test_go_ahead_before_plan_records_a_request(self):
        pid = self.job("Ember")
        res = self.go(pid)
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual((body["state"], body["rank"], body["entryId"]), ("requested", None, None))
        self.assertEqual(self.count("SELECT count(*) FROM daily_list"), 0)
        self.assertEqual(self.count("SELECT count(*) FROM event WHERE kind = 'own_pick_requested' "
                                    "AND json_extract(detail, '$.list_date') = ?", DAY), 1)

    def test_go_ahead_on_a_saturday_is_kept_for_monday(self):
        self.set_now("2026-11-14T15:14:00Z")  # Saturday 10:14 in New York
        body = self.go(self.job("Ember")).json()
        self.assertEqual((body["state"], body["listDate"]), ("requested", "2026-11-16"))
        self.assertIn("Monday, Nov 16", body["text"])
        self.assertNotIn("today", body["text"])
        self.assertEqual(self.count("SELECT json_extract(detail, '$.list_date') FROM event "
                                    "WHERE kind = 'own_pick_requested'"), "2026-11-16")

    def test_go_ahead_on_a_sunday_is_kept_for_monday(self):
        self.set_now("2026-11-15T15:14:00Z")
        body = self.go(self.job("Ember")).json()
        self.assertEqual((body["state"], body["listDate"]), ("requested", "2026-11-16"))

    def test_a_weekday_request_says_today(self):
        body = self.go(self.job("Ember")).json()
        self.assertEqual(body["listDate"], DAY)
        self.assertIn("today's list", body["text"])

    def test_a_second_go_ahead_before_the_plan_is_refused(self):
        self.go(self.job("Ember"))
        res = self.go(self.job("Ember", "Backend Engineer"))
        self.assertEqual(res.status_code, 409)
        self.assertIn("already asked for", res.json()["message"])

    def test_go_ahead_before_plan_is_kept_by_build(self):
        titan = s.company(self.conn, "Titan")
        self.ids["Titan"] = titan
        s.score(self.conn, titan, {"live_role": 1.0}, hide={"reason": "too_big"})
        pid = self.job("Titan")
        self.go(pid)
        result = build_plan(self.conn, today=date.fromisoformat(DAY), read_boards=False)
        self.assertEqual(result.status, "built")  # the request did not make it "already built"
        rows = self.conn.execute("SELECT company_id, rank, state, posting_id FROM daily_list_entry "
                                 "ORDER BY rank").fetchall()
        self.assertEqual(tuple(rows[0]), (titan, 1, "proposed", pid))
        self.assertEqual([r["rank"] for r in rows], list(range(1, len(rows) + 1)))
        self.assertEqual(len({r["company_id"] for r in rows}), len(rows))
        self.assertIn("Titan", self.plan_names())

    def test_build_does_not_double_a_company_it_would_have_chosen_anyway(self):
        ember = self.ids["Ember"]
        pid = self.job("Ember")
        self.go(pid)
        build_plan(self.conn, today=date.fromisoformat(DAY), read_boards=False)
        self.assertEqual(self.count("SELECT count(*) FROM daily_list_entry WHERE company_id = ?", ember), 1)
        self.assertEqual(self.count("SELECT rank FROM daily_list_entry WHERE company_id = ?", ember), 1)

    def test_build_drops_a_request_that_went_no_go_since(self):
        ember = self.ids["Ember"]
        self.go(self.job("Ember"))
        self.conn.execute("INSERT INTO no_go(company_id, reason, source, created_at) VALUES (?, 'no_sponsorship', "
                          "'devanshu', '2026-11-10T14:00:00Z')", (ember,))
        build_plan(self.conn, today=date.fromisoformat(DAY), read_boards=False)
        self.assertEqual(self.count("SELECT count(*) FROM daily_list_entry WHERE company_id = ?", ember), 0)

    def test_go_ahead_after_the_build_takes_the_next_rank(self):
        co = s.company(self.conn, "Unranked")
        self.ids["Unranked"] = co
        pid = self.job("Unranked")
        build_plan(self.conn, today=date.fromisoformat(DAY), read_boards=False)
        last = self.count("SELECT max(rank) FROM daily_list_entry")
        res = self.go(pid)
        self.assertEqual((res.json()["state"], res.json()["rank"]), ("added", last + 1))
