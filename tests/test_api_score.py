"""The score breakdown: read from stored `company_score` rows in the ranking plan's shape, never recomputed."""
import unittest

from outreach.api.queries import score as score_queries
from tests import apiseed as s
from tests.apitest import NOW, ApiCase


class Score(ApiCase):
    def score(self, company_id: int) -> dict:
        res = self.get(f"/api/companies/{company_id}/score")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def signals(self, company_id: int) -> dict:
        return {x["name"]: x for x in self.score(company_id)["signals"]}

    def test_score_points_and_shares_add_up(self):
        cid = s.company(self.conn, "Acme", "backlog")
        s.score(self.conn, cid, {"live_role": 40.0, "everify": 12.0, "small": 12.0, "staffing_risk": -16.0},
                lanes=("level_role", "any_us_role"), line="Hiring a new grad engineer")
        view = self.score(cid)
        self.assertEqual(view["asOf"], NOW)
        self.assertEqual((view["companyId"], view["rankerVersion"], view["score"]), (cid, "rk-1", 48.0))
        self.assertEqual(view["scoredAt"], "2026-11-10T08:00:00Z")
        shares = [x["share"] for x in view["signals"]]
        self.assertAlmostEqual(sum(abs(x) for x in shares), 1.0, delta=0.001)
        by = {x["name"]: x for x in view["signals"]}
        self.assertAlmostEqual(by["live_role"]["share"], 40 / 80, places=3)
        self.assertAlmostEqual(by["staffing_risk"]["share"], -16 / 80, places=3)  # signed
        live = by["live_role"]
        self.assertEqual((live["points"], live["value"], live["weight"], live["reason"]), (40.0, 1.0, 40.0, "live_role reason"))
        self.assertEqual((live["confidence"], live["basis"]), ("medium", "live_role basis"))  # from values[name]
        self.assertEqual([x["lane"] for x in view["lanes"]], ["level_role", "any_us_role"])
        self.assertEqual([x["strongest"] for x in view["lanes"]], [True, False])

    def test_score_rank_reason_and_confidence_are_the_stored_ones(self):
        cid = s.company(self.conn, "Acme")
        s.score(self.conn, cid, {"live_role": 40.0}, confidence=("low", "the role is from an unread board"),
                line="Hiring")
        view = self.score(cid)
        self.assertEqual(view["rankReason"], "Hiring (low confidence: the role is from an unread board)")
        self.assertEqual(view["confidence"], {"level": "low", "basis": "the role is from an unread board"})

    def test_score_neutral_signal_has_no_share_weight_and_says_so(self):
        cid = s.company(self.conn, "Acme")
        s.score(self.conn, cid, {"live_role": 40.0, "small": 0.0, "funding": 0.0})
        by = self.signals(cid)
        self.assertEqual((by["small"]["neutral"], by["small"]["share"], by["small"]["points"]), (True, 0, 0))
        self.assertFalse(by["live_role"]["neutral"])
        self.assertEqual(by["live_role"]["share"], 1)

    def test_score_with_every_signal_neutral_has_zero_shares(self):
        cid = s.company(self.conn, "Acme")
        s.score(self.conn, cid, {"live_role": 0.0, "small": 0.0})
        self.assertEqual([x["share"] for x in self.score(cid)["signals"]], [0, 0])

    def test_score_neutral_is_the_value_not_the_points(self):
        cid = s.company(self.conn, "Acme")
        s.score(self.conn, cid, {"live_role": 40.0, "pay_level": 0.0}, value={"live_role": 0.0, "pay_level": 0.5})
        by = self.signals(cid)
        self.assertEqual((by["live_role"]["neutral"], by["pay_level"]["neutral"]), (True, False))

    def test_score_signals_are_ordered_by_abs_points(self):
        cid = s.company(self.conn, "Acme")
        s.score(self.conn, cid, {"funding": 5.0, "small": 5.0, "staffing_risk": -30.0, "live_role": 20.0, "pay_level": 0.0, "everify": -5.0})
        names = [x["name"] for x in self.score(cid)["signals"]]
        # abs(points) descending; the 5-point tie follows the ranker's signal order, not the alphabet
        self.assertEqual(names, ["staffing_risk", "live_role", "everify", "small", "funding", "pay_level"])

    def test_signal_order_is_the_rankers(self):
        from outreach.ranking.config import load_ranker_config
        from outreach.ranking.signals import build_signals
        self.assertEqual(list(score_queries.SIGNAL_ORDER), [x.name for x in build_signals(load_ranker_config())])

    def test_score_labels(self):
        cid = s.company(self.conn, "Acme")
        s.score(self.conn, cid, {"live_role": 1.0, "everify": 1.0, "pay_level": 1.0, "mystery_new_signal": 1.0})
        by = self.signals(cid)
        self.assertEqual(by["pay_level"]["label"], "Pay level")
        self.assertEqual(by["everify"]["label"], "E-Verify")
        self.assertEqual(by["mystery_new_signal"]["label"], "Mystery new signal")  # an unknown name still reads well
        self.assertEqual([x["name"] for x in self.score(cid)["signals"]][-1], "mystery_new_signal")  # last on a tie

    def test_the_profile_fit_row_is_labelled_for_people(self):
        cid = s.company(self.conn, "Acme")
        s.score(self.conn, cid, {"live_role": 1.0, "profile_fit": 6.12})
        self.assertEqual(self.signals(cid)["profile_fit"]["label"], "Fits what you build")

    def test_score_rank_ignores_hidden_companies(self):
        a = s.company(self.conn, "A")
        hidden = s.company(self.conn, "Hidden")
        b = s.company(self.conn, "B")
        c = s.company(self.conn, "C")
        s.score(self.conn, a, {"live_role": 90.0})
        s.score(self.conn, hidden, {"live_role": 80.0}, hide={"kind": "over_size", "reason": "Over 300", "provisional": False})
        s.score(self.conn, b, {"live_role": 70.0})
        s.score(self.conn, c, {"live_role": 60.0})
        self.assertEqual((self.score(b)["rank"], self.score(b)["ofCompanies"]), (2, 3))
        self.assertEqual((self.score(c)["rank"], self.score(c)["ofCompanies"]), (3, 3))
        view = self.score(hidden)  # a hidden company still has its breakdown, and no place in the list
        self.assertEqual((view["rank"], view["ofCompanies"], view["score"]), (None, 3, 80.0))

    def test_score_rank_ties_break_on_the_lower_company_id(self):
        a = s.company(self.conn, "A")
        b = s.company(self.conn, "B")
        s.score(self.conn, a, {"live_role": 50.0})
        s.score(self.conn, b, {"live_role": 50.0})
        self.assertEqual((self.score(a)["rank"], self.score(b)["rank"]), (1, 2))

    def test_score_rank_counts_only_the_companys_own_version(self):
        a = s.company(self.conn, "A")
        b = s.company(self.conn, "B")
        s.score(self.conn, a, {"live_role": 10.0}, version="rk-old", at="2026-11-01T08:00:00Z")
        s.score(self.conn, b, {"live_role": 99.0}, version="rk-old", at="2026-11-01T08:00:00Z")
        s.score(self.conn, a, {"live_role": 10.0}, version="rk-new", at="2026-11-10T08:00:00Z")
        view = self.score(a)
        self.assertEqual((view["rankerVersion"], view["rank"], view["ofCompanies"]), ("rk-new", 1, 1))

    def test_score_previous_version_is_the_next_older(self):
        cid = s.company(self.conn, "Acme")
        s.score(self.conn, cid, {"live_role": 10.0}, version="rk-1", at="2026-10-01T08:00:00Z")
        s.score(self.conn, cid, {"live_role": 20.0}, version="rk-2", at="2026-10-15T08:00:00Z")
        s.score(self.conn, cid, {"live_role": 30.0}, version="rk-3", at="2026-11-01T08:00:00Z")
        view = self.score(cid)
        self.assertEqual((view["rankerVersion"], view["score"]), ("rk-3", 30.0))
        self.assertEqual(view["previous"], {"rankerVersion": "rk-2", "score": 20.0})  # not rk-1

    def test_score_previous_is_null_with_one_version(self):
        cid = s.company(self.conn, "Acme")
        s.score(self.conn, cid, {"live_role": 10.0})
        self.assertIsNone(self.score(cid)["previous"])

    def test_score_newest_is_by_scored_at_not_by_version_text(self):
        cid = s.company(self.conn, "Acme")
        s.score(self.conn, cid, {"live_role": 10.0}, version="zzz", at="2026-10-01T08:00:00Z")
        s.score(self.conn, cid, {"live_role": 20.0}, version="aaa", at="2026-11-01T08:00:00Z")
        view = self.score(cid)
        self.assertEqual((view["rankerVersion"], view["previous"]["rankerVersion"]), ("aaa", "zzz"))

    def test_score_signal_source_is_the_best_postings_url_for_role_and_pay(self):
        cid = s.company(self.conn, "Acme")
        pid = s.posting(self.conn, cid, "New Grad Engineer", url="https://jobs.example/ng")
        s.score(self.conn, cid, {"live_role": 40.0, "pay_level": 5.0, "posting_sponsors": 5.0, "small": 3.0},
                best_posting_id=pid)
        by = self.signals(cid)
        for name in ("live_role", "pay_level", "posting_sponsors"):
            self.assertEqual(by[name]["source"], {"label": "New Grad Engineer", "url": "https://jobs.example/ng"}, name)
        self.assertIsNone(by["small"]["source"])  # no size evidence

    def test_score_signal_source_label_is_the_posting_title_scrubbed_to_one_plain_line(self):
        cid = s.company(self.conn, "Acme")
        pid = s.posting(self.conn, cid, "New Grad Engineer \u2014 Platform\n  (Remote)", url="https://jobs.example/ng")
        s.score(self.conn, cid, {"live_role": 40.0}, best_posting_id=pid)
        self.assertEqual(self.signals(cid)["live_role"]["source"]["label"], "New Grad Engineer - Platform (Remote)")

    def test_score_signal_source_is_the_winning_evidence_rows_http_ref(self):
        cid = s.company(self.conn, "Acme")
        s.evidence(self.conn, cid, "everify", "enrolled", source_kind="everify", source_ref="https://e-verify.example/acme",
                   recorded_at="2026-11-01T12:00:00Z")
        # a locked hand value wins over a newer guess, so its ref (not the guess's) is the source
        s.evidence(self.conn, cid, "size", "20", locked=True, source_ref="https://acme.example/about")
        s.evidence(self.conn, cid, "size", "400", confidence="guess", source_kind="builtin",
                   source_ref="https://builtin.example/acme", recorded_at="2026-11-05T12:00:00Z")
        s.evidence(self.conn, cid, "lca", s.lca(), source_kind="lca", source_ref="https://lca.example/acme")
        s.score(self.conn, cid, {"everify": 12.0, "small": 12.0, "lca_junior": 12.0, "reachable_person": 8.0})
        by = self.signals(cid)
        self.assertEqual(by["everify"]["source"]["url"], "https://e-verify.example/acme")
        self.assertEqual(by["small"]["source"]["url"], "https://acme.example/about")
        self.assertEqual(by["lca_junior"]["source"]["url"], "https://lca.example/acme")
        self.assertIsNone(by["reachable_person"]["source"])
        for name in ("everify", "small", "lca_junior"):
            self.assertTrue(by[name]["source"]["label"], name)

    def test_score_signal_source_is_null_for_a_non_url_ref(self):
        cid = s.company(self.conn, "Acme")
        s.evidence(self.conn, cid, "everify", "enrolled", source_kind="everify", source_ref="everify-2026-10.csv row 4")
        s.evidence(self.conn, cid, "cap_exempt", "confirmed", source_kind="other", source_ref="")
        s.score(self.conn, cid, {"everify": 12.0, "cap_exempt": 10.0})
        by = self.signals(cid)
        self.assertIsNone(by["everify"]["source"])
        self.assertIsNone(by["cap_exempt"]["source"])

    def test_score_signal_source_is_null_without_a_best_posting(self):
        cid = s.company(self.conn, "Acme")
        s.score(self.conn, cid, {"live_role": 0.0}, best_posting_id=None)
        self.assertIsNone(self.signals(cid)["live_role"]["source"])

    def test_score_signal_source_reads_a_merged_duplicates_evidence(self):
        a = s.company(self.conn, "Acme")
        b = s.company(self.conn, "Acme Labs", "removed", remove_reason="merged", merged_into=a)
        s.evidence(self.conn, b, "everify", "enrolled", source_kind="everify", source_ref="https://e-verify.example/b")
        s.score(self.conn, a, {"everify": 12.0})
        self.assertEqual(self.signals(b)["everify"]["source"]["url"], "https://e-verify.example/b")

    def test_score_of_a_merged_id_answers_as_its_survivor(self):
        a = s.company(self.conn, "Acme")
        b = s.company(self.conn, "Acme Labs", "removed", remove_reason="merged", merged_into=a)
        c = s.company(self.conn, "Acme Labs Inc", "removed", remove_reason="merged", merged_into=b)
        s.score(self.conn, a, {"live_role": 40.0})
        self.assertEqual(self.score(c)["companyId"], a)
        self.assertEqual(self.score(c), self.score(a))

    def test_score_without_a_row_is_404(self):
        cid = s.company(self.conn, "Acme")
        res = self.get(f"/api/companies/{cid}/score")
        self.assertEqual((res.status_code, res.json()["code"]), (404, "not_found"))

    def test_score_of_an_unknown_company_is_404(self):
        s.score(self.conn, s.company(self.conn, "Acme"), {"live_role": 40.0})
        res = self.get("/api/companies/999/score")
        self.assertEqual((res.status_code, res.json()["code"]), (404, "not_found"))

    def test_score_of_a_merged_away_company_whose_survivor_has_no_score_is_404(self):
        a = s.company(self.conn, "Acme")
        b = s.company(self.conn, "Acme Labs", "removed", remove_reason="merged", merged_into=a)
        s.score(self.conn, b, {"live_role": 40.0})  # a score on the duplicate is not the survivor's
        self.assertEqual(self.get(f"/api/companies/{b}/score").status_code, 404)


class QueryPlans(ApiCase):
    def plan(self, sql: str, params: tuple) -> list[str]:
        return [r["detail"] for r in self.conn.execute("EXPLAIN QUERY PLAN " + sql, params)]

    def test_the_score_reads_look_rows_up_by_key_and_never_join_a_view(self):
        cid = s.company(self.conn, "Acme")
        s.score(self.conn, cid, {"live_role": 40.0})
        cases = {
            "rows": (score_queries.ROWS_SQL, (cid,)),
            "rank": (score_queries.RANK_SQL, (40.0, 40.0, cid, "rk-1")),
            "evidence": (score_queries.EVIDENCE_SQL.format(marks="?"), (cid,)),
        }
        for name, (sql, params) in cases.items():
            for line in self.plan(sql, params):
                self.assertNotIn("MATERIALIZE", line, name)
                self.assertNotIn("company_fact", line, name)
                self.assertNotIn("current_decision", line, name)
                self.assertNotIn("posting_decision", line, name)
        self.assertTrue(any("SEARCH company_score USING" in line for line in self.plan(*cases["rows"])))
        self.assertTrue(any("SEARCH evidence USING INDEX evidence_company" in line for line in self.plan(*cases["evidence"])))
        # the rank query is one pass over one table: no join, no sort
        self.assertEqual(len(self.plan(*cases["rank"])), 1, self.plan(*cases["rank"]))


if __name__ == "__main__":
    unittest.main()
