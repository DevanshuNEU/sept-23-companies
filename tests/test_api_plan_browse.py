"""Browse companies (#120): `GET /api/plan/browse` lists every company the plan could pick, in stored rank order, paged by
a cursor, with a name search and three filters. It never lists a no-go company, a company on today's list or one in
flight; a held back company is listed with its reason. Scratch store, reads only."""
import dataclasses
from unittest import mock

from outreach.outcomes.context import build_context
from outreach.plan.config import default_plan_config
from outreach.store import log_touch
from tests import apiseed as s
from tests.test_api_plan_picks import DAY, PicksCase

HEAD = ("Gale", "Haven", "Ember", "Dune", "Fable")  # the seed's pool by score, minus today's list, Iris and Jolt


def _context():
    return build_context(source="plan", lane="level_role", role_kind="engineer", role_age_days=3,
                         ranker_version="rk-1", signals={}, role_named=False)


class BrowseCase(PicksCase):
    def browse(self, **params):
        res = self.get("/api/plan/browse", params=params)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def listed(self, **params):
        return self.names(self.browse(**params)["companies"])

    def add_company(self, name, score, **kw):
        lanes = kw.pop("lanes", ("level_role",))
        cid = s.company(self.conn, name)
        s.score(self.conn, cid, {"live_role": float(score)}, lanes=lanes)
        self.ids[name] = cid
        return cid


class ListTest(BrowseCase):
    def test_lists_in_stored_rank_order_with_the_ranked_position(self):
        b = self.browse()
        self.assertEqual(self.names(b["companies"]), list(HEAD))
        self.assertEqual([c["position"] for c in b["companies"]], [1, 2, 5, 9, 10])
        self.assertEqual((b["total"], b["nextAfter"], b["date"]), (5, None, DAY))

    def test_a_no_go_company_is_never_listed(self):
        b = self.browse()
        self.assertNotIn("Jolt", self.names(b["companies"]))
        self.assertEqual(b["total"], 5)  # never counted either
        self.assertNotIn("Jolt", self.listed(q="jolt"))

    def test_a_lifted_no_go_company_is_listed_again(self):
        self.conn.execute("UPDATE no_go SET lifted_at = '2026-11-05T12:00:00Z', lift_reason = 'mistake' WHERE company_id = ?", (self.ids["Jolt"],))
        self.assertIn("Jolt", self.listed())

    def test_todays_list_and_in_flight_companies_are_never_listed(self):
        got = self.listed()
        for name in ("Beacon", "Corvid", "Aster", "Iris"):
            self.assertNotIn(name, got)
        self.assertEqual(self.browse()["total"], 5)

    def test_a_company_with_a_removed_row_today_is_not_listed_either(self):
        self.pass_("Beacon")
        self.assertNotIn("Beacon", self.listed())

    def test_a_held_back_company_is_listed_with_its_reason(self):
        by = {c["company"]["name"]: c for c in self.browse()["companies"]}
        self.assertIn("Nov 7", by["Gale"]["blocked"])
        self.assertIn("Not now", by["Gale"]["blocked"])
        self.assertIn("applied", by["Haven"]["blocked"])
        self.assertIsNone(by["Ember"]["blocked"])

    def test_rows_carry_the_pick_facts(self):
        dune = next(c for c in self.browse()["companies"] if c["company"]["name"] == "Dune")
        q = dune["queue"]
        self.assertEqual(q["opening"]["title"], "Software Engineer I")
        self.assertEqual(q["sponsorshipLine"], "Sponsorship not stated")
        self.assertEqual(q["linkedinCompanyUrl"], "https://www.linkedin.com/search/results/companies/?keywords=Dune")
        for key in ("whyToday", "reasons", "sizeLine", "careersUrl", "websiteUrl", "peopleSearchUrl"):
            self.assertIn(key, q)
        self.assertEqual((dune["lane"], dune["fresh"]), ("level_role", True))

    def test_a_company_past_the_scan_limit_is_listed(self):
        cfg = default_plan_config()
        small = dataclasses.replace(cfg, plan={**cfg.plan, "scan_limit": 2})
        with mock.patch("outreach.api.routers.plan.default_plan_config", return_value=small):
            self.assertEqual(self.listed(), list(HEAD))

    def test_the_read_writes_nothing(self):
        before = [self.count(f"SELECT count(*) FROM {t}") for t in ("event", "daily_list_entry", "no_go")]
        self.browse(q="e")
        self.assertEqual([self.count(f"SELECT count(*) FROM {t}") for t in ("event", "daily_list_entry", "no_go")], before)


class SearchTest(BrowseCase):
    def test_search_matches_name_keys(self):
        self.add_company("Retell AI, Inc.", 40)
        self.assertEqual(self.listed(q="retell inc"), ["Retell AI, Inc."])
        self.assertEqual(self.listed(q="RETELL"), ["Retell AI, Inc."])
        self.assertEqual(self.listed(q="ell"), ["Retell AI, Inc."])

    def test_a_short_name_is_found(self):
        self.add_company("Ax", 41)
        self.assertEqual(self.listed(q="ax"), ["Ax"])

    def test_search_reads_the_stored_aliases_and_legal_names(self):
        self.conn.execute("INSERT INTO company_name_key(name_key, company_id, strong) VALUES ('emberlabs', ?, 1)",
                          (self.ids["Ember"],))
        self.assertEqual(self.listed(q="ember labs"), ["Ember"])

    def test_a_query_with_no_letters_lists_nothing_and_does_not_fail(self):
        b = self.browse(q="...")
        self.assertEqual((b["companies"], b["total"], b["nextAfter"]), ([], 0, None))

    def test_search_never_lists_what_is_excluded(self):
        for name in ("Jolt", "Iris", "Beacon"):
            self.assertEqual(self.listed(q=name.lower()), [])

    def test_search_counts_only_matches(self):
        self.assertEqual(self.browse(q="a")["total"], len(self.listed(q="a")))


class FilterTest(BrowseCase):
    def test_level_role_filter(self):
        self.add_company("Mist", 55, lanes=("any_us_role",))
        self.assertIn("Mist", self.listed())
        self.assertNotIn("Mist", self.listed(levelRole="true"))
        self.assertEqual(self.listed(levelRole="true"), list(HEAD))

    def test_visa_filter_drops_a_company_whose_sponsorship_is_no(self):
        s.evidence(self.conn, self.ids["Ember"], "sponsorship", "no", locked=True)
        self.assertIn("Ember", self.listed())
        self.assertNotIn("Ember", self.listed(visaOk="true"))
        self.assertEqual(self.browse(visaOk="true")["total"], 4)

    def test_visa_filter_keeps_an_unstated_or_yes_company(self):
        s.evidence(self.conn, self.ids["Dune"], "sponsorship", "yes", locked=True)
        self.assertEqual(self.listed(visaOk="true"), list(HEAD))

    def test_unworked_filter_drops_touched_and_applied_companies(self):
        kite = self.add_company("Kite", 45)
        pid = s.person(self.conn, kite, "Ana Bell", channel="linkedin")
        log_touch(self.conn, person_id=pid, company_id=kite, channel="linkedin_request",
                  sent_at="2026-10-05T15:00:00Z", context=_context(), idempotency_key="seed:kite")
        got = {c["company"]["name"]: c for c in self.browse()["companies"]}
        self.assertTrue(got["Kite"]["worked"] and got["Haven"]["worked"])
        self.assertFalse(got["Ember"]["worked"])
        unworked = self.listed(unworked="true")
        self.assertEqual(unworked, ["Gale", "Ember", "Dune", "Fable"])  # Haven applied, Kite was asked

    def test_filters_combine_with_search(self):
        s.evidence(self.conn, self.ids["Ember"], "sponsorship", "no", locked=True)
        self.assertEqual(self.listed(q="e", visaOk="true"), ["Gale", "Haven", "Dune", "Fable"])


class PagingTest(BrowseCase):
    def test_pages_are_disjoint_and_chain_by_cursor(self):
        first = self.browse(limit=2)
        self.assertEqual(self.names(first["companies"]), ["Gale", "Haven"])
        self.assertEqual((first["total"], first["nextAfter"]), (5, 2))
        second = self.browse(limit=2, after=first["nextAfter"])
        self.assertEqual(self.names(second["companies"]), ["Ember", "Dune"])
        self.assertEqual(second["nextAfter"], 9)
        third = self.browse(limit=2, after=second["nextAfter"])
        self.assertEqual((self.names(third["companies"]), third["nextAfter"]), (["Fable"], None))

    def test_a_cursor_past_the_end_is_an_empty_page(self):
        b = self.browse(after=99)
        self.assertEqual((b["companies"], b["nextAfter"], b["total"]), ([], None, 5))

    def test_the_limit_is_bounded(self):
        for bad in (0, 51):
            self.assertEqual(self.get("/api/plan/browse", params={"limit": bad}).status_code, 400)
