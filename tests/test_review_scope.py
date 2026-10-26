"""Which companies' items ask for his attention, and the sweep step that clears the crawler's out-of-scope gaps
(#16, the company-first ruling in #9)."""
import unittest
from types import SimpleNamespace
from unittest import mock

from outreach.review.config import ReviewConfig
from outreach.review.items import resolve_item
from outreach.review.scope import OUT_OF_SCOPE_NOTE, attention, clear_out_of_scope, ranked_ids
from outreach.review.triggers import sweep_needs_you
from tests.review_seed import seed_email_person, seed_item, seed_plan_row, seed_queued_message
from tests.storetest import fresh_store, seed_company

NOW = "2026-11-10T15:00:00Z"
TODAY = "2026-11-10"
CFG = ReviewConfig(sponsorship_top_n=150, planned_days=4, stale_days=14, crawler_scope_top=2, crawler_cap_per_run=10)


def ranked(*ids):
    """The ranked list as `ranked_list` answers it, highest first, cut at its `limit`."""
    rows = [SimpleNamespace(company_id=c) for c in ids]
    return mock.patch("outreach.ranking.scores.ranked_list", side_effect=lambda conn, *a, limit=None, **kw: rows[:limit])


class ScopeBase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def company(self, name):
        cid = seed_company(self.conn, name)
        self.conn.execute("UPDATE company SET status = 'candidate' WHERE company_id = ?", (cid,))
        return cid

    def held(self, cid):
        person = seed_email_person(self.conn, cid)
        seed_queued_message(self.conn, person, "sent")
        touch = self.conn.execute("SELECT touch_id FROM touch WHERE person_id = ?", (person,)).fetchone()[0]
        self.conn.execute("UPDATE touch SET state = 'replied', replied_at = ?, reply_kind = 'unsorted' WHERE touch_id = ?", (NOW, touch))
        self.conn.execute("UPDATE company SET held_by_touch = ?, held_at = ? WHERE company_id = ?", (touch, NOW, cid))

    def crawler_gap(self, cid, key="no-usable-domain"):
        return seed_item(self.conn, "domain_missing", cid, raised_by="crawler", evidence_key=key,
                         reason="No company domain is known, so its careers page cannot be checked for a board.")

    def state(self, review_id):
        return tuple(self.conn.execute("SELECT state, resolution, resolution_note FROM review_item "
                                       "WHERE review_id = ?", (review_id,)).fetchone())


class AttentionTest(ScopeBase):
    def test_in_play_emailed_and_held_companies_ask_for_attention_and_a_plain_one_does_not(self):
        picked, mailed, held, plain = (self.company(n) for n in ("Picked", "Mailed", "Held", "Plain"))
        seed_plan_row(self.conn, picked, TODAY, "picked")
        seed_queued_message(self.conn, seed_email_person(self.conn, mailed), "drafted")
        self.held(held)
        self.assertEqual(attention(self.conn, today=TODAY, planned_days=4), {picked, mailed, held})
        self.assertNotIn(plain, attention(self.conn, today=TODAY, planned_days=4))

    def test_a_hold_on_a_merged_duplicate_holds_the_survivor(self):
        keep, dup = self.company("Keep"), self.company("Dup")
        self.held(dup)
        self.conn.execute("UPDATE company SET merged_into = ?, status = 'removed', remove_reason = 'merged' "
                          "WHERE company_id = ?", (keep, dup))
        self.assertIn(keep, attention(self.conn, today=TODAY, planned_days=4))

    def test_ranked_ids_keeps_the_ranked_order_and_is_empty_without_a_ranking(self):
        with ranked(7, 3, 9) as patched:
            self.assertEqual(ranked_ids(self.conn, 3), [7, 3, 9])
        self.assertEqual(patched.call_args.kwargs["limit"], 3)
        self.assertEqual(ranked_ids(self.conn, 3), [])  # no scores stored


class ClearOutOfScopeTest(ScopeBase):
    def test_clears_out_of_scope_crawler_gaps_at_once_and_keeps_in_play_and_top_ones(self):
        out, top, picked = self.company("Out"), self.company("Top"), self.company("Picked")
        seed_plan_row(self.conn, picked, TODAY, "picked")
        items = {cid: self.crawler_gap(cid) for cid in (out, top, picked)}
        with ranked(top, 999):
            cleared = clear_out_of_scope(self.conn, now=NOW, cfg=CFG)
        self.assertEqual(cleared, 1)
        self.assertEqual(self.state(items[out]), ("resolved", "cleared", OUT_OF_SCOPE_NOTE))
        self.assertEqual(self.state(items[top])[0], "open")
        self.assertEqual(self.state(items[picked])[0], "open")
        event = self.conn.execute("SELECT actor FROM event WHERE entity = 'review_item' AND kind = 'cleared' "
                                  "AND entity_id = ?", (items[out],)).fetchone()
        self.assertEqual(event["actor"], "system")

    def test_the_top_n_is_the_crawler_scope_not_the_sponsorship_one(self):
        first, third = self.company("First"), self.company("Third")
        a, b = self.crawler_gap(first), self.crawler_gap(third)
        with ranked(first, 555, third) as patched:  # crawler_scope_top = 2: the third is out
            clear_out_of_scope(self.conn, now=NOW, cfg=CFG)
        self.assertEqual(patched.call_args.kwargs["limit"], 2)
        self.assertEqual((self.state(a)[0], self.state(b)[0]), ("open", "resolved"))

    def test_never_touches_an_item_he_settled_or_another_producers_or_kinds(self):
        out = self.company("Out")
        settled = self.crawler_gap(out, key="settled")
        resolve_item(self.conn, settled, resolution="marked_ok", note="Marked ok by you", revision=1, now=NOW)
        sender = seed_item(self.conn, "domain_missing", out, raised_by="sender", evidence_key="no-domain")
        board = seed_item(self.conn, "board_empty", out, raised_by="crawler")
        flag = seed_item(self.conn, "flagged", out, raised_by="crawler")
        with ranked(999):
            self.assertEqual(clear_out_of_scope(self.conn, now=NOW, cfg=CFG), 0)
        self.assertEqual(self.state(settled)[:2], ("resolved", "marked_ok"))
        for review_id in (sender, board, flag):
            self.assertEqual(self.state(review_id)[0], "open")

    def test_with_no_ranked_list_nothing_is_cleared(self):
        out = self.company("Out")
        item = self.crawler_gap(out)
        self.assertIsNone(clear_out_of_scope(self.conn, now=NOW, cfg=CFG))
        self.assertEqual(self.state(item)[0], "open")

    def test_company_ids_limit_the_step(self):
        a, b = self.company("A"), self.company("B")
        ia, ib = self.crawler_gap(a), self.crawler_gap(b)
        with ranked(999):
            self.assertEqual(clear_out_of_scope(self.conn, now=NOW, cfg=CFG, company_ids=[a]), 1)
        self.assertEqual((self.state(ia)[0], self.state(ib)[0]), ("resolved", "open"))

    def test_the_sweep_runs_the_step_and_reports_it(self):
        out, top = self.company("Out"), self.company("Top")
        gone, kept = self.crawler_gap(out), self.crawler_gap(top)
        with ranked(top):
            report = sweep_needs_you(self.conn, now=NOW, cfg=CFG)
        self.assertEqual(report.cleared.get("domain_missing"), 1)
        self.assertEqual((self.state(gone)[0], self.state(kept)[0]), ("resolved", "open"))
        again = sweep_needs_you(self.conn, now=NOW)  # no ranking now: held back and said so
        self.assertIn("crawler domain items: no ranked list, so none was cleared for leaving the top 150",
                      again.skipped)


if __name__ == "__main__":
    unittest.main()
