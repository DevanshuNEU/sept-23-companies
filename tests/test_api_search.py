"""Search: the index, posting search and company search, read from a scratch store seeded through SQL."""
import unittest
from unittest import mock

from outreach.api.queries import score as score_queries
from outreach.api.queries import search
from tests import apiseed as s
from tests.apitest import NOW, ApiCase
from tests.storetest import fresh_store


class Search(ApiCase):
    def index(self) -> dict:
        res = self.get("/api/search/index")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def postings(self, q: str) -> list[dict]:
        res = self.get("/api/search/postings", params={"q": q})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def companies(self, q: str) -> list[dict]:
        res = self.get("/api/search/companies", params={"q": q})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_empty_store_has_an_empty_index(self):
        self.assertEqual(self.index(), {"asOf": NOW, "companies": [], "people": []})

    def test_search_index_aliases_and_domain(self):
        cid = s.company(self.conn, "Acme", "working")
        s.alias(self.conn, cid, "Acme Labs")
        s.alias(self.conn, cid, "ashby:acme", source="ats")
        s.evidence(self.conn, cid, "domain", "acme.example")
        s.company(self.conn, "Zed", "backlog")
        pid = s.person(self.conn, cid, "Rae Replied", title="Staff Engineer", email="rae@acme.example")
        s.send_first(self.conn, s.touch(self.conn, pid, "replied"))
        s.person(self.conn, cid, "Lin Kedin", channel="linkedin", email=None)
        index = self.index()
        by = {c["name"]: c for c in index["companies"]}
        self.assertEqual(
            by["Acme"],
            {"id": cid, "name": "Acme", "aliases": ["Acme Labs"], "domain": "acme.example", "status": "working",
             "statusLine": "Emailing", "inProgress": True},
        )
        self.assertEqual((by["Zed"]["status"], by["Zed"]["statusLine"], by["Zed"]["inProgress"], by["Zed"]["domain"]),
                         ("backlog", "Backlog", False, None))
        people = {p["name"]: p for p in index["people"]}
        self.assertEqual(
            people["Rae Replied"],
            {"id": pid, "companyId": cid, "companyName": "Acme", "name": "Rae Replied", "title": "Staff Engineer",
             "email": "rae@acme.example", "statusLine": "Replied"},
        )
        self.assertEqual((people["Lin Kedin"]["email"], people["Lin Kedin"]["statusLine"]), (None, "Not started"))

    def test_status_lines_per_status(self):
        candidate = s.company(self.conn, "A", "candidate")
        s.score(self.conn, candidate, {"live_role": 10.0})  # a candidate is indexed when it scores high enough
        lines = {
            candidate: "Candidate",
            s.company(self.conn, "B", "closed", close_reason="interview"): "Done: interview",
            s.company(self.conn, "C", "closed", close_reason="no_sponsorship"): "Closed: no sponsorship",
            s.company(self.conn, "D", "removed", remove_reason="not_a_fit"): "Removed by you",
            s.company(self.conn, "E", "working", paused_at="2026-11-09T12:00:00Z"): "Paused by you",
        }
        got = {c["id"]: c["statusLine"] for c in self.index()["companies"]}
        self.assertEqual(got, lines)

    def test_merged_companies_leave_the_index_and_lend_their_name_to_the_survivor(self):
        a = s.company(self.conn, "Acme")
        b = s.company(self.conn, "Acme Labs", "removed", remove_reason="merged", merged_into=a)
        s.person(self.conn, b, "Pat Lee")
        index = self.index()
        self.assertEqual([c["id"] for c in index["companies"]], [a])
        self.assertEqual(index["companies"][0]["aliases"], ["Acme Labs"])
        self.assertEqual((index["people"][0]["companyId"], index["people"][0]["companyName"]), (a, "Acme"))

    def test_a_person_on_a_merged_duplicate_is_judged_against_the_survivors_pause(self):
        a = s.company(self.conn, "Acme", "working", paused_at="2026-11-09T12:00:00Z")
        b = s.company(self.conn, "Acme Labs", "removed", remove_reason="merged", merged_into=a)
        pid = s.person(self.conn, b, "Ann Approved")
        s.message(self.conn, s.touch(self.conn, pid), 0, "approved")
        self.assertEqual([p["statusLine"] for p in self.index()["people"]], ["Paused, approved"])

    def test_a_hold_on_a_merged_duplicate_holds_the_survivor_in_the_index(self):
        a = s.company(self.conn, "Acme", "working")
        b = s.company(self.conn, "Acme Labs", "removed", remove_reason="merged", merged_into=a)
        t = s.touch(self.conn, s.person(self.conn, b, "Rae Replied"), "replied")
        s.send_first(self.conn, t)
        self.conn.execute("UPDATE company SET held_by_touch = ?, held_at = ? WHERE company_id = ?", (t, s.T0, b))
        s.message(self.conn, s.touch(self.conn, s.person(self.conn, a, "Ann Approved")), 0, "approved")
        index = self.index()
        self.assertEqual([(c["id"], c["statusLine"]) for c in index["companies"]], [(a, "Held: reply")])
        lines = {p["name"]: p["statusLine"] for p in index["people"]}
        self.assertEqual(lines["Ann Approved"], "Held, approved")

    def test_a_merge_cycle_does_not_break_the_index(self):
        a = s.company(self.conn, "Acme", "removed", remove_reason="merged")
        b = s.company(self.conn, "Acme Labs", "removed", remove_reason="merged", merged_into=a)
        self.conn.execute("UPDATE company SET merged_into = ? WHERE company_id = ?", (b, a))
        s.person(self.conn, a, "Pat Lee")
        s.score(self.conn, s.company(self.conn, "Fine Co"), {"live_role": 10.0})  # a scored candidate is indexed
        index = self.index()
        self.assertEqual([c["name"] for c in index["companies"]], ["Fine Co"])
        self.assertEqual(len(index["people"]), 1)  # kept, under a company name that exists
        self.assertIn(index["people"][0]["companyName"], ("Acme", "Acme Labs"))

    def test_company_search_never_cuts_the_best_name_match(self):
        for i in range(205):
            s.company(self.conn, f"Zed orbit {i:03d}")
        s.company(self.conn, "Orbital")
        s.company(self.conn, "Orbit")
        names = [h["name"] for h in self.companies("orbit")]
        self.assertEqual(names[:2], ["Orbit", "Orbital"])  # exact, then prefix, before the 200 substring matches

    def test_company_search_never_cuts_the_best_alias_or_domain_match(self):
        owner = s.company(self.conn, "Holder")
        for i in range(205):
            c = s.company(self.conn, f"Filler {i:03d}")
            s.alias(self.conn, c, f"Big orbit {i:03d}")
            s.evidence(self.conn, c, "domain", f"x{i:03d}.orbit.example")
        s.alias(self.conn, owner, "Orbit")
        late = s.company(self.conn, "Late")
        s.evidence(self.conn, late, "domain", "orbit.example")
        self.assertEqual(self.companies("orbit")[0]["name"], "Holder")  # an exact alias beats the substrings
        self.assertEqual(self.companies("orbit.example")[0]["name"], "Late")  # an exact domain too

    def test_search_index_cap_keeps_touched_companies_and_the_top_scored(self):
        working = s.company(self.conn, "Working", "working")
        done = s.company(self.conn, "Done", "closed", close_reason="interview")
        backlog = s.company(self.conn, "Backlog", "backlog")
        with_person = s.company(self.conn, "With Person")
        s.person(self.conn, with_person, "Pat Lee")
        survivor = s.company(self.conn, "Survivor")
        duplicate = s.company(self.conn, "Duplicate", "removed", remove_reason="merged", merged_into=survivor)
        s.person(self.conn, duplicate, "Dee Dupe")  # a person on a duplicate touches the survivor
        top = [s.company(self.conn, f"Top {i}") for i in range(3)]
        low = [s.company(self.conn, f"Low {i}") for i in range(2)]
        hidden = s.company(self.conn, "Hidden")
        unscored = s.company(self.conn, "Unscored")
        stale = s.company(self.conn, "Only Old Version")
        for points, cid in zip((90.0, 80.0, 70.0), top):
            s.score(self.conn, cid, {"live_role": points})
        for points, cid in zip((60.0, 50.0), low):
            s.score(self.conn, cid, {"live_role": points})
        s.score(self.conn, hidden, {"live_role": 99.0}, hide={"kind": "no_go", "reason": "No", "provisional": False})
        s.score(self.conn, stale, {"live_role": 98.0}, version="rk-old", at="2026-10-01T08:00:00Z")
        s.score(self.conn, top[0], {"live_role": 1.0}, version="rk-old", at="2026-10-01T08:00:00Z")
        with mock.patch.object(search, "INDEX_TOP_SCORED", 3):
            index = self.index()
        names = [c["name"] for c in index["companies"]]
        self.assertEqual(names, sorted(["Working", "Done", "Backlog", "With Person", "Survivor", "Top 0", "Top 1", "Top 2"],
                                       key=str.lower))
        absent = {"Low 0", "Low 1", "Hidden", "Unscored", "Only Old Version", "Duplicate"}
        self.assertFalse(absent & set(names), absent & set(names))
        self.assertEqual({p["name"] for p in index["people"]}, {"Pat Lee", "Dee Dupe"})
        self.assertEqual([c["name"] for c in self.companies("low 1")], ["Low 1"])  # found by the live company search
        self.assertEqual({working, done, backlog} - {c["id"] for c in index["companies"]}, set())

    def test_search_index_cap_default_is_3000_and_an_empty_ranking_changes_nothing(self):
        self.assertEqual(search.INDEX_TOP_SCORED, 3000)
        cid = s.company(self.conn, "Candidate")
        self.assertEqual([c["name"] for c in self.index()["companies"]], [])  # unscored, untouched: not in the index
        s.company(self.conn, "Working", "working")
        self.assertEqual([c["name"] for c in self.index()["companies"]], ["Working"])
        self.assertEqual([c["name"] for c in self.companies("candid")], ["Candidate"])
        self.assertTrue(cid)

    def test_top_scored_ids_are_the_rankers_ranked_list(self):
        from outreach.ranking.scores import ranked_list
        ids = [s.company(self.conn, f"C{i}") for i in range(6)]
        for i, cid in enumerate(ids):
            hide = {"kind": "no_go", "reason": "No", "provisional": True} if i == 4 else None
            s.score(self.conn, cid, {"live_role": float(10 * (i % 3))}, hide=hide)
        for limit in (1, 3, 6, 50):
            self.assertEqual(score_queries.top_scored_ids(self.conn, limit),
                             [r.company_id for r in ranked_list(self.conn, limit=limit)])
        self.assertEqual(score_queries.top_scored_ids(self.conn, 0), [])

    def test_search_index_reads_a_fixed_number_of_statements(self):
        def count(n: int) -> int:
            conn = fresh_store()
            self.addCleanup(conn.close)
            for i in range(n):
                cid = s.company(conn, f"Co {i}", "working" if i % 2 else "candidate")
                s.score(conn, cid, {"live_role": float(i)})
                if i % 3 == 0:
                    s.person(conn, cid, f"Person {i}")
            seen: list[str] = []
            conn.set_trace_callback(seen.append)
            search.index(conn, NOW)
            return len(seen)

        self.assertEqual(count(10), count(60))  # never one query per company

    def test_posting_search_matches_initials(self):
        cid = s.company(self.conn, "Acme")
        mts = s.posting(self.conn, cid, "Member of Technical Staff")
        designer = s.posting(self.conn, cid, "Product Designer")
        self.assertEqual([h["postingId"] for h in self.postings("mts")], [mts])
        self.assertEqual([h["postingId"] for h in self.postings("MTS")], [mts])
        self.assertEqual([h["postingId"] for h in self.postings("technical")], [mts])  # substring
        self.assertEqual([h["postingId"] for h in self.postings("pd")], [designer])  # initials of Product Designer
        self.assertEqual([h["postingId"] for h in self.postings("d")], [designer])  # one letter is a substring match only
        self.assertEqual([h["postingId"] for h in self.postings("z")], [])

    def test_posting_hit_shape_open_first_and_a_limit_of_20(self):
        cid = s.company(self.conn, "Acme")
        closed = s.posting(self.conn, cid, "Backend Engineer", closed_at="2026-11-02T10:00:00Z")
        ids = [s.posting(self.conn, cid, f"Backend Engineer {i}") for i in range(25)]
        hits = self.postings("backend")
        self.assertEqual(len(hits), 20)
        self.assertEqual(hits[0], {"postingId": ids[-1], "title": "Backend Engineer 24", "companyId": cid, "companyName": "Acme", "open": True})
        self.assertNotIn(closed, [h["postingId"] for h in hits])  # twenty open ones fill it first

    def test_posting_search_lists_open_postings_before_closed_ones(self):
        cid = s.company(self.conn, "Acme")
        closed = s.posting(self.conn, cid, "Data Engineer", closed_at="2026-11-02T10:00:00Z")
        newer_open = s.posting(self.conn, cid, "Data Engineer II")
        hits = self.postings("data engineer")
        self.assertEqual([(h["postingId"], h["open"]) for h in hits], [(newer_open, True), (closed, False)])

    def test_posting_search_answers_a_merged_company_as_its_survivor(self):
        a = s.company(self.conn, "Acme")
        b = s.company(self.conn, "Acme Labs", "removed", remove_reason="merged", merged_into=a)
        s.posting(self.conn, b, "Platform Engineer")
        hit = self.postings("platform")[0]
        self.assertEqual((hit["companyId"], hit["companyName"]), (a, "Acme"))

    def test_search_empty_query_is_empty(self):
        cid = s.company(self.conn, "Acme")
        s.posting(self.conn, cid, "Engineer")
        self.assertEqual(self.postings(""), [])
        self.assertEqual(self.postings("   "), [])
        self.assertEqual(self.get("/api/search/postings").json(), [])

    def test_company_search_finds_a_candidate_outside_the_index_and_answers_merged_ids_as_survivors(self):
        a = s.company(self.conn, "Acme")
        b = s.company(self.conn, "Zephyr Works", "removed", remove_reason="merged", merged_into=a)
        s.alias(self.conn, b, "Zeph")
        s.evidence(self.conn, b, "domain", "zephyrworks.example")
        gone = s.company(self.conn, "Gone Co", "removed", remove_reason="not_a_fit")
        cand = s.company(self.conn, "Plain Candidate")
        s.evidence(self.conn, cand, "domain", "plain.example")
        names = lambda q: [h["name"] for h in self.companies(q)]
        self.assertEqual(names("candidate"), ["Plain Candidate"])
        self.assertEqual(names("zephyr"), ["Acme"])  # the merged name finds its survivor
        self.assertEqual(names("zeph"), ["Acme"])  # one hit, though the name and the alias both match
        self.assertEqual(names("zephyrworks.example"), ["Acme"])  # a domain of the merged company
        self.assertEqual(names("PLAIN.EX"), ["Plain Candidate"])  # a domain substring, any case
        self.assertEqual(names("gone"), ["Gone Co"])  # any status
        hit = self.companies("gone")[0]
        self.assertEqual(hit, {"id": gone, "name": "Gone Co", "aliases": [], "domain": None, "status": "removed",
                               "statusLine": "Removed by you", "inProgress": False})
        self.assertEqual(self.companies("zephyr")[0]["id"], a)
        self.assertEqual(names("nothing like this"), [])

    def test_company_search_orders_name_matches_first_and_stops_at_20(self):
        for i in range(25):
            s.company(self.conn, f"Orbit {i:02d}")
        c = s.company(self.conn, "Zed")
        s.alias(self.conn, c, "Orbital Labs")
        hits = self.companies("orbit")
        self.assertEqual(len(hits), 20)
        self.assertEqual([h["name"] for h in hits][:3], ["Orbit 00", "Orbit 01", "Orbit 02"])
        self.assertEqual([h["name"] for h in self.companies("orbital")], ["Zed"])

    def test_company_search_treats_percent_and_underscore_as_plain_characters(self):
        s.company(self.conn, "Acme")
        s.company(self.conn, "100% Pure")
        self.assertEqual([h["name"] for h in self.companies("%")], ["100% Pure"])
        self.assertEqual(self.companies("_"), [])

    def test_company_search_empty_query_is_empty(self):
        s.company(self.conn, "Acme")
        self.assertEqual(self.companies(""), [])
        self.assertEqual(self.companies("  "), [])
        self.assertEqual(self.get("/api/search/companies").json(), [])

    def test_responses_over_1kb_are_gzipped_and_small_ones_are_not(self):
        for i in range(40):
            s.score(self.conn, s.company(self.conn, f"Company number {i}"), {"live_role": float(i)})
        big = self.get("/api/search/index", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(big.headers.get("content-encoding"), "gzip")
        self.assertEqual(len(big.json()["companies"]), 40)  # the client unpacks it
        small = self.get("/api/search/postings", params={"q": "none"}, headers={"Accept-Encoding": "gzip"})
        self.assertNotIn("gzip", small.headers.get("content-encoding", ""))


if __name__ == "__main__":
    unittest.main()
