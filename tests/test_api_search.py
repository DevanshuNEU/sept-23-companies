"""Search: the index, posting search and company search, read from a scratch store seeded through SQL."""
import unittest

from tests import apiseed as s
from tests.apitest import NOW, ApiCase


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
        lines = {
            s.company(self.conn, "A", "candidate"): "Candidate",
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

    # TODO(#6): the capped index (touched companies and the top scored) and its test,
    # test_search_index_cap_keeps_touched_companies_and_the_top_scored, land with the ranking package.

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
            s.company(self.conn, f"Company number {i}")
        big = self.get("/api/search/index", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(big.headers.get("content-encoding"), "gzip")
        self.assertEqual(len(big.json()["companies"]), 40)  # the client unpacks it
        small = self.get("/api/search/postings", params={"q": "none"}, headers={"Accept-Encoding": "gzip"})
        self.assertNotIn("gzip", small.headers.get("content-encoding", ""))


if __name__ == "__main__":
    unittest.main()
