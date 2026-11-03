"""The job list on `GET /api/jobs` (#156): the Task 1 query behind a heavy view, camelCase on the wire, a plain 400 for a
bad parameter. Scratch store, reads only, no network."""
import itertools
from datetime import date

from tests.apitest import ApiCase
from tests.ranking_store import decision, evidence, posting
from tests.storetest import seed_company

_names = itertools.count(1)


class JobsCase(ApiCase):
    """NOW is 2026-11-10 10:14 in New York."""

    def company(self, name=None, *, size=None):
        cid = seed_company(self.conn, name or f"Company {next(_names)}")
        if size is not None:
            evidence(self.conn, cid, "size", num_low=size, num_high=size)
        return cid

    def job(self, company, title="Software Engineer", *, posted="2026-11-09", tier="new_grad", ok=1, **dec):
        pid = posting(self.conn, company, title, posted_on=posted, first_seen_at="2026-11-01T12:00:00Z",
                      last_seen_at="2026-11-09T12:00:00Z")
        decision(self.conn, pid, level_tier=tier, level_ok=ok, **dec)
        return pid

    def jobs(self, status=200, **params):
        res = self.get("/api/jobs", params=params)
        self.assertEqual(res.status_code, status, res.text)
        return res.json()

    def ids(self, **params):
        return [r["postingId"] for r in self.jobs(**params)["rows"]]


class ListTest(JobsCase):
    def test_the_default_view_is_your_level_in_the_last_30_days_newest_first(self):
        co = self.company(size=40)
        new, old = self.job(co, posted="2026-11-09"), self.job(co, posted="2026-10-20")
        self.job(co, posted="2026-09-01")  # outside 30 days
        self.job(co, tier="swe2", ok=0, level_state="reject")  # higher level
        body = self.jobs()
        self.assertEqual([r["postingId"] for r in body["rows"]], [new, old])
        self.assertEqual((body["total"], body["nextAfter"], body["asOf"]), (2, None, self.now))
        self.assertEqual(body["counts"], {"yourLevel": 2, "higher": 1})

    def test_a_row_is_camel_case_with_every_fact(self):
        co = self.company("Acme", size=40)
        pid = self.job(co, "Software Engineer, New Grad")
        row = self.jobs()["rows"][0]
        self.assertEqual(row["postingId"], pid)
        self.assertEqual(
            sorted(row), sorted(["postingId", "title", "url", "location", "levelTag", "date", "dateKind", "ageDays",
                                 "companyId", "companyName", "size", "big", "saysNoSponsorship", "everify", "yc",
                                 "contactedOn"]))
        self.assertEqual((row["companyName"], row["size"], row["big"], row["levelTag"], row["dateKind"],
                          row["ageDays"], row["date"]), ("Acme", 40, False, "new grad", "posted", 1, "2026-11-09"))

    def test_view_higher_lists_the_other_postings(self):
        co = self.company(size=40)
        self.job(co)
        high = self.job(co, tier="swe2", ok=0, level_state="reject")
        self.assertEqual(self.ids(view="higher"), [high])

    def test_window_any_and_7(self):
        co = self.company(size=40)
        fresh, old = self.job(co, posted="2026-11-08"), self.job(co, posted="2026-08-01")
        self.assertEqual(self.ids(window="7"), [fresh])
        self.assertEqual(self.ids(window="any"), [fresh, old])
        self.assertEqual(self.ids(window="90"), [fresh])

    def test_size_filter(self):
        small, big = self.company(size=40), self.company(size=5000)
        a, b = self.job(small), self.job(big)
        self.assertEqual(self.ids(size="small"), [a])
        self.assertEqual(self.ids(size="big"), [b])
        self.assertEqual(self.ids(size="unknown"), [])

    def test_q_searches_the_company_name(self):
        a, b = self.job(self.company("Lantern Labs", size=10)), self.job(self.company("Quarry", size=10))
        self.assertEqual(self.ids(q="lantern"), [a])
        self.assertEqual(self.ids(q="quar"), [b])

    def test_not_contacted_hides_a_company_he_reached(self):
        from tests.plan_seed import seed_outbound, seed_person
        reached, fresh = self.company("Reached", size=10), self.company("Fresh", size=10)
        self.job(reached)
        keep = self.job(fresh)
        person = seed_person(self.conn, reached, "Ana Bell")
        seed_outbound(self.conn, person, reached, "linkedin_request", date(2026, 11, 5))
        self.assertEqual(len(self.ids()), 2)
        self.assertEqual(self.ids(notContacted="true"), [keep])

    def test_paging_by_cursor(self):
        co = self.company(size=10)
        made = [self.job(co, posted=f"2026-11-0{n}") for n in range(1, 6)]
        first = self.jobs(limit=2)
        self.assertEqual([r["postingId"] for r in first["rows"]], [made[4], made[3]])
        self.assertEqual(first["total"], 5)
        self.assertIsNotNone(first["nextAfter"])
        second = self.jobs(limit=2, after=first["nextAfter"])
        self.assertEqual([r["postingId"] for r in second["rows"]], [made[2], made[1]])
        last = self.jobs(limit=2, after=second["nextAfter"])
        self.assertEqual([r["postingId"] for r in last["rows"]], [made[0]])
        self.assertIsNone(last["nextAfter"])

    def test_an_empty_store_is_an_empty_list(self):
        self.assertEqual(self.jobs(), {"asOf": self.now, "rows": [], "total": 0, "nextAfter": None,
                                       "counts": {"yourLevel": 0, "higher": 0}})


class BadParameterTest(JobsCase):
    def bad(self, **params):
        body = self.jobs(status=400, **params)
        self.assertEqual(body["code"], "bad_request")
        self.assertNotIn("Traceback", body["message"])
        return body

    def test_each_bad_parameter_is_a_400_in_plain_words(self):
        self.bad(view="sideways")
        self.bad(window="14")
        self.bad(size="medium")
        self.bad(limit="0")
        self.bad(limit="500")
        self.bad(notContacted="maybe")
        self.bad(q="x" * 101)

    def test_a_malformed_cursor_is_a_400(self):
        body = self.bad(after="not-a-cursor")
        self.assertEqual(body["message"], "That page link is out of date. Reload the list.")
        self.assertNotIn("not-a-cursor", body["message"])


class CacheTest(JobsCase):
    def test_a_second_identical_call_is_served_from_the_view_cache(self):
        self.addCleanup(self.app.state.view_cache.close)
        self.job(self.company(size=10))
        first = self.get("/api/jobs?view=your_level").content
        second = self.get("/api/jobs?view=your_level").content
        self.assertEqual(first, second)
        self.assertEqual(self.app.state.view_cache.computed, 1)
