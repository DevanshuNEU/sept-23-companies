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
        pid = posting(self.conn, company, title, posted_on=posted, first_seen_at=f"{posted}T12:00:00Z",
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
        self.job(co, tier="swe2", ok=0)  # higher level
        body = self.jobs()
        self.assertEqual([r["postingId"] for r in body["rows"]], [new, old])
        self.assertEqual((body["total"], body["nextAfter"], body["asOf"]), (2, None, self.now))
        self.assertEqual(body["counts"], {"yourLevel": 2, "higher": 1, "other": 0})

    def test_a_row_is_camel_case_with_every_fact(self):
        co = self.company("Acme", size=40)
        pid = self.job(co, "Software Engineer, New Grad")
        row = self.jobs()["rows"][0]
        self.assertEqual(row["postingId"], pid)
        self.assertEqual(
            sorted(row), sorted(["postingId", "title", "url", "location", "levelTag", "listAt", "postedAt", "firstSeenAt", "seenGapS", "firstRead", "repostedFrom",
                                 "companyId", "companyName", "size", "big", "saysNoSponsorship", "everify", "yc",
                                 "contactedOn"]))
        self.assertEqual((row["companyName"], row["size"], row["big"], row["levelTag"], row["firstSeenAt"]),
                         ("Acme", 40, False, "new grad", "2026-11-09T12:00:00Z"))

    def test_jobs_row_has_time_fields(self):
        co = self.company(size=40)
        gone = posting(self.conn, co, "Software Engineer", first_seen_at="2026-08-01T12:00:00Z",
                       last_seen_at="2026-10-01T12:00:00Z", closed=True)
        again = posting(self.conn, co, "Software Engineer", first_seen_at="2026-11-10T15:10:00Z",
                        last_seen_at="2026-11-10T15:10:00Z", posted_at="2026-11-10T15:02:00Z", seen_gap_s=1800,
                        repost_of=gone, list_at="2026-11-10T15:02:00Z")
        unknown = posting(self.conn, co, "Backend Engineer", first_seen_at="2026-11-09T12:00:00Z",
                          last_seen_at="2026-11-09T12:00:00Z", list_at=None)
        for pid in (again, unknown):
            decision(self.conn, pid, level_tier="new_grad", level_ok=1)
        rows = {r["postingId"]: r for r in self.jobs()["rows"]}
        self.assertEqual(list(rows), [again, unknown])
        time = ("listAt", "postedAt", "firstSeenAt", "seenGapS", "firstRead", "repostedFrom")
        self.assertEqual(tuple(rows[again][k] for k in time),
                         ("2026-11-10T15:02:00Z", "2026-11-10T15:02:00Z", "2026-11-10T15:10:00Z", 1800, False,
                          "2026-08-01T12:00:00Z"))
        self.assertEqual(tuple(rows[unknown][k] for k in time),
                         (None, None, "2026-11-09T12:00:00Z", None, True, None))

    def test_view_higher_lists_the_other_postings(self):
        co = self.company(size=40)
        self.job(co)
        high = self.job(co, tier="swe2", ok=0)
        self.assertEqual(self.ids(view="higher"), [high])

    def test_view_other_lists_the_other_roles(self):
        # #250: everything that is not a software role is on Other roles, with its own count.
        co = self.company(size=40)
        self.job(co)
        scientist = self.job(co, "Data Scientist I", role="other")
        body = self.jobs(view="other")
        self.assertEqual([r["postingId"] for r in body["rows"]], [scientist])
        self.assertEqual(body["counts"], {"yourLevel": 1, "higher": 0, "other": 1})

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
                                       "counts": {"yourLevel": 0, "higher": 0, "other": 0}})


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


class HourWindowAndLevelTest(JobsCase):
    """#213 on the wire: window 1h, 6h and 24h, and `levels`, a comma list of level chips."""

    def test_hour_windows(self):
        co = self.company(size=40)
        # NOW is 15:14 UTC on 2026-11-10.
        recent = posting(self.conn, co, "Recent", posted_on="2026-11-10", first_seen_at="2026-11-10T14:44:00Z",
                         last_seen_at="2026-11-10T14:44:00Z", list_at="2026-11-10T14:44:00Z")
        decision(self.conn, recent, level_tier="new_grad", level_ok=1)
        older = self.job(co, posted="2026-11-09")
        self.assertEqual(self.ids(window="1h"), [recent])
        self.assertEqual(self.ids(window="24h"), [recent])
        self.assertEqual(self.ids(window="7"), [recent, older])

    def test_levels_keep_the_chosen_chips(self):
        co = self.company(size=40)
        ng, s1 = self.job(co, "NG", tier="new_grad"), self.job(co, "S1", tier="swe1")
        self.job(co, "EC", tier="early_career")
        self.assertEqual(sorted(self.ids(levels="new_grad,swe1")), sorted([ng, s1]))
        self.assertEqual(self.ids(levels="swe1"), [s1])
        self.assertEqual(len(self.ids()), 3)

    def test_a_bad_window_or_level_is_a_400(self):
        for params in ({"window": "2h"}, {"levels": "senior"}, {"levels": "swe1,"}, {"levels": "SWE1"},
                       {"levels": "swe1;drop"}):
            res = self.get("/api/jobs", params=params)
            self.assertEqual((res.status_code, res.json()["code"]), (400, "bad_request"), params)
