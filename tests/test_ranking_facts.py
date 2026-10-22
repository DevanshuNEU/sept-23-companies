"""RankFacts and the facts loader (issue #6, Task 5; rulings 2026-10-22 on stale postings and confidence).

The loader reads the store in bulk, composes the lanes, the weak flags and the pay level, and grades every signal once.
A stale posting gives no lane, pay or sponsor credit; a merged-away company's rows count for the survivor; a company
with nothing loads without a crash."""
import json
import unittest
from datetime import date, timedelta

from outreach.crawl.stale import load_stale
from outreach.ranking.common import canonical_ids, doubtful_board_ids
from outreach.ranking.config import default_ranker_config
from outreach.ranking.confidence import LEVELS, Confidence
from outreach.ranking.facts import SHARED_DOMAIN_FLAG, RankFacts, load_facts, recent_fiscal_years
from outreach.ranking.pay import load_wages
from outreach.ranking.roles import load_roles
from outreach.ranking.types import CompanyFacts
from tests import ranking_store as rs
from tests.storetest import fresh_store, seed_company

TODAY = date(2026, 10, 21)
NG, INTERN, SENIOR = "Software Engineer, New Grad", "Software Engineering Intern", "Senior Software Engineer"
SIGNALS = ("live_role", "posting_sponsors", "everify", "cap_exempt", "pay_level", "lca_junior", "small",
           "reachable_person", "funding", "staffing_risk", "large_later_stage")
NOW = "2026-10-01T08:00:00Z"


def ago(days: int) -> str:
    return (TODAY - timedelta(days=days)).isoformat()


def lca(fy: int, jr: int = 0, level1: int = 0, software: int = 0, median: int | None = None) -> str:
    row = {"fy": fy, "software": software, "jr": jr, "level1": level1, "newemp_jr": 0}
    if median is not None:
        row["median_jr_wage"] = median
    return json.dumps(row)


def funding(filed_on: str) -> str:
    return json.dumps({"filed_on": filed_on, "amount_usd": None, "kind": "form_d"})


class Store(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.co = seed_company(self.conn, "Acme")
        self.board = rs.board(self.conn, self.co)

    def load(self, today: date = TODAY) -> dict[int, RankFacts]:
        canon, doubtful = canonical_ids(self.conn), doubtful_board_ids(self.conn)
        stale = load_stale(self.conn, today, max_age_months=12)
        roles = load_roles(self.conn, canon, doubtful, today, stale)
        facts = load_facts(self.conn, canon, doubtful, roles, today, default_ranker_config(), load_wages())
        return {f.company_id: f for f in facts}

    def one(self, company_id: int | None = None) -> RankFacts:
        return self.load()[company_id or self.co]

    def add(self, kind="ng", *, company_id=None, board_id="default", **fields) -> int:
        """A posting decided directly. kind: ng (new grad), intern or senior; fields go to the posting or decision."""
        title = {"ng": NG, "intern": INTERN, "senior": SENIOR}[kind]
        posting_fields = {k: fields.pop(k) for k in ("posted_on", "first_seen_at", "last_seen_at", "closed",
                                                     "location", "pay") if k in fields}
        posting_fields.setdefault("posted_on", ago(5))
        pid = rs.posting(self.conn, company_id or self.co, title,
                         board_id=self.board if board_id == "default" else board_id, **posting_fields)
        lane = {"ng": dict(), "intern": dict(lane="intern", level_tier="not_stated"),
                "senior": dict(level_tier="senior")}[kind]
        rs.decision(self.conn, pid, **{**lane, **fields})
        return pid

    def ev(self, fact: str, value: str, *, company_id=None, confidence="estimate", locked=0, observed_on=None,
           source_kind=None, num_low=None, num_high=None, recorded_at=NOW, source_ref="https://example.com/a") -> int:
        kind = source_kind or ("hand" if locked else "linkedin")
        return self.conn.execute(
            "INSERT INTO evidence(company_id, fact, value, num_low, num_high, confidence, locked, source_kind, "
            "source_ref, observed_on, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (company_id or self.co, fact, value, num_low, num_high, confidence, locked, kind, source_ref,
             observed_on, recorded_at)).lastrowid

    def size(self, low, high=None, *, fact="size", **kw) -> int:
        return self.ev(fact, f"{low}", num_low=low, num_high=high if high is not None else low, **kw)

    def hq(self, value: str, *, company_id=None, confidence="confirmed") -> int:
        return self.ev("hq", value, company_id=company_id, confidence=confidence)

    def person(self, role_kind="engineer", name="Jane Doe", *, company_id=None) -> int:
        return self.conn.execute(
            "INSERT INTO person(company_id, name, role_kind, email, channel, added_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'email', 'devanshu', ?, ?)",
            (company_id or self.co, name, role_kind, f"{name.split()[0].lower()}{role_kind}@example.com", NOW,
             NOW)).lastrowid

    def person_source(self, person_id: int, kind="team") -> None:
        self.conn.execute("INSERT INTO person_source(person_id, kind, url, recorded_at) VALUES (?, ?, ?, ?)",
                          (person_id, kind, f"https://example.com/{kind}/{person_id}", NOW))

    def hn_post(self, days_old: int, *, asks_email=1, company_id=None) -> None:
        self.conn.execute("INSERT INTO source(source_name, version) VALUES ('hn', 'v1') ON CONFLICT DO NOTHING")
        when = (TODAY - timedelta(days=days_old)).isoformat() + "T09:00:00Z"
        self.conn.execute("INSERT INTO company_post(company_id, source_name, url, posted_at, asks_email) "
                          "VALUES (?, 'hn', ?, ?, ?)",
                          (company_id or self.co, f"https://news.ycombinator.com/item?id={days_old}", when, asks_email))

    def tier(self, tier: int, promoted=0, reason="staffing firm", *, company_id=None) -> None:
        self.conn.execute("INSERT INTO work_tier(company_id, tier, promoted, reason, set_at) VALUES (?, ?, ?, ?, ?)",
                          (company_id or self.co, tier, promoted, reason, NOW))

    def watch(self, company_id=None) -> None:
        self.conn.execute("INSERT INTO watched_page(company_id, url) VALUES (?, 'https://acme.example/careers')",
                          (company_id or self.co,))

    def company(self, name: str) -> int:
        return seed_company(self.conn, name)


class FactsTests(Store):
    def test_level_role_company_has_best_posting_age_and_stance(self):
        pid = self.add("ng", sponsorship="yes", posted_on=ago(5))
        f = self.one()
        self.assertIsInstance(f, RankFacts)
        self.assertIsInstance(f, CompanyFacts)
        self.assertEqual((f.company_id, f.name), (self.co, "Acme"))
        self.assertEqual(f.lanes, ("level_role",))
        self.assertEqual((f.best_posting_id, f.best_role_age_days), (pid, 5))
        self.assertEqual((f.best_title, f.best_level_tier, f.best_sponsorship), (NG, "new_grad", "yes"))

    def test_lanes_are_composed_in_design_order(self):
        self.add("ng")
        senior_only = self.company("Senior only")
        self.add("senior", company_id=senior_only, board_id=None)
        funded = self.company("Funded")
        self.add("senior", company_id=funded, board_id=None)
        self.ev("funding", funding("2026-05-10"), company_id=funded)
        intern = self.company("Intern")
        self.add("intern", company_id=intern, board_id=None)
        self.ev("lca", lca(2026, jr=4), company_id=intern)
        got = self.load()
        self.assertEqual(got[self.co].lanes, ("level_role",))
        self.assertEqual(got[senior_only].lanes, ("any_us_role",))
        self.assertEqual(got[funded].lanes, ("reason_no_role", "any_us_role"))
        self.assertEqual(got[intern].lanes, ("intern_junior",))  # a role lane blocks reason_no_role

    def test_a_company_with_a_level_role_and_an_intern_posting_holds_both_role_lanes(self):
        self.add("ng")
        self.add("intern")
        self.add("senior")
        self.assertEqual(self.one().lanes, ("level_role", "intern_junior"))

    def test_reason_no_role_sources(self):
        window = default_ranker_config().ranker["funding_window_months"]
        recent, old, jr, watched = (self.company(n) for n in ("Recent", "Old", "Juniors", "Watched"))
        self.ev("funding", funding((TODAY - timedelta(days=window * 30)).isoformat()), company_id=recent)
        self.ev("funding", funding((TODAY - timedelta(days=(window + 1) * 30)).isoformat()), company_id=old)
        self.ev("lca", lca(2026, jr=1), company_id=jr)
        self.watch(watched)
        got = self.load()
        self.assertEqual(got[recent].funded_months_ago, window)
        self.assertEqual(got[recent].lanes, ("reason_no_role",))
        self.assertEqual(got[old].funded_months_ago, window + 1)
        self.assertEqual(got[old].lanes, ())
        self.assertEqual(got[jr].lanes, ("reason_no_role",))
        self.assertEqual(got[watched].lanes, ("reason_no_role",))

    def test_any_us_role_is_dropped_for_a_large_later_stage_company(self):
        self.add("senior")
        self.ev("stage", "series_d")
        self.assertEqual(self.one().lanes, ())
        big = self.company("Guessed big")
        self.add("senior", company_id=big, board_id=None)
        self.size(400, confidence="guess", company_id=big)
        self.assertEqual(self.load()[big].lanes, ())

    def test_company_with_no_postings_and_no_evidence_still_gets_facts(self):
        f = self.one()
        self.assertEqual(f.lanes, ())
        for name in ("best_posting_id", "best_role_age_days", "everify", "cap_exempt", "staffing_risk", "pay_level",
                     "size_low", "size_high", "stage", "funded_months_ago", "best_level_tier", "best_sponsorship",
                     "size_confidence", "work_tier"):
            self.assertIsNone(getattr(f, name), name)
        self.assertEqual((f.lca_jr_recent, f.lca_level1_recent, f.reachable_person), (0, 0, False))
        self.assertEqual((f.best_title, f.pay_basis, f.pay_note, f.reach_via, f.reach_note), ("", "", "", "", ""))
        self.assertEqual((f.weak_flags, f.stale_postings, f.ignored_postings, f.work_promoted), ((), (), 0, False))

    def test_unknown_size_stays_none(self):
        f = self.one()
        self.assertEqual((f.size_low, f.size_high, f.size_confidence, f.weak_flags), (None, None, None, ()))
        self.ev("stage", "series_d")
        f = self.one()
        self.assertEqual(f.stage, "series_d")
        self.assertEqual(f.weak_flags, ("later stage, size unknown",))
        self.assertIsNone(f.size_low)

    def test_known_size_is_carried_with_its_confidence(self):
        self.size(120, 180, confidence="confirmed")
        f = self.one()
        self.assertEqual((f.size_low, f.size_high, f.size_confidence), (120, 180, "confirmed"))
        self.assertEqual(f.weak_flags, ())

    def test_headcount_is_used_when_there_is_no_size(self):
        self.size(90, fact="headcount", confidence="estimate")
        f = self.one()
        self.assertEqual((f.size_low, f.size_high, f.size_confidence), (90, 90, "estimate"))
        self.size(40, 60, confidence="guess")  # a size row beats headcount whatever its confidence
        f = self.one()
        self.assertEqual((f.size_low, f.size_high, f.size_confidence), (40, 60, "guess"))

    def test_lca_recent_uses_the_two_newest_fiscal_years_in_the_store(self):
        a, b = self.co, self.company("Only old")
        self.ev("lca", lca(2024, jr=50, level1=50), company_id=a)
        self.ev("lca", lca(2025, jr=2, level1=1), company_id=a)
        self.ev("lca", lca(2026, jr=4, level1=2), company_id=a, recorded_at="2026-09-01T08:00:00Z")
        # the same fiscal year recorded again later: the newest row counts, once
        self.ev("lca", lca(2026, jr=5, level1=3), company_id=a, recorded_at="2026-10-01T08:00:00Z")
        self.ev("lca", lca(2024, jr=9, level1=9), company_id=b)
        self.assertEqual(recent_fiscal_years(self.conn, 2), (2026, 2025))
        self.assertEqual(recent_fiscal_years(self.conn, 1), (2026,))
        got = self.load()
        self.assertEqual((got[a].lca_jr_recent, got[a].lca_level1_recent), (7, 4))
        self.assertEqual((got[b].lca_jr_recent, got[b].lca_level1_recent), (0, 0))

    def test_recent_fiscal_years_with_no_lca_evidence_is_empty(self):
        self.assertEqual(recent_fiscal_years(self.conn, 2), ())

    def test_funding_months_and_future_date(self):
        self.ev("funding", funding("2026-05-10"))
        self.assertEqual(self.one().funded_months_ago, 5)
        later = self.company("Later")
        self.ev("funding", funding("2026-12-01"), company_id=later)
        self.assertEqual(self.load()[later].funded_months_ago, 0)
        two = self.company("Two rounds")
        self.ev("funding", funding("2024-01-01"), company_id=two)
        self.ev("funding", funding("2026-07-20"), company_id=two)
        self.assertEqual(self.load()[two].funded_months_ago, 3)  # the newest filing

    def test_pay_level_posted_beats_lca_and_lca_beats_nothing(self):
        self.add("ng", location="Boston, MA", pay="$130,000 - $150,000")
        self.ev("lca", lca(2026, jr=15, level1=2))
        f = self.one()
        self.assertEqual((f.pay_level, f.pay_basis), (2, "posted"))
        self.assertIn("level 2", f.pay_note)
        lca_only = self.company("LCA only")
        self.ev("lca", lca(2026, jr=15, level1=2), company_id=lca_only)
        f = self.load()[lca_only]
        self.assertEqual((f.pay_level, f.pay_basis), (2, "lca"))
        self.assertIn("2 of 15", f.pay_note)
        mostly_one = self.company("Level I shop")
        self.ev("lca", lca(2026, jr=10, level1=8), company_id=mostly_one)
        self.assertEqual(self.load()[mostly_one].pay_level, 1)
        neither = self.company("Neither")
        self.add("ng", company_id=neither, board_id=None, location="Boston, MA")
        f = self.load()[neither]
        self.assertEqual((f.pay_level, f.pay_basis, f.pay_note), (None, "", ""))
        junk = self.company("Junk pay")
        self.add("ng", company_id=junk, board_id=None, location="Boston, MA", pay="Competitive")
        self.assertIsNone(self.load()[junk].pay_level)

    def test_pay_comes_from_the_first_visible_posting_that_has_a_pay_level(self):
        self.add("ng", location="Boston, MA", pay="Competitive", posted_on=ago(1))
        self.add("ng", location="Remote", pay="$130,000 - $150,000", posted_on=ago(2))
        self.add("ng", location="Boston, MA", pay="$140,000 - $150,000", posted_on=ago(3))
        f = self.one()
        self.assertEqual((f.pay_level, f.pay_basis), (2, "posted"))

    def test_the_lca_median_wage_gives_a_level_everywhere_it_is_the_same(self):
        self.ev("lca", lca(2026, jr=12, level1=12, median=300_000))  # level I share says 1; the wage says 4
        f = self.one()
        self.assertEqual((f.pay_level, f.pay_basis), (4, "lca"))
        self.assertEqual(f.pay_note, "LCA median junior wage $300,000 in every listed area: level 4")
        self.assertEqual(f.grade("pay_level").level, "medium")  # 12 filings: medium, never high

    def test_the_lca_median_wage_uses_the_area_of_the_hq_state(self):
        self.ev("lca", lca(2026, jr=4, level1=4, median=140_000))
        self.hq("Austin, TX")
        f = self.one()
        self.assertEqual((f.pay_level, f.pay_basis), (2, "lca"))
        self.assertEqual(f.pay_note, "LCA median junior wage $140,000 in Austin: level 2")
        self.assertEqual(f.grade("pay_level").level, "low")  # 4 filings
        ca = self.company("California")
        self.ev("lca", lca(2026, jr=4, median=170_000), company_id=ca)  # level 1 in San Jose, 2 in San Francisco
        self.hq("CA", company_id=ca)  # both areas hold CA: the lower level
        self.assertEqual(self.load()[ca].pay_level, 1)

    def test_an_ambiguous_median_wage_with_no_usable_hq_falls_back_to_the_level_one_share(self):
        self.ev("lca", lca(2026, jr=10, level1=8, median=140_000))  # level 1 in San Jose, 2 in Boston
        f = self.one()
        self.assertEqual((f.pay_level, f.pay_basis), (1, "lca"))
        self.assertIn("8 of 10", f.pay_note)
        guess = self.company("Guessed hq")
        self.ev("lca", lca(2026, jr=10, level1=8, median=140_000), company_id=guess)
        self.hq("TX", company_id=guess, confidence="guess")  # a guess is not a usable hq
        self.assertIn("8 of 10", self.load()[guess].pay_note)
        two = self.company("Two states")
        self.ev("lca", lca(2026, jr=10, level1=8, median=140_000), company_id=two)
        self.hq("TX", company_id=two)
        self.hq("CA", company_id=two, confidence="estimate")
        self.assertIn("8 of 10", self.load()[two].pay_note)

    def test_a_median_wage_in_an_unlisted_hq_state_is_not_read(self):
        self.ev("lca", lca(2026, jr=10, level1=8, median=300_000))
        self.hq("Miami, FL")
        f = self.one()
        self.assertEqual((f.pay_level, f.pay_basis), (1, "lca"))
        self.assertIn("8 of 10", f.pay_note)

    def test_an_lca_row_without_the_median_behaves_as_before(self):
        self.ev("lca", lca(2026, jr=15, level1=2))
        self.hq("TX")
        f = self.one()
        self.assertEqual((f.pay_level, f.pay_basis), (2, "lca"))
        self.assertIn("2 of 15", f.pay_note)

    def test_the_median_comes_from_the_newest_counted_row_that_has_one(self):
        self.ev("lca", lca(2025, jr=3, median=300_000))
        self.ev("lca", lca(2026, jr=3))  # newest year has no median
        self.ev("lca", lca(2024, jr=3, median=100_000))  # not a recent year
        f = self.one()
        self.assertEqual((f.pay_level, f.pay_basis), (4, "lca"))
        self.assertIn("$300,000", f.pay_note)
        both = self.company("Two medians")
        self.ev("lca", lca(2025, jr=3, median=300_000), company_id=both)
        self.ev("lca", lca(2026, jr=3, median=100_000), company_id=both)
        self.assertIn("$100,000", self.load()[both].pay_note)  # the newer year wins

    def test_a_posted_range_beats_the_lca_median(self):
        self.add("ng", location="Boston, MA", pay="$140,000 - $150,000")
        self.ev("lca", lca(2026, jr=12, median=300_000))
        f = self.one()
        self.assertEqual((f.pay_level, f.pay_basis), (2, "posted"))

    def test_pay_is_read_only_from_the_best_postings_lane(self):
        self.add("ng", location="Boston, MA", posted_on=ago(1))  # the best posting has no pay
        self.add("intern", location="Boston, MA", pay="$140,000 - $150,000", posted_on=ago(2))
        f = self.one()
        self.assertEqual(f.lanes, ("level_role", "intern_junior"))
        self.assertEqual((f.pay_level, f.pay_basis), (None, ""))
        senior = self.company("Same lane")
        self.add("senior", company_id=senior, board_id=None, location="Boston, MA", posted_on=ago(1))
        self.add("senior", company_id=senior, board_id=None, location="Boston, MA", pay="$140,000 - $150,000",
                 posted_on=ago(2))
        self.assertEqual(self.load()[senior].pay_level, 2)  # another posting of the same lane still counts

    def test_an_undated_posting_is_marked_undated(self):
        self.add("ng", posted_on=None, first_seen_at="2026-10-01T08:00:00Z")
        f = self.one()
        self.assertEqual((f.best_role_age_days, f.best_dated), (20, False))
        dated = self.company("Dated")
        self.add("ng", company_id=dated, board_id=None, posted_on=ago(4))
        self.assertEqual((self.load()[dated].best_role_age_days, self.load()[dated].best_dated), (4, True))
        self.assertTrue(self.company("None") and self.load()[self.co].best_dated is False)

    def test_an_hn_post_at_the_window_edge_counts_and_one_day_past_it_does_not(self):
        days = default_ranker_config().ranker["hn_post_days"]
        edge, past = self.company("Edge"), self.company("Past")
        self.hn_post(days, company_id=edge)
        self.hn_post(days + 1, company_id=past)
        got = self.load()
        self.assertEqual(got[edge].reach_via, "hn_post")
        self.assertEqual(got[past].reach_via, "")

    def test_an_hn_post_dated_after_today_is_not_counted(self):
        future = self.company("Future")
        self.hn_post(-3, company_id=future)
        self.assertEqual(self.load()[future].reach_via, "")

    def test_the_worst_work_tier_among_merged_duplicates_counts(self):
        dup = self.company("Acme Staffing")
        self.tier(1, promoted=1, reason="builds product")
        self.tier(3, promoted=0, reason="places contractors", company_id=dup)
        rs.merge(self.conn, dup, self.co)
        f = self.one()
        self.assertEqual((f.work_tier, f.work_promoted), (3, False))
        self.assertIn("places contractors", f.grade("staffing_risk").basis)

    def test_a_tied_work_tier_is_not_promoted_unless_every_row_is(self):
        dup = self.company("Acme Two")
        self.tier(3, promoted=1, reason="promoted by hand")
        self.tier(3, promoted=0, reason="places contractors", company_id=dup)
        rs.merge(self.conn, dup, self.co)
        self.assertEqual((self.one().work_tier, self.one().work_promoted), (3, False))

    def test_a_fiscal_year_recorded_on_two_merged_companies_counts_once_with_the_newest_row(self):
        dup = self.company("Acme Inc")
        self.ev("lca", lca(2026, jr=9, level1=9), recorded_at="2026-08-01T08:00:00Z")
        self.ev("lca", lca(2026, jr=3, level1=1), company_id=dup, recorded_at="2026-09-15T08:00:00Z")
        rs.merge(self.conn, dup, self.co)
        f = self.one()
        self.assertEqual((f.lca_jr_recent, f.lca_level1_recent), (3, 1))  # the newest row only, never the sum

    def test_reach(self):
        eng, rec, banned, hn, old_hn = (self.company(n) for n in ("Eng", "Recruiter", "Banned", "HN", "Old HN"))
        self.person("engineer", company_id=eng)
        self.person("recruiter", company_id=rec)
        pid = self.person("engineer", "Sam Lee", company_id=banned)
        self.conn.execute("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'stop_reply', "
                          "'devanshu', ?)", (pid, NOW))
        self.hn_post(20, company_id=hn)
        self.hn_post(120, company_id=old_hn)
        got = self.load()
        self.assertEqual((got[eng].reachable_person, got[eng].reach_via), (True, "person"))
        self.assertIn("Jane Doe", got[eng].reach_note)
        self.assertEqual((got[rec].reachable_person, got[rec].reach_via, got[rec].reach_note), (False, "", ""))
        self.assertEqual((got[banned].reachable_person, got[banned].reach_via), (False, ""))
        self.assertEqual((got[hn].reachable_person, got[hn].reach_via), (True, "hn_post"))
        self.assertIn("20 days", got[hn].reach_note)
        self.assertEqual((got[old_hn].reachable_person, got[old_hn].reach_via), (False, ""))

    def test_reach_ignores_an_hn_post_that_does_not_ask_for_email_and_a_lifted_person_no_go(self):
        plain, lifted = self.company("Plain"), self.company("Lifted")
        self.hn_post(5, asks_email=0, company_id=plain)
        pid = self.person("manager", company_id=lifted)
        self.conn.execute("INSERT INTO no_go(person_id, reason, source, created_at, lifted_at, lift_reason) "
                          "VALUES (?, 'stop_reply', 'devanshu', ?, ?, 'he asked')", (pid, NOW, "2026-10-02T08:00:00Z"))
        got = self.load()
        self.assertFalse(got[plain].reachable_person)
        self.assertEqual((got[lifted].reachable_person, got[lifted].reach_via), (True, "person"))

    def test_work_tier_and_promotion_are_carried(self):
        other = self.company("Other")
        self.tier(3, promoted=0)
        self.tier(1, promoted=1, reason="builds product", company_id=other)
        got = self.load()
        self.assertEqual((got[self.co].work_tier, got[self.co].work_promoted), (3, False))
        self.assertEqual((got[other].work_tier, got[other].work_promoted), (1, True))
        nothing = self.company("Nothing")
        self.assertEqual((self.load()[nothing].work_tier, self.load()[nothing].work_promoted), (None, False))

    def test_weak_flags_many_postings_lca_software_and_shared_domain(self):
        cfg = default_ranker_config().ranker
        many, soft, shared, quiet = (self.company(n) for n in ("Many", "Software", "Shared", "Quiet"))
        many_board = rs.board(self.conn, many)
        for _ in range(cfg["many_postings"] + 1):
            rs.posting(self.conn, many, SENIOR, board_id=many_board)
        self.ev("lca", lca(2025, software=cfg["lca_software_many"]), company_id=soft)
        self.ev("domain", "acme.com", company_id=shared)
        self.conn.execute("INSERT INTO shared_key(kind, value, reason, recorded_at) VALUES ('domain', 'acme.com', "
                          "'two employers use it', ?)", (NOW,))
        self.ev("lca", lca(2025, software=cfg["lca_software_many"] - 1), company_id=quiet)
        self.ev("domain", "quiet.com", company_id=quiet)
        got = self.load()
        self.assertEqual(got[many].weak_flags, (f"{cfg['many_postings'] + 1} open roles",))
        self.assertEqual(got[soft].weak_flags, (f"{cfg['lca_software_many']} software H-1B filings in one fiscal year",))
        self.assertEqual(got[shared].weak_flags, (SHARED_DOMAIN_FLAG,))
        self.assertEqual(SHARED_DOMAIN_FLAG, "domain shared with other employers")
        self.assertEqual(got[quiet].weak_flags, ())

    def test_exactly_the_limit_is_not_many_postings(self):
        limit = default_ranker_config().ranker["many_postings"]
        b = rs.board(self.conn, self.co)
        for _ in range(limit):
            rs.posting(self.conn, self.co, SENIOR, board_id=b)
        self.assertEqual(self.one().weak_flags, ())

    def test_postings_on_doubtful_boards_do_not_count_toward_the_many_postings_flag(self):
        limit = default_ranker_config().ranker["many_postings"]
        b = rs.board(self.conn, self.co)
        rs.flag_board(self.conn, self.co, b)
        for _ in range(limit + 5):
            rs.posting(self.conn, self.co, SENIOR, board_id=b)
        f = self.one()
        self.assertEqual(f.weak_flags, ())
        self.assertEqual(f.ignored_postings, limit + 5)

    def test_merged_away_company_is_not_listed_and_its_evidence_counts_for_the_survivor(self):
        dup = self.company("Acme Inc")
        pid = self.add("ng", company_id=dup)
        self.ev("everify", "enrolled", company_id=dup, confidence="confirmed", observed_on="2026-09-01")
        self.ev("lca", lca(2026, jr=3, level1=1), company_id=dup)
        self.size(60, 80, company_id=dup)
        self.person("engineer", "Dee Dup", company_id=dup)
        rs.merge(self.conn, dup, self.co)
        got = self.load()
        self.assertNotIn(dup, got)
        f = got[self.co]
        self.assertEqual((f.best_posting_id, f.everify, f.lca_jr_recent, f.size_low), (pid, "enrolled", 3, 60))
        self.assertEqual((f.reachable_person, f.lanes), (True, ("level_role",)))
        self.assertEqual(f.grade("everify").level, "high")

    def test_closed_and_removed_companies_are_not_listed(self):
        closed, removed, working, backlog = (self.company(n) for n in ("Closed", "Removed", "Working", "Backlog"))
        self.conn.execute("UPDATE company SET status = 'closed', close_reason = 'not_now' WHERE company_id = ?",
                          (closed,))
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'junk' WHERE company_id = ?",
                          (removed,))
        self.conn.execute("UPDATE company SET status = 'working' WHERE company_id = ?", (working,))
        self.conn.execute("UPDATE company SET status = 'backlog' WHERE company_id = ?", (backlog,))
        self.assertEqual(sorted(self.load()), [self.co, working, backlog])

    def test_facts_come_in_company_id_order(self):
        for n in "BCD":
            self.company(n)
        canon, doubtful = canonical_ids(self.conn), doubtful_board_ids(self.conn)
        facts = load_facts(self.conn, canon, doubtful, {}, TODAY, default_ranker_config(), load_wages())
        self.assertEqual([f.company_id for f in facts], sorted(f.company_id for f in facts))
        self.assertEqual(len(facts), 4)

    def test_the_loader_reads_in_bulk_not_once_per_company(self):
        def selects(n_companies: int) -> int:
            conn = fresh_store()
            self.addCleanup(conn.close)
            for i in range(n_companies):
                c = seed_company(conn, f"Co {i}")
                b = rs.board(conn, c)
                pid = rs.posting(conn, c, NG, board_id=b, posted_on=ago(3))
                rs.decision(conn, pid)
                conn.execute("INSERT INTO evidence(company_id, fact, value, confidence, source_kind, recorded_at) "
                             "VALUES (?, 'stage', 'seed', 'estimate', 'linkedin', ?)", (c, NOW))
            canon, doubtful = canonical_ids(conn), doubtful_board_ids(conn)
            roles = load_roles(conn, canon, doubtful, TODAY, {})
            seen: list[str] = []
            conn.set_trace_callback(seen.append)
            facts = load_facts(conn, canon, doubtful, roles, TODAY, default_ranker_config(), load_wages())
            conn.set_trace_callback(None)
            self.assertEqual(len(facts), n_companies)
            return sum(1 for s in seen if s.lstrip().upper().startswith(("SELECT", "WITH")))
        self.assertEqual(selects(2), selects(12))


class GradeTests(Store):
    def test_grades_come_from_the_evidence_rows(self):
        self.ev("everify", "enrolled", confidence="confirmed", observed_on="2026-09-01")
        self.ev("cap_exempt", "likely", confidence="estimate")
        self.size(80, 120, confidence="guess")
        guessed_locked = self.company("Locked")
        self.size(80, 120, confidence="guess", locked=1, company_id=guessed_locked)
        f = self.one()
        self.assertEqual(f.grade("everify").level, "high")
        self.assertEqual(f.grade("cap_exempt").level, "medium")
        self.assertEqual(f.grade("small").level, "low")
        self.assertEqual(self.load()[guessed_locked].grade("small").level, "high")
        for name in ("pay_level", "funding", "lca_junior"):
            self.assertEqual(f.grade(name).level, "low")

    def test_old_evidence_drops_one_level_and_says_how_old(self):
        self.ev("everify", "enrolled", confidence="confirmed", observed_on="2024-01-02")
        g = self.one().grade("everify")
        self.assertEqual(g.level, "medium")
        self.assertTrue(g.basis.endswith("observed 2024-01-02, over 365 days old"), g.basis)

    def test_no_row_is_low_and_says_so(self):
        self.assertEqual(self.one().grade("everify"), Confidence("low", "no everify evidence"))

    def test_large_later_stage_grade_follows_the_evidence_that_triggered_it(self):
        self.ev("stage", "series_d", confidence="confirmed")
        self.size(400, confidence="guess")
        self.assertEqual(self.one().grade("large_later_stage").level, "high")
        guess_only = self.company("Guess only")
        self.size(400, confidence="guess", company_id=guess_only)
        self.assertEqual(self.load()[guess_only].grade("large_later_stage").level, "low")

    def test_large_later_stage_with_nothing_triggering_it_still_has_a_grade(self):
        f = self.one()
        self.assertEqual(f.grade("large_later_stage").level, "low")
        self.ev("stage", "seed", confidence="confirmed")
        self.assertIn(self.one().grade("large_later_stage").level, LEVELS)

    def test_lca_junior_grade_is_the_weakest_row_it_sums(self):
        self.ev("lca", lca(2025, jr=2), confidence="confirmed", source_kind="lca", source_ref="https://dol/a",
                observed_on="2026-06-01")
        self.ev("lca", lca(2026, jr=2), confidence="guess", source_kind="lca", source_ref="https://dol/b",
                observed_on="2026-06-01")
        self.assertEqual(self.one().grade("lca_junior").level, "low")
        solid = self.company("Solid")
        for fy in (2025, 2026):
            self.ev("lca", lca(fy, jr=2), confidence="confirmed", source_kind="lca", company_id=solid,
                    observed_on="2026-06-01")
        self.assertEqual(self.load()[solid].grade("lca_junior").level, "high")

    def test_lca_outside_the_recent_years_is_not_graded_as_evidence(self):
        self.ev("lca", lca(2024, jr=9), confidence="confirmed")
        other = self.company("Other")  # the two newest fiscal years in the store are 2025 and 2026, not 2024
        self.ev("lca", lca(2026, jr=1), company_id=other)
        self.ev("lca", lca(2025, jr=1), company_id=other)
        f = self.load()[self.co]
        self.assertEqual(f.lca_jr_recent, 0)
        self.assertEqual(f.grade("lca_junior").level, "low")

    def test_staffing_risk_from_work_tier_alone_is_medium(self):
        self.tier(3, reason="places contractors at clients")
        g = self.one().grade("staffing_risk")
        self.assertEqual(g.level, "medium")
        self.assertIn("places contractors at clients", g.basis)
        self.assertIn("tier 3", g.basis)

    def test_staffing_risk_from_a_row_is_graded_like_any_evidence(self):
        self.ev("staffing_risk", "high", confidence="confirmed", observed_on="2026-09-01")
        self.assertEqual(self.one().grade("staffing_risk").level, "high")
        promoted = self.company("Promoted")
        self.tier(3, promoted=1, company_id=promoted)
        self.assertEqual(self.load()[promoted].grade("staffing_risk").level, "low")

    def test_staffing_risk_tier_and_row_pick_the_better_supported_grade(self):
        sure, weak, other_value = (self.company(n) for n in ("Sure", "Weak", "Other"))
        for cid in (sure, weak, other_value):
            self.tier(3, company_id=cid, reason="places contractors")
        self.ev("staffing_risk", "high", confidence="confirmed", observed_on="2026-09-01", company_id=sure)
        self.ev("staffing_risk", "high", confidence="guess", company_id=weak)
        # the row says low, so the value comes from the tier and the grade is the tier's, not the row's
        self.ev("staffing_risk", "low", confidence="confirmed", observed_on="2026-09-01", company_id=other_value)
        got = self.load()
        self.assertEqual(got[sure].grade("staffing_risk").level, "high")
        self.assertEqual(got[weak].grade("staffing_risk").level, "medium")
        self.assertIn("tier 3", got[weak].grade("staffing_risk").basis)
        self.assertEqual(got[other_value].grade("staffing_risk").level, "medium")
        self.assertIn("tier 3", got[other_value].grade("staffing_risk").basis)

    def test_funding_is_graded_from_the_filing_it_reads(self):
        self.ev("funding", funding("2026-05-10"), confidence="confirmed", source_kind="formd",
                observed_on="2026-05-10")
        self.assertEqual(self.one().grade("funding").level, "high")

    def test_live_role_grade_is_the_roles_grade_and_a_company_with_no_postings_uses_its_board_state(self):
        self.add("ng", posted_on=ago(3))
        f = self.one()
        self.assertEqual(f.grade("live_role").level, "high")
        quiet = self.company("Quiet")
        rs.board(self.conn, quiet, last_read="ok")
        bare = self.company("Bare")
        got = self.load()
        self.assertEqual(got[quiet].grade("live_role"), Confidence("high", "board read ok, no matching posting listed"))
        self.assertEqual(got[bare].grade("live_role"),
                         Confidence("low", "no board found yet: roles are unknown, not absent"))

    def test_a_company_with_only_a_doubtful_board_and_no_postings_is_unconfirmed_low(self):
        guess = self.company("Guessed")
        rs.board(self.conn, guess, found_by="stapply_name", last_read=None)
        self.assertEqual(self.load()[guess].grade("live_role").level, "low")

    def test_posting_sponsors_grade_is_medium_for_a_rules_only_decision_and_high_for_a_final_one(self):
        pid = self.add("ng", sponsorship="yes")
        self.assertEqual(self.one().grade("posting_sponsors").level, "medium")
        rs.make_final(self.conn, pid)
        self.assertEqual(self.one().grade("posting_sponsors").level, "high")
        none = self.company("No posting")
        self.assertEqual(self.load()[none].grade("posting_sponsors").level, "low")

    def test_pay_grade_cases(self):
        both = self.company("Both ends")
        self.add("ng", company_id=both, board_id=None, location="Boston, MA", pay="$140,000 - $150,000")
        straddle = self.company("Straddles")
        self.add("ng", company_id=straddle, board_id=None, location="Boston, MA", pay="$130,000 - $160,000")
        twelve, five, none = self.company("Twelve"), self.company("Five"), self.company("None")
        self.ev("lca", lca(2026, jr=12, level1=3), company_id=twelve)
        self.ev("lca", lca(2026, jr=5, level1=3), company_id=five)
        got = self.load()
        self.assertEqual(got[both].pay_level, 2)
        self.assertEqual(got[both].grade("pay_level").level, "high")
        self.assertEqual((got[straddle].pay_level, got[straddle].grade("pay_level").level), (2, "medium"))
        self.assertEqual((got[twelve].pay_basis, got[twelve].grade("pay_level").level), ("lca", "medium"))
        self.assertEqual((got[five].pay_basis, got[five].grade("pay_level").level), ("lca", "low"))
        self.assertEqual(got[none].grade("pay_level").level, "low")

    def test_pay_grade_straddle_follows_the_minimum_and_the_midpoint_not_the_chosen_basis(self):
        cfg = default_ranker_config()
        self.assertEqual(cfg.section("pay_level")["basis"], "midpoint")
        self.add("ng", location="Boston, MA", pay="$130,000 - $160,000")
        self.assertEqual(self.one().grade("pay_level").level, "medium")

    def test_reach_grade(self):
        team, plain, hn, none = (self.company(n) for n in ("Team", "Plain", "HN", "None"))
        self.person_source(self.person("engineer", company_id=team), "team")
        self.person("engineer", "Sam Lee", company_id=plain)
        self.hn_post(10, company_id=hn)
        got = self.load()
        self.assertEqual(got[team].grade("reachable_person").level, "high")
        self.assertEqual(got[plain].grade("reachable_person").level, "medium")
        self.assertEqual(got[hn].grade("reachable_person").level, "medium")
        self.assertEqual(got[none].grade("reachable_person"), Confidence("low", "no person recorded yet"))

    def test_reach_grade_uses_the_person_with_a_team_source(self):
        a = self.person("engineer", "Ann Bare")
        b = self.person("alum", "Bo Source")
        self.person_source(b, "team")
        self.person_source(a, "profile")  # a profile source is not a team source
        f = self.one()
        self.assertEqual(f.grade("reachable_person").level, "high")
        self.assertIn("Bo Source", f.reach_note)

    def test_every_signal_name_has_a_grade_or_the_low_default(self):
        f = self.one()
        for name in SIGNALS:
            g = f.grade(name)
            self.assertIsInstance(g, Confidence)
            self.assertTrue(g.basis.strip())
        self.assertEqual(set(SIGNALS) - set(f.grades), set(), "the loader grades all eleven signals")
        self.assertEqual(RankFacts.__dataclass_fields__["grades"].default_factory(), {})
        self.assertEqual(f.grade("not_a_signal"), Confidence("low", "no data for not a signal"))

    def test_a_grade_that_is_not_stored_is_the_low_default(self):
        bare = CompanyFacts(1, (), None, None, None, None, None, 0, 0, None, None, None, None, None, False)
        rf = RankFacts(**{k: getattr(bare, k) for k in CompanyFacts.__dataclass_fields__})
        self.assertEqual(rf.grade("live_role"), Confidence("low", "no data for live role"))


class StaleFactsTests(Store):
    def test_stale_postings_reach_the_facts_and_give_no_lane_pay_or_sponsor_credit(self):
        pid = self.add("ng", posted_on="2016-03-02", location="Boston, MA", pay="$130,000 - $150,000",
                       sponsorship="yes")
        f = self.one()
        self.assertEqual(f.lanes, ())
        self.assertIsNone(f.best_posting_id)
        self.assertIsNone(f.best_role_age_days)
        self.assertIsNone(f.pay_level)
        self.assertIsNone(f.best_sponsorship)
        self.assertEqual([b.posting_id for b in f.stale_postings], [pid])
        self.assertEqual(f.stale_postings[0].stale_reason, "posted 2016-03-02, over 12 months")
        self.assertEqual(f.grade("posting_sponsors").level, "low")
        self.assertEqual(f.grade("live_role").level, "high")  # stale by a posted date is a fact

    def test_a_failed_read_leaves_the_posting_live_in_the_facts(self):
        self.conn.execute("UPDATE board SET last_read = 'error', error_streak = 1 WHERE board_id = ?", (self.board,))
        self.add("ng")
        f = self.one()
        self.assertEqual(f.lanes, ("level_role",))
        self.assertEqual(f.stale_postings, ())
        self.assertEqual(f.grade("live_role").level, "medium")


if __name__ == "__main__":
    unittest.main()
