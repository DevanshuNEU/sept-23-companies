"""Pass and Done on the job list (#156, #195): `POST /api/jobs/{postingId}/pass` and `/done`, and Done's undo
`POST /api/companies/{companyId}/reached/clear`. Scratch store, no network.

Pass hides one posting and is never a company Not now. Done records his mark that the company has been reached (the
first to see ruling in #9): no other check stops it, and the company's rows show it as reached.
"""
from datetime import date

from outreach.pipeline import reached
from outreach.pipeline.joblist import passed_posting_ids
from outreach.plan.config import default_plan_config
from outreach.plan.fresh import load_passed_postings, load_passes
from outreach.ranking.common import canonical_ids
from tests import apiseed as s
from tests.ranking_store import decision, posting
from tests.test_api_plan_picks import DAY, PicksCase


class WritesCase(PicksCase):
    def job(self, name, title="Software Engineer", *, posted="2026-11-09", tier="new_grad", ok=1):
        pid = posting(self.conn, self.ids[name], title, posted_on=posted, first_seen_at="2026-11-01T12:00:00Z",
                      last_seen_at="2026-11-10T12:00:00Z")
        decision(self.conn, pid, level_tier=tier, level_ok=ok)
        return pid

    def passed(self, pid, **kw):
        return self.post(f"/api/jobs/{pid}/pass", {}, **kw)

    def done(self, pid, **kw):
        return self.post(f"/api/jobs/{pid}/done", {}, **kw)

    def clear(self, company_id, **kw):
        return self.post(f"/api/companies/{company_id}/reached/clear", {}, **kw)

    def jobs(self, **params):
        res = self.get("/api/jobs", params=params)
        self.assertEqual(res.status_code, 200, res.text)
        return [r["postingId"] for r in res.json()["rows"]]


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


class DoneTest(WritesCase):
    """Done (#195, the first to see ruling in #9): his mark that a company has been reached. No other check stops it."""

    def marks(self, kind="company_reached"):
        return self.conn.execute("SELECT * FROM event WHERE kind = ? ORDER BY event_id", (kind,)).fetchall()

    def test_done_records_reached_mark(self):
        pid = self.job("Ember")
        res = self.done(pid)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"postingId": pid, "companyId": self.ids["Ember"], "reachedOn": DAY,
                                      "text": "Marked Ember as reached."})
        [row] = self.marks()
        self.assertEqual((row["entity"], row["entity_id"], row["company_id"], row["actor"], row["at"]),
                         ("company", self.ids["Ember"], self.ids["Ember"], "devanshu", self.now))
        self.assertEqual(self.conn.execute("SELECT json_extract(detail, '$.posting_id'), "
                                           "json_extract(detail, '$.company_id') FROM event "
                                           "WHERE kind = 'company_reached'").fetchone()[:], (pid, self.ids["Ember"]))
        self.assertEqual(reached.marks(self.conn), {self.ids["Ember"]: DAY})

    def test_done_shows_as_reached_on_every_row_of_the_company(self):
        a, b = self.job("Ember"), self.job("Ember", "Backend Engineer")
        self.done(a)
        rows = self.get("/api/jobs", params={"q": "Ember"}).json()["rows"]
        self.assertEqual({r["postingId"]: r["contactedOn"] for r in rows}, {a: DAY, b: DAY})
        self.assertEqual(self.jobs(q="Ember", notContacted="true"), [])

    def test_done_twice_is_one_mark(self):
        a, b = self.job("Ember"), self.job("Ember", "Backend Engineer")
        self.done(a)
        self.set_now("2026-11-11T15:14:00Z")
        res = self.done(b)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual((res.json()["reachedOn"], res.json()["text"]),
                         (DAY, "Ember was already marked reached on Nov 10."))
        self.assertEqual(len(self.marks()), 1)

    def test_done_replays_the_same_key(self):
        pid = self.job("Ember")
        first = self.done(pid, key="job-done-key-1")
        again = self.done(pid, key="job-done-key-1")
        self.assertEqual(again.headers["Idempotent-Replay"], "true")
        self.assertEqual(first.json(), again.json())
        self.assertEqual(len(self.marks()), 1)

    def test_done_on_no_go_company_still_records(self):
        res = self.done(self.job("Jolt"))
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(len(self.marks()), 1)
        self.assertEqual(reached.marks(self.conn), {self.ids["Jolt"]: DAY})

    def test_done_ignores_a_company_in_flight_on_the_list_or_passed(self):
        for name in ("Iris", "Beacon", "Gale"):  # someone waiting, on today's list, a Not now
            self.assertEqual(self.done(self.job(name)).status_code, 200, name)
        self.assertEqual(len(self.marks()), 3)

    def test_done_touches_nothing_but_its_event(self):
        pid = self.job("Ember")
        tables = ("daily_list_entry", "outreach_touch", "touch", "linkedin_log", "no_go", "application")
        before = [self.count(f"SELECT count(*) FROM {t}") for t in tables]
        events = self.count("SELECT count(*) FROM event")
        self.done(pid)
        self.assertEqual([self.count(f"SELECT count(*) FROM {t}") for t in tables], before)
        self.assertEqual(self.count("SELECT count(*) FROM event"), events + 1)
        self.assertEqual(self.count("SELECT status FROM company WHERE company_id = ?", self.ids["Ember"]), "candidate")
        self.assertEqual(self.jobs(q="Ember"), [pid])  # Done never hides the posting

    def test_done_follows_a_merged_company_to_its_survivor(self):
        dupe = s.company(self.conn, "Ember Inc", "removed", merged_into=self.ids["Ember"])
        self.ids["Ember Inc"] = dupe
        body = self.done(self.job("Ember Inc")).json()
        self.assertEqual(body["companyId"], self.ids["Ember"])
        self.assertEqual(reached.marks(self.conn), {self.ids["Ember"]: DAY})

    def test_done_on_a_posting_that_is_not_there_is_a_404(self):
        res = self.done(999999)
        self.assertEqual((res.status_code, res.json()["code"]), (404, "not_found"))

    def test_done_with_a_stray_field_is_a_400(self):
        self.assertEqual(self.post(f"/api/jobs/{self.job('Ember')}/done", {"company": 1}).status_code, 400)

    def test_go_ahead_route_is_gone(self):
        pid = self.job("Ember")
        before = self.count("SELECT count(*) FROM event")
        self.assertEqual(self.post(f"/api/jobs/{pid}/go-ahead", {}).status_code, 404)
        self.assertEqual(self.count("SELECT count(*) FROM event"), before)
        paths = self.app.openapi()["paths"]
        self.assertIn("/api/jobs/{posting_id}/done", paths)
        self.assertNotIn("/api/jobs/{posting_id}/go-ahead", paths)


class ClearDoneTest(WritesCase):
    """Undo of Done: `POST /api/companies/{companyId}/reached/clear`."""

    def test_clear_mark_removes_reached(self):
        pid = self.job("Ember")
        self.done(pid)
        res = self.clear(self.ids["Ember"])
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"companyId": self.ids["Ember"], "cleared": True})
        row = self.conn.execute("SELECT * FROM event WHERE kind = 'company_reached_cleared'").fetchone()
        self.assertEqual((row["entity"], row["entity_id"], row["company_id"], row["actor"]),
                         ("company", self.ids["Ember"], self.ids["Ember"], "devanshu"))
        self.assertEqual(reached.marks(self.conn), {})
        self.assertEqual(self.jobs(q="Ember", notContacted="true"), [pid])
        self.assertIsNone(self.get("/api/jobs", params={"q": "Ember"}).json()["rows"][0]["contactedOn"])

    def test_mark_after_clear_counts_again(self):
        pid = self.job("Ember")
        self.done(pid)
        self.clear(self.ids["Ember"])
        self.set_now("2026-11-11T15:14:00Z")
        res = self.done(pid)
        self.assertEqual((res.json()["reachedOn"], res.json()["text"]), ("2026-11-11", "Marked Ember as reached."))
        self.assertEqual(reached.marks(self.conn), {self.ids["Ember"]: "2026-11-11"})

    def test_clear_with_no_mark_writes_nothing(self):
        before = self.count("SELECT count(*) FROM event")
        res = self.clear(self.ids["Ember"])
        self.assertEqual(res.json(), {"companyId": self.ids["Ember"], "cleared": False})
        self.done(self.job("Ember"))
        self.clear(self.ids["Ember"])
        self.assertEqual(self.clear(self.ids["Ember"]).json()["cleared"], False)  # a second clear writes nothing
        self.assertEqual(self.count("SELECT count(*) FROM event"), before + 2)

    def test_clear_never_takes_back_real_outreach(self):
        # Iris has a LinkedIn request on file: clearing his mark leaves her reached by the request.
        pid = self.job("Iris")
        self.done(pid)
        self.clear(self.ids["Iris"])
        self.assertEqual(self.get("/api/jobs", params={"q": "Iris"}).json()["rows"][0]["contactedOn"], "2026-11-08")

    def test_clear_on_a_merged_away_id_clears_the_survivor(self):
        dupe = s.company(self.conn, "Ember Inc", "removed", merged_into=self.ids["Ember"])
        self.done(self.job("Ember"))
        res = self.clear(dupe)
        self.assertEqual(res.json(), {"companyId": self.ids["Ember"], "cleared": True})
        self.assertEqual(reached.marks(self.conn), {})

    def test_clear_on_a_company_that_is_not_there_is_a_404(self):
        res = self.clear(999999)
        self.assertEqual((res.status_code, res.json()["code"]), (404, "not_found"))

    def test_clear_replays_the_same_key(self):
        self.done(self.job("Ember"))
        first = self.clear(self.ids["Ember"], key="reached-clear-key-1")
        again = self.clear(self.ids["Ember"], key="reached-clear-key-1")
        self.assertEqual(again.headers["Idempotent-Replay"], "true")
        self.assertEqual(first.json(), again.json())
        self.assertEqual(self.count("SELECT count(*) FROM event WHERE kind = 'company_reached_cleared'"), 1)
