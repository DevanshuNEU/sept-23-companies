import dataclasses
import hashlib
import itertools
import unittest
from types import SimpleNamespace
from unittest import mock

from outreach.review import words
from outreach.review.items import resolve_item
from outreach.review.triggers import SyncResult, sync_sponsorship
from outreach.rules import Decision, rebuild
from outreach.store.timefmt import utcnow
from tests.review_seed import seed_plan_row
from tests.storetest import fresh_store, seed_board, seed_company, seed_posting

NOW = "2026-11-10T15:00:00Z"
TODAY = "2026-11-10"
UNCLEAR = "We may be able to support visa sponsorship for exceptional candidates."
OTHER_UNCLEAR = "Sponsorship may be considered on a case by case basis."
YES = "We sponsor H-1B visas for the right candidate."
OPT_ONLY = "STEM OPT is supported but we do not sponsor H-1B."
TITLE = "Software Engineer, New Grad"


def key_of(quote: str) -> str:
    return hashlib.sha256(" ".join(quote.lower().split()).encode()).hexdigest()[:16]


class SponsorshipTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def picked(self, name="Acme"):
        cid = seed_company(self.conn, name)
        seed_plan_row(self.conn, cid, TODAY, "picked")
        return cid

    def post(self, cid, body=UNCLEAR, title=TITLE, board=None):
        pid = seed_posting(self.conn, cid, title, body, "Boston, MA")
        if board is not None:
            self.conn.execute("UPDATE posting SET board_id = ? WHERE posting_id = ?", (board, pid))
        return pid

    def decide(self):
        rebuild(self.conn)

    def sync(self, **kw):
        return sync_sponsorship(self.conn, now=NOW, **kw)

    def items(self, where=""):
        return self.conn.execute("SELECT * FROM review_item WHERE kind = 'sponsorship_unclear' " + where +
                                 " ORDER BY review_id").fetchall()

    def marks(self):
        return tuple(tuple(self.conn.execute(f"SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM {t}").fetchone()[:])
                     for t in ("review_item", "event"))

    def ready(self, body=UNCLEAR, title=TITLE, name="Acme"):
        cid = self.picked(name)
        pid = self.post(cid, body, title)
        self.decide()
        return cid, pid

    def test_unclear_posting_at_a_picked_company_raises_one_item_with_its_posting_and_key(self):
        cid, pid = self.ready(name="Retell")
        self.assertEqual(self.sync(), SyncResult(raised=1, cleared=0))
        (item,) = self.items()
        self.assertEqual((item["company_id"], item["posting_id"], item["raised_by"], item["state"]),
                         (cid, pid, "rules", "open"))
        self.assertEqual(item["evidence_key"], key_of(UNCLEAR))
        self.assertEqual(item["reason"], words.sponsorship_reason("Retell", TITLE))

    def test_two_unclear_postings_with_one_wording_make_one_item_on_the_newer_posting(self):
        cid = self.picked()
        self.post(cid)
        newer = self.post(cid)
        self.decide()
        self.assertEqual(self.sync(), SyncResult(1, 0))
        (item,) = self.items()
        self.assertEqual(item["posting_id"], newer)

    def test_the_same_wording_on_a_new_posting_does_not_return_after_he_marked_it_ok(self):
        cid, _ = self.ready()
        self.sync()
        resolve_item(self.conn, self.items()[0]["review_id"], resolution="marked_ok", note="ok", revision=1)
        self.post(cid, "  " + UNCLEAR.upper() + "  ")  # the same words in another case and spacing
        self.decide()
        self.assertEqual(self.sync(), SyncResult(0, 0))
        self.assertEqual(len(self.items()), 1)

    def test_a_changed_wording_raises_again_and_reopened_note_says_the_wording_changed(self):
        cid, pid = self.ready()
        self.sync()
        first = self.items()[0]
        resolve_item(self.conn, first["review_id"], resolution="marked_ok", note="ok", revision=1)
        self.conn.execute("UPDATE posting_body SET body = ? WHERE posting_id = ?", (OTHER_UNCLEAR, pid))
        self.decide()
        self.assertEqual(self.sync(), SyncResult(1, 0))
        old, new = self.items()
        self.assertEqual((old["state"], old["resolution"]), ("resolved", "marked_ok"))
        self.assertEqual((new["state"], new["evidence_key"]), ("open", key_of(OTHER_UNCLEAR)))
        self.assertNotEqual(old["evidence_key"], new["evidence_key"])
        try:
            from outreach.review import queries
        except ImportError:  # Task 9 adds it
            return
        self.assertIn("wording", queries.reopened_note(self.conn, new["review_id"])["changed"])

    def test_company_out_of_scope_gets_no_item(self):
        ids = []
        for n in range(400):
            cid = seed_company(self.conn, f"Co {n}")
            self.post(cid)
            ids.append(cid)
        self.decide()
        self.assertEqual(self.sync(), SyncResult(0, 0))
        self.assertEqual(self.items(), [])
        top = [SimpleNamespace(company_id=c) for c in ids[:3]]
        with mock.patch("outreach.ranking.scores.ranked_list", return_value=top) as ranked:
            self.assertEqual(self.sync(), SyncResult(3, 0))
        ranked.assert_called_once()
        self.assertEqual(ranked.call_args.kwargs["limit"], 150)
        self.assertEqual(sorted(r["company_id"] for r in self.items()), ids[:3])

    def test_a_company_with_a_sponsoring_posting_gets_no_item(self):
        for body in (YES, OPT_ONLY):
            cid = self.picked(f"Sponsor {body[:6]}")
            self.post(cid)
            self.post(cid, body)
        self.decide()
        self.assertEqual(self.sync(), SyncResult(0, 0))

    def test_a_sponsoring_posting_that_arrives_later_clears_the_item(self):
        cid, _ = self.ready()
        self.sync()
        self.post(cid, YES)
        self.decide()
        self.assertEqual(self.sync(), SyncResult(0, 1))
        self.assertEqual(self.items()[0]["resolution_note"], "The wording is no longer unclear here.")

    def test_a_posting_waiting_in_the_read_queue_is_left_to_the_reader(self):
        cid, pid = self.ready()
        self.conn.execute("INSERT INTO read_queue(posting_id, body_sha256, reader_version, priority, enqueued_at) "
                          "VALUES (?, ?, 'rv-1', 1, ?)", (pid, "a" * 64, utcnow()))
        self.assertEqual(self.sync(), SyncResult(0, 0))
        self.conn.execute("UPDATE read_queue SET state = 'done'")
        self.assertEqual(self.sync(), SyncResult(1, 0))

    def test_a_posting_on_a_doubtful_board_is_skipped(self):
        cid = self.picked()
        board = seed_board(self.conn, cid)
        self.post(cid, board=board)
        self.decide()
        with mock.patch("outreach.ranking.common.doubtful_board_ids", return_value=frozenset({board})):
            self.assertEqual(self.sync(), SyncResult(0, 0))
        self.assertEqual(self.sync(), SyncResult(1, 0))

    def test_a_locked_sponsorship_fact_suppresses_and_clears(self):
        cid, _ = self.ready()
        other = self.picked("Other")
        self.post(other)
        self.decide()
        self.conn.execute("INSERT INTO evidence(company_id, fact, value, confidence, locked, source_kind, "
                          "recorded_at) VALUES (?, 'sponsorship', 'no', 'confirmed', 1, 'hand', ?)", (other, utcnow()))
        self.assertEqual(self.sync(), SyncResult(1, 0))
        self.assertEqual([r["company_id"] for r in self.items()], [cid])
        self.conn.execute("INSERT INTO evidence(company_id, fact, value, confidence, locked, source_kind, "
                          "recorded_at) VALUES (?, 'sponsorship', 'yes', 'confirmed', 1, 'hand', ?)", (cid, utcnow()))
        self.assertEqual(self.sync(), SyncResult(0, 1))

    def test_an_unlocked_sponsorship_fact_does_not_suppress(self):
        cid, _ = self.ready()
        self.conn.execute("INSERT INTO evidence(company_id, fact, value, confidence, source_kind, recorded_at) "
                          "VALUES (?, 'sponsorship', 'yes', 'estimate', 'posting', ?)", (cid, utcnow()))
        self.assertEqual(self.sync(), SyncResult(1, 0))

    def test_closed_posting_clears_the_item(self):
        cid, pid = self.ready()
        self.sync()
        self.conn.execute("UPDATE posting SET closed_at = ?, close_reason = 'hand' WHERE posting_id = ?", (NOW, pid))
        self.assertEqual(self.sync(), SyncResult(0, 1))
        (item,) = self.items()
        self.assertEqual((item["state"], item["resolution"]), ("resolved", "cleared"))

    def test_ruleset_not_current_skips_and_clears_nothing(self):
        cid, _ = self.ready()
        self.sync()
        self.conn.execute("UPDATE ruleset SET is_current = 0")
        self.conn.execute("INSERT INTO ruleset(ruleset_version, config_toml, created_at, is_current) "
                          "VALUES ('r-0123456789ab', '', ?, 1)", (NOW,))
        before = self.marks()
        result = self.sync()
        self.assertEqual(result, SyncResult(0, 0, "ruleset not current"))
        self.assertEqual(self.items()[0]["state"], "open")
        self.assertEqual(self.marks(), before)

    def test_no_current_ruleset_skips_too(self):
        self.picked()
        self.conn.execute("UPDATE ruleset SET is_current = 0")
        self.assertEqual(self.sync(), SyncResult(0, 0, "ruleset not current"))

    def test_a_current_ruleset_with_no_decisions_skips_and_clears_nothing(self):
        cid, _ = self.ready()
        self.sync()
        self.conn.execute("DELETE FROM decision")
        result = self.sync()
        self.assertEqual((result.raised, result.cleared), (0, 0))
        self.assertIsNotNone(result.skipped)
        self.assertEqual(self.items()[0]["state"], "open")

    def test_second_run_writes_nothing(self):
        self.ready()
        self.sync()
        before = self.marks()
        self.assertEqual(self.sync(), SyncResult(0, 0))
        self.assertEqual(self.marks(), before)

    def test_intern_lane_posting_counts_and_off_lane_does_not(self):
        intern, off = self.picked("Intern Co"), self.picked("Sales Co")
        self.post(intern, title="Software Engineering Intern")
        self.post(off, title="Account Executive")
        self.decide()
        self.assertEqual(self.sync(), SyncResult(1, 0))
        self.assertEqual([r["company_id"] for r in self.items()], [intern])

    def test_ranking_that_returns_nothing_does_not_clear_items_of_companies_out_of_play(self):
        cid, _ = self.ready()
        self.sync()
        self.conn.execute("DELETE FROM daily_list_entry")
        self.assertEqual(self.sync(), SyncResult(0, 0))
        self.assertEqual(self.items()[0]["state"], "open")
        with mock.patch("outreach.ranking.scores.ranked_list", return_value=[SimpleNamespace(company_id=cid + 99)]):
            self.assertEqual(self.sync(), SyncResult(0, 1))

    def test_company_ids_limits_the_sync(self):
        a, _ = self.ready(name="A")
        b = self.picked("B")
        self.post(b)
        self.decide()
        self.assertEqual(self.sync(company_ids=[b]), SyncResult(1, 0))
        self.assertEqual([r["company_id"] for r in self.items()], [b])
        self.assertEqual(self.sync(company_ids=[a]), SyncResult(1, 0))

    def test_visible_matches_rankings_posting_visible(self):
        from outreach.ranking.hide import posting_visible
        from outreach.review.triggers import _visible
        base = Decision(lane="engineering", level_tier="new_grad", level_ok=True, level_basis="x", years_asked=None,
                        years_quote="", us="yes", sponsorship="unclear", sponsorship_quote="q", citizenship=False,
                        citizenship_quote="", eligibility="ok", eligibility_reason=None, eligibility_quote="",
                        start_2027=False)
        for lane, us, spons, cit, elig in itertools.product(
                ("engineering", "intern", "off_lane", "not_engineering", "general_application"),
                ("yes", "no", "unknown"), ("yes", "no", "opt_only", "unclear", "silent"), (False, True),
                ("ok", "excluded")):
            d = dataclasses.replace(base, lane=lane, us=us, sponsorship=spons, citizenship=cit, eligibility=elig,
                                    eligibility_reason=None if elig == "ok" else "grad_window",
                                    eligibility_quote="" if elig == "ok" else "q")
            self.assertEqual(_visible(d), posting_visible(d), (lane, us, spons, cit, elig))


if __name__ == "__main__":
    unittest.main()
