"""Size and sponsorship-conflict review items (issue #6, Task 10; rulings 9 and 11; design 5.4).

An item only asks Devanshu to look. It never changes a score, a hide or the list."""
import dataclasses
import unittest

from outreach.ranking.config import default_ranker_config
from outreach.ranking.facts import SHARED_DOMAIN_FLAG
from outreach.ranking.hide import Hide, HideInputs, Refusal, SizeEvidence
from outreach.ranking.review import sync_conflict_items, sync_size_items
from outreach.ranking.score import list_order, score_company, to_columns
from outreach.ranking.signals import build_signals
from tests import ranking_store as rs
from tests.ranking_helpers import neutral_facts
from tests.storetest import fresh_store, seed_company

CFG = default_ranker_config()
SIGNALS = build_signals(CFG)
TOP2 = dataclasses.replace(CFG, ranker={**CFG.ranker, "size_check_top_n": 2})
NOW = "2026-10-22T09:00:00Z"
LATER = "2026-10-23T09:00:00Z"


def guess(low: int, evidence_id: int = 1, confidence: str = "guess", locked: bool = False) -> SizeEvidence:
    return SizeEvidence(low, None, confidence, "https://example.com/about", evidence_id, locked)


class Store(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.a, self.b, self.c = (seed_company(self.conn, n) for n in ("Alpha", "Beta", "Gamma"))
        # posting ids 1 to 12 exist, so a conflict item's posting_id is a real foreign key
        self.postings = [rs.posting(self.conn, self.a) for _ in range(12)]
        self.assertEqual(self.postings, list(range(1, 13)))

    def company(self, cid: int, points: float, *, flags=(), hide: Hide | None = None, size_low: int | None = None):
        """(scored row at `points`, its facts); the size is unknown unless `size_low` says so"""
        facts = neutral_facts(company_id=cid, name=f"Co {cid}", weak_flags=tuple(flags), size_low=size_low,
                              size_high=size_low)
        return dataclasses.replace(score_company(facts, hide, SIGNALS, CFG), score=points), facts

    def size_sync(self, rows, sizes=None, cfg=CFG, now=NOW):
        scored = [s for s, _f in rows]
        return sync_size_items(self.conn, scored, {f.company_id: f for _s, f in rows}, sizes or {}, cfg, now=now)

    def items(self, kind: str | None = None, state: str | None = None) -> list:
        sql, args = "SELECT * FROM review_item WHERE raised_by = 'ranker'", []
        if kind:
            sql, args = sql + " AND kind = ?", args + [kind]
        if state:
            sql, args = sql + " AND state = ?", args + [state]
        return list(self.conn.execute(sql + " ORDER BY review_id", args))


class SizeCheckTests(Store):
    def test_weak_flag_in_the_top_n_raises_size_check_and_outside_it_does_not(self):
        rows = [self.company(self.a, 30.0, flags=("90 open roles",)), self.company(self.b, 20.0),
                self.company(self.c, 10.0, flags=("90 open roles",))]
        self.assertEqual(self.size_sync(rows, cfg=TOP2), (1, 0))
        got = self.items("size_check")
        self.assertEqual([(r["company_id"], r["evidence_key"], r["state"]) for r in got],
                         [(self.a, "many_open_roles", "open")])
        self.assertIn("90 open roles", got[0]["reason"])
        self.assertIsNone(got[0]["board_id"])
        self.assertIsNone(got[0]["posting_id"])

    def test_the_top_n_counts_only_companies_that_are_listed(self):
        gone = Hide("no_go", "On the no-go list", False)
        rows = [self.company(self.a, 99.0, flags=("90 open roles",), hide=gone),
                self.company(self.b, 20.0, flags=("90 open roles",)), self.company(self.c, 10.0)]
        self.assertEqual(self.size_sync(rows, cfg=TOP2), (1, 0))  # the hidden one takes no place in the top 2
        self.assertEqual([r["company_id"] for r in self.items("size_check")], [self.b])

    def test_the_key_is_the_sorted_flag_kinds_and_the_reason_is_one_plain_line(self):
        dash = "9 roles " + chr(0x2014) + " many"
        flags = ("12 software H-1B filings in one fiscal year", "90 open roles", "later stage, size unknown", dash)
        self.size_sync([self.company(self.a, 30.0, flags=flags)])
        got = self.items("size_check")[0]
        self.assertEqual(got["evidence_key"],
                         "later_stage_no_size;many_open_roles;many_software_lca;other:N roles - many")
        self.assertIn("90 open roles", got["reason"])  # the count lives in the reason
        self.assertNotIn("\n", got["reason"])
        for c in (chr(0x2014), chr(0x2013)):
            self.assertNotIn(c, got["reason"])

    def test_a_known_size_needs_no_check(self):
        rows = [self.company(self.a, 30.0, flags=("90 open roles", "later stage, size unknown"), size_low=50)]
        self.assertEqual(self.size_sync(rows, {self.a: guess(50, 3, confidence="confirmed")}), (0, 0))
        self.assertEqual(self.items(), [])

    def test_a_known_size_clears_an_open_check(self):
        self.size_sync([self.company(self.a, 30.0, flags=("90 open roles",))])
        rows = [self.company(self.a, 30.0, flags=("90 open roles",), size_low=50)]
        self.assertEqual(self.size_sync(rows, now=LATER), (0, 1))

    def test_shared_domain_flag_raises_no_ranker_size_check(self):
        rows = [self.company(self.a, 30.0, flags=(SHARED_DOMAIN_FLAG,))]
        self.assertEqual(self.size_sync(rows), (0, 0))
        self.assertEqual(self.items(), [])
        # with another flag beside it, the shared-domain flag is left out of the key
        rows = [self.company(self.a, 30.0, flags=(SHARED_DOMAIN_FLAG, "90 open roles"))]
        self.size_sync(rows)
        self.assertEqual(self.items("size_check")[0]["evidence_key"], "many_open_roles")

    def test_no_flag_and_no_size_raise_nothing(self):
        self.assertEqual(self.size_sync([self.company(self.a, 30.0)]), (0, 0))
        self.assertEqual(self.items(), [])


class SizeGuessTests(Store):
    def test_guess_over_the_cap_raises_size_guess_wherever_it_ranks(self):
        rows = [self.company(self.a, 30.0), self.company(self.b, 20.0), self.company(self.c, -150.0)]
        self.assertEqual(self.size_sync(rows, {self.c: guess(400, 7)}, cfg=TOP2), (1, 0))
        got = self.items("size_guess")
        self.assertEqual([(r["company_id"], r["evidence_key"]) for r in got], [(self.c, "7")])
        self.assertIn("400", got[0]["reason"])

    def test_only_a_guess_over_the_cap_raises_one(self):
        rows = [self.company(self.a, 30.0), self.company(self.b, 20.0), self.company(self.c, 10.0)]
        sizes = {self.a: guess(300, 1), self.b: guess(80, 2), self.c: guess(900, 3, confidence="estimate")}
        self.assertEqual(self.size_sync(rows, sizes), (0, 0))  # 300 is not over; an estimate hides, it is no guess

    def test_a_guess_never_hides_and_the_company_stays_listed(self):
        rows = [self.company(self.a, 30.0)]
        self.size_sync(rows, {self.a: guess(500)})
        self.assertEqual([s.company_id for s in list_order([r[0] for r in rows])], [self.a])

    def test_unknown_size_alone_raises_nothing(self):
        rows = [self.company(self.a, 30.0), self.company(self.b, 20.0)]
        self.assertEqual(self.size_sync(rows, {}), (0, 0))  # size_unknown is #15's item, not the ranker's
        self.assertEqual(self.items(), [])

    def test_a_hand_locked_guess_raises_no_size_guess(self):
        rows = [self.company(self.a, 30.0)]
        self.assertEqual(self.size_sync(rows, {self.a: guess(500, 4, locked=True)}), (0, 0))
        self.assertEqual(self.items(), [])

    def test_a_hidden_company_raises_no_size_guess(self):
        gone = Hide("no_go", "On the no-go list", False)
        rows = [self.company(self.a, 30.0, hide=gone)]
        self.assertEqual(self.size_sync(rows, {self.a: guess(500)}), (0, 0))

    def test_a_company_that_is_not_scored_raises_nothing(self):
        rows = [self.company(self.a, 30.0)]
        self.assertEqual(self.size_sync(rows, {self.b: guess(500)}), (0, 0))


class SizeLifecycleTests(Store):
    def test_a_resolved_item_stays_resolved_until_the_evidence_changes(self):
        rows = [self.company(self.a, 30.0)]
        self.size_sync(rows, {self.a: guess(400, 11)})
        rs.resolve(self.conn, self.items("size_guess")[0]["review_id"], "marked_ok")
        self.assertEqual(self.size_sync(rows, {self.a: guess(400, 11)}, now=LATER), (0, 0))
        self.assertEqual([r["state"] for r in self.items("size_guess")], ["resolved"])
        self.assertEqual(self.items("size_guess")[0]["resolution"], "marked_ok")
        # new evidence, new key, a new item
        self.assertEqual(self.size_sync(rows, {self.a: guess(450, 12)}, now=LATER), (1, 0))
        self.assertEqual([(r["evidence_key"], r["state"]) for r in self.items("size_guess")],
                         [("11", "resolved"), ("12", "open")])

    def test_a_resolved_size_check_stays_resolved_until_the_flags_change(self):
        rows = [self.company(self.a, 30.0, flags=("90 open roles",))]
        self.size_sync(rows)
        rs.resolve(self.conn, self.items("size_check")[0]["review_id"], "marked_ok")
        self.assertEqual(self.size_sync(rows, now=LATER), (0, 0))
        rows = [self.company(self.a, 30.0, flags=("90 open roles", "later stage, size unknown"))]
        self.assertEqual(self.size_sync(rows, now=LATER), (1, 0))

    def test_a_changed_count_keeps_a_resolved_item_resolved_and_adds_no_row(self):
        self.size_sync([self.company(self.a, 30.0, flags=("90 open roles",))])
        rs.resolve(self.conn, self.items("size_check")[0]["review_id"], "marked_ok")
        self.assertEqual(self.size_sync([self.company(self.a, 30.0, flags=("91 open roles",))], now=LATER), (0, 0))
        got = self.items("size_check")
        self.assertEqual([(r["evidence_key"], r["state"], r["resolution"]) for r in got],
                         [("many_open_roles", "resolved", "marked_ok")])

    def test_a_changed_count_updates_the_reason_of_an_open_item_in_place(self):
        self.size_sync([self.company(self.a, 30.0, flags=("90 open roles",))])
        self.assertEqual(self.size_sync([self.company(self.a, 30.0, flags=("91 open roles",))], now=LATER), (0, 0))
        got = self.items("size_check")
        self.assertEqual(len(got), 1)
        self.assertIn("91 open roles", got[0]["reason"])
        self.assertNotIn("90 open roles", got[0]["reason"])

    def test_a_condition_that_went_away_is_cleared(self):
        flagged = [self.company(self.a, 30.0, flags=("90 open roles",)), self.company(self.b, 20.0)]
        self.size_sync(flagged, {self.b: guess(400, 5)})
        self.assertEqual(len(self.items(state="open")), 2)
        clean = [self.company(self.a, 30.0), self.company(self.b, 20.0)]
        self.assertEqual(self.size_sync(clean, {}, now=LATER), (0, 2))
        self.assertEqual([(r["state"], r["resolution"], r["resolved_at"]) for r in self.items()],
                         [("resolved", "cleared", LATER)] * 2)

    def test_a_changed_key_clears_the_old_item_and_raises_the_new_one(self):
        self.size_sync([self.company(self.a, 30.0, flags=("90 open roles",))])
        got = self.size_sync([self.company(self.a, 30.0, flags=("30 software H-1B filings in one fiscal year",))],
                             now=LATER)
        self.assertEqual(got, (1, 1))
        self.assertEqual([r["state"] for r in self.items("size_check")], ["resolved", "open"])

    def test_a_company_that_falls_out_of_the_top_n_is_cleared(self):
        self.size_sync([self.company(self.a, 30.0, flags=("90 open roles",)), self.company(self.b, 20.0)], cfg=TOP2)
        rows = [self.company(self.a, 5.0, flags=("90 open roles",)), self.company(self.b, 20.0),
                self.company(self.c, 30.0)]
        self.assertEqual(self.size_sync(rows, cfg=TOP2, now=LATER), (0, 1))

    def test_a_cleared_item_opens_again_when_the_condition_comes_back(self):
        flagged = [self.company(self.a, 30.0, flags=("90 open roles",))]
        self.size_sync(flagged)
        self.size_sync([self.company(self.a, 30.0)], now=LATER)
        self.assertEqual(self.items("size_check")[0]["state"], "resolved")
        self.assertEqual(self.size_sync(flagged, now="2026-10-24T09:00:00Z"), (1, 0))
        got = self.items("size_check")
        self.assertEqual((len(got), got[0]["state"], got[0]["resolution"]), (1, "open", None))

    def test_a_rerun_changes_nothing(self):
        rows = [self.company(self.a, 30.0, flags=("90 open roles",)), self.company(self.b, 20.0)]
        sizes = {self.b: guess(400, 5)}
        self.assertEqual(self.size_sync(rows, sizes), (2, 0))
        before = [tuple(r) for r in self.conn.execute("SELECT * FROM review_item ORDER BY review_id")]
        self.assertEqual(self.size_sync(rows, sizes, now=LATER), (0, 0))
        self.assertEqual([tuple(r) for r in self.conn.execute("SELECT * FROM review_item ORDER BY review_id")], before)

    def test_only_the_rankers_own_items_are_cleared(self):
        mine = self.conn.execute(
            "INSERT INTO review_item(kind, company_id, reason, evidence_key, raised_by, created_at) "
            "VALUES ('size_guess', ?, 'imported guess', '99', 'import', ?)", (self.a, NOW)).lastrowid
        kinds = self.conn.execute(
            "INSERT INTO review_item(kind, company_id, reason, evidence_key, raised_by, created_at) "
            "VALUES ('size_unknown', ?, 'no size', 'k', 'ranker', ?)", (self.a, NOW)).lastrowid
        self.assertEqual(self.size_sync([self.company(self.a, 30.0)]), (0, 0))
        got = {r["review_id"]: r["state"] for r in self.conn.execute("SELECT review_id, state FROM review_item")}
        self.assertEqual(got, {mine: "open", kinds: "open"})


def conflict(company_id: int, refuse=(5,), sponsors=(8,)) -> HideInputs:
    return HideInputs(company_id, None, "", tuple(Refusal(p, False, "We cannot sponsor.") for p in refuse),
                      tuple(sponsors), None)


class ConflictTests(Store):
    def sync(self, inputs, now=NOW, hidden=(), omit=()):
        """Every company in `inputs` is scored and listed, except those in `hidden` (a no-go) or `omit` (not scored)."""
        gone = Hide("no_go", "On the no-go list", False)
        scored = [self.company(cid, 10.0, hide=gone if cid in hidden else None)[0]
                  for cid in {self.a, self.b, self.c, *inputs} if cid not in omit]
        return sync_conflict_items(self.conn, inputs, scored, CFG, now=now)

    def test_sponsorship_conflict_raises_one_flagged_item_and_the_company_stays_listed(self):
        inputs = {self.a: conflict(self.a, refuse=(6, 5), sponsors=(8,)), self.b: conflict(self.b, (), (3,)),
                  self.c: conflict(self.c, (4,), ())}
        self.assertEqual(self.sync(inputs), (1, 0))
        (item,) = self.items()
        self.assertEqual((item["kind"], item["company_id"], item["posting_id"], item["raised_by"], item["state"]),
                         ("flagged", self.a, 5, "ranker", "open"))
        self.assertEqual(item["evidence_key"], "sponsorship-conflict:5,6")
        self.assertIn("posting", item["reason"])
        self.assertEqual(item["reason"], "Postings disagree on sponsorship: postings 5 and 6 refuse and posting 8 "
                                         "says it sponsors. The company stays visible.")
        scored, _facts = self.company(self.a, 12.0)
        self.assertEqual([s.company_id for s in list_order([scored])], [self.a])

    def test_a_hidden_or_unscored_company_gets_no_conflict_item(self):
        inputs = {self.a: conflict(self.a), self.b: conflict(self.b, (1,), (2,)), self.c: conflict(self.c, (3,), (4,))}
        self.assertEqual(self.sync(inputs, hidden=(self.a,), omit=(self.b,)), (1, 0))  # b: closed, never scored
        self.assertEqual([r["company_id"] for r in self.items()], [self.c])

    def test_a_company_that_becomes_hidden_has_its_open_item_cleared(self):
        self.sync({self.a: conflict(self.a)})
        self.assertEqual(self.sync({self.a: conflict(self.a)}, now=LATER, hidden=(self.a,)), (0, 1))
        self.assertEqual([(r["state"], r["resolution"]) for r in self.items()], [("resolved", "cleared")])

    def test_the_reason_names_both_postings_when_there_is_one_of_each(self):
        self.sync({self.a: conflict(self.a, (5,), (8,))})
        self.assertEqual(self.items()[0]["reason"], "Postings disagree on sponsorship: posting 5 refuses and "
                                                    "posting 8 says it sponsors. The company stays visible.")

    def test_many_sponsoring_postings_are_all_named(self):
        self.sync({self.a: conflict(self.a, (5,), (9, 8))})
        self.assertIn("postings 8 and 9 say they sponsor", self.items()[0]["reason"])

    def test_conflict_item_clears_when_the_condition_goes_away(self):
        self.sync({self.a: conflict(self.a)})
        self.assertEqual(self.sync({self.a: conflict(self.a, (5,), ())}, now=LATER), (0, 1))
        self.assertEqual([(r["state"], r["resolution"]) for r in self.items()], [("resolved", "cleared")])
        self.assertEqual(self.sync({}, now=LATER), (0, 0))

    def test_a_company_gone_from_the_inputs_is_cleared(self):
        self.sync({self.a: conflict(self.a)})
        self.assertEqual(self.sync({self.b: conflict(self.b, (1,), (2,))}, now=LATER), (1, 1))

    def test_a_resolved_conflict_item_stays_resolved_until_the_key_changes(self):
        self.sync({self.a: conflict(self.a)})
        rs.resolve(self.conn, self.items()[0]["review_id"], "marked_ok")
        self.assertEqual(self.sync({self.a: conflict(self.a)}, now=LATER), (0, 0))
        self.assertEqual([r["state"] for r in self.items()], ["resolved"])
        self.assertEqual(self.sync({self.a: conflict(self.a, (5, 7), (8,))}, now=LATER), (1, 0))
        self.assertEqual([(r["evidence_key"], r["state"]) for r in self.items()],
                         [("sponsorship-conflict:5", "resolved"), ("sponsorship-conflict:5,7", "open")])

    def test_a_cleared_conflict_opens_again_when_it_comes_back(self):
        self.sync({self.a: conflict(self.a)})
        self.sync({}, now=LATER)
        self.assertEqual(self.sync({self.a: conflict(self.a)}, now="2026-10-24T09:00:00Z"), (1, 0))
        self.assertEqual([(r["state"], r["resolution"]) for r in self.items()], [("open", None)])

    def test_the_reason_follows_a_changed_sponsor_set_without_a_new_item(self):
        self.sync({self.a: conflict(self.a, (5,), (8,))})
        self.assertEqual(self.sync({self.a: conflict(self.a, (5,), (9,))}, now=LATER), (0, 0))
        (item,) = self.items()
        self.assertIn("posting 9 says it sponsors", item["reason"])

    def test_a_rerun_changes_nothing(self):
        inputs = {self.a: conflict(self.a), self.b: conflict(self.b, (1,), (2,))}
        self.assertEqual(self.sync(inputs), (2, 0))
        before = [tuple(r) for r in self.conn.execute("SELECT * FROM review_item ORDER BY review_id")]
        self.assertEqual(self.sync(inputs, now=LATER), (0, 0))
        self.assertEqual([tuple(r) for r in self.conn.execute("SELECT * FROM review_item ORDER BY review_id")], before)

    def test_other_flagged_items_are_left_alone(self):
        doubtful = rs.flag_board(self.conn, self.a, rs.board(self.conn, self.a))
        theirs = self.conn.execute(
            "INSERT INTO review_item(kind, company_id, reason, evidence_key, raised_by, created_at) "
            "VALUES ('flagged', ?, 'ranker note', 'something-else', 'ranker', ?)", (self.a,  NOW)).lastrowid
        self.assertEqual(self.sync({}), (0, 0))
        got = {r["review_id"]: r["state"] for r in self.conn.execute("SELECT review_id, state FROM review_item")}
        self.assertEqual(got, {doubtful: "open", theirs: "open"})


class NeverHidesTests(Store):
    def test_items_never_change_a_score_or_a_hide(self):
        gone = Hide("no_go", "On the no-go list", False)
        rows = [self.company(self.a, 30.0, flags=("90 open roles",)), self.company(self.b, 20.0),
                self.company(self.c, 10.0, hide=gone)]
        scored = [s for s, _f in rows]
        before = [(s.score, s.hide, to_columns(s)) for s in scored]
        order = [s.company_id for s in list_order(scored)]
        self.size_sync(rows, {self.b: guess(500, 4)})
        sync_conflict_items(self.conn, {self.a: conflict(self.a)}, scored, CFG, now=NOW)
        self.assertEqual([(s.score, s.hide, to_columns(s)) for s in scored], before)
        self.assertEqual([s.company_id for s in list_order(scored)], order)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM no_go").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM company_score").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
