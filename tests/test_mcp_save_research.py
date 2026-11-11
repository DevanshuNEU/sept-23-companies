"""save_research (#205): Claude's facts are kept only when code finds the quote on the page. Pages come from a fake
client; nothing here reaches the network."""
from unittest import mock

import live_checkout

from outreach.mcp import server
from outreach.net import Response
from tests import apiseed as seed
from tests.mcptest import NOW, McpCase
from tests.test_mcp_reads import merge

PAGE = "https://acme.example/about"
HTML = "<html><body><p>We build queue tools for small teams.</p><p>Founded in 2021 by two friends.</p></body></html>"


class FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, headers=None, *, follow_redirects=False, max_bytes=None):
        self.calls.append(url)
        return Response(status=200, url=url, body=self.pages[url].encode(), headers={"content-type": "text/html"})


def fact(quote="We build queue tools for small teams.", url=PAGE, **extra):
    return {"fact": "Builds queue tools for small teams", "quote": quote, "url": url, **extra}


class SaveResearchCase(McpCase):
    def setUp(self):
        super().setUp()
        self.client = FakeClient({PAGE: HTML})
        patcher = mock.patch("outreach.mcp.tools.research._client", return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)
        live = mock.patch.object(live_checkout, "is_live", return_value=True)
        live.start()
        self.addCleanup(live.stop)
        self.acme = seed.company(self.db, "Acme", "working")
        self.pat = seed.person(self.db, self.acme, "Pat Lee")

    def call_save(self, facts, **extra):
        return self.ok("save_research", {"company_id": self.acme, "facts": facts, **extra})

    def rows(self, table):
        return self.db.execute(f"SELECT * FROM {table}").fetchall()


class SaveResearchTest(SaveResearchCase):
    def test_save_research_keeps_checked_company_facts(self):
        got = self.call_save([fact(page_date="2026-10-01")])
        self.assertEqual((got["saved"], got["already_known"], got["rejected"]), (1, 0, []))
        row = self.rows("research_fact")[0]
        self.assertEqual((row["company_id"], row["found_by"], row["url"], row["page_date"], row["verified_at"]),
                         (self.acme, "claude", PAGE, "2026-10-01", NOW))
        ev = self.db.execute("SELECT entity, kind, actor, company_id FROM event WHERE kind = 'research_saved'")
        self.assertEqual([tuple(e) for e in ev], [("company", "research_saved", "claude", self.acme)])
        again = self.call_save([fact()])
        self.assertEqual((again["saved"], again["already_known"]), (0, 1))
        self.assertEqual(len(self.rows("research_fact")), 1)
        self.assertEqual(len(self.db.execute("SELECT 1 FROM event WHERE kind = 'research_saved'").fetchall()), 1)

    def test_a_merged_company_stores_on_its_survivor(self):
        other = seed.company(self.db, "Acme Inc", "working")
        merge(self.db, other, self.acme)
        got = self.ok("save_research", {"company_id": other, "facts": [fact()]})
        self.assertEqual(got["company_id"], self.acme)
        self.assertEqual(self.rows("research_fact")[0]["company_id"], self.acme)

    def test_save_research_stores_person_facts_as_sources(self):
        got = self.call_save([fact(page_date="2026-10-01")], person_id=self.pat)
        self.assertEqual((got["saved"], got["rejected"]), (1, []))
        s = self.rows("person_source")[0]
        self.assertEqual((s["person_id"], s["kind"], s["url"], s["label"], s["quote"], s["observed_on"],
                          s["email_basis"]),
                         (self.pat, "post", PAGE, "Builds queue tools for small teams",
                          "We build queue tools for small teams.", "2026-10-01", None))
        self.assertEqual(self.rows("research_fact"), [])

    def test_a_second_fact_on_the_same_page_for_a_person_is_already_known(self):
        got = self.call_save([fact(), fact("Founded in 2021 by two friends.")], person_id=self.pat)
        self.assertEqual((got["saved"], got["already_known"]), (1, 1))
        self.assertEqual(len(self.rows("person_source")), 1)

    def test_rejected_facts_are_reported_and_not_stored(self):
        got = self.call_save([fact("We build rockets for the moon."), fact(url="https://www.linkedin.com/company/acme"), fact()])
        self.assertEqual(got["saved"], 1)
        self.assertEqual([(r["quote"], r["reason"]) for r in got["rejected"]],
                         [("We build rockets for the moon.", "quote not found on the page"),
                          ("We build queue tools for small teams.", "LinkedIn pages are not used")])
        self.assertEqual(len(self.rows("research_fact")), 1)

    def test_a_link_the_table_refuses_does_not_lose_the_other_facts(self):
        got = self.call_save([fact(url=PAGE + "?utm_source=linkedin.com"), fact()])
        self.assertEqual((got["saved"], [r["reason"] for r in got["rejected"]]), (1, ["LinkedIn pages are not used"]))
        self.assertEqual(len(self.rows("research_fact")), 1)

    def test_a_recheck_after_the_fact_went_stale_refreshes_it(self):
        self.call_save([fact()])
        self.db.execute("UPDATE research_fact SET verified_at = '2026-01-01T00:00:00Z'")
        got = self.call_save([fact()])
        self.assertEqual((got["saved"], got["already_known"]), (1, 0))
        self.assertEqual(self.rows("research_fact")[0]["verified_at"], NOW)

    def test_nothing_kept_writes_nothing_and_no_event(self):
        got = self.call_save([fact("We build rockets for the moon.")])
        self.assertEqual(got["saved"], 0)
        self.assertEqual(self.rows("research_fact"), [])
        self.assertEqual(self.db.execute("SELECT 1 FROM event WHERE kind = 'research_saved'").fetchall(), [])

    def test_person_from_another_company_is_refused(self):
        other = seed.company(self.db, "Elsewhere", "working")
        stranger = seed.person(self.db, other, "Sam Roe")
        err = self.error("save_research", {"company_id": self.acme, "person_id": stranger, "facts": [fact()]})
        self.assertEqual(err["code"], "wrong_company")
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.rows("person_source"), [])

    def test_bad_input_is_refused(self):
        for bad in ({"facts": []}, {"facts": [fact()] * 9}, {"facts": [fact(url="http://acme.example/a")]},
                    {"facts": [fact(page_date="2026-13-40")]}):
            with self.subTest(bad=bad):
                is_error, _ = self.call("save_research", {"company_id": self.acme, **bad})
                self.assertTrue(is_error)
        self.assertEqual(self.error("save_research", {"company_id": 9999, "facts": [fact()]})["code"], "not_found")

    def test_tool_is_registered_with_its_grants(self):
        tool = next(s for s in server.tool_specs() if s.name == "save_research")
        self.assertEqual({t: (g.insert, set(g.update)) for t, g in tool.grants.items()},
                         {"research_fact": (True, {"verified_at", "page_date"}), "person_source": (True, set()), "event": (True, set())})

    def test_outside_the_live_checkout_nothing_is_fetched(self):
        with mock.patch.object(live_checkout, "is_live", return_value=False):
            err = self.error("save_research", {"company_id": self.acme, "facts": [fact()]})
        self.assertEqual(err["code"], "not_live")
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.rows("research_fact"), [])
