"""The five Needs-you triggers end to end, and the `needs_you` flow stage (#16 Task 12).

Triggers 1 and 2 are raised by the crawler (`write_board_state`, #21); triggers 3, 4 and 5 by the sweep.
"""
import itertools
import unittest

from outreach.crawl.base import JobRef, ReadResult, Validators
from outreach.crawl.boardstate import load_board, write_board_state
from outreach.review import queries
from outreach.review.fixes import Fix, apply_fix
from outreach.review.items import raise_item
from outreach.review.kinds import KINDS
from outreach.review.triggers import sweep_needs_you
from outreach.rules import rebuild
from outreach.store import start_job
from tests.contracts import check_review_items
from tests.pipeline.harness import PipelineHarness
from tests.pipeline.test_flow import INVARIANTS
from tests.review_seed import seed_email_person, seed_item, seed_person, seed_plan_row, seed_queued_message
from tests.storetest import fresh_store, seed_board, seed_company, seed_posting

NOW = "2026-11-10T15:00:00Z"
TODAY = "2026-11-10"
UNCLEAR = "We may be able to support visa sponsorship for exceptional candidates."
OTHER_UNCLEAR = "Sponsorship may be considered on a case by case basis."
JOB = JobRef("1", "Software Engineer", "https://example.com/1", "Boston, MA", "US", None, None)
OK = ReadResult("ok", (JOB,), Validators("etag-1", "fp-1"), http_status=200)
EMPTY = ReadResult("empty", (), Validators(), http_status=200)
ERROR = ReadResult("error", (), Validators(), http_status=500, error="HTTP 500")


class FlowBase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.reads = itertools.count(1)

    def items(self, kind, where=""):
        return self.conn.execute(f"SELECT * FROM review_item WHERE kind = ? {where} ORDER BY review_id",
                                 (kind,)).fetchall()

    def open_items(self, kind):
        return self.items(kind, "AND state = 'open'")

    def count_items(self):
        return self.conn.execute("SELECT count(*) FROM review_item").fetchone()[0]

    def sweep(self):
        return sweep_needs_you(self.conn, now=NOW)

    def read(self, board_id, result):
        """One crawler read of the board in its own job run, a day after the last."""
        n = next(self.reads)
        board = load_board(self.conn, board_id)
        return write_board_state(self.conn, board, result, run_id=start_job(self.conn, "read_boards"),
                                 now=f"2026-10-{n:02d}T08:00:00Z", open_postings=1,
                                 next_read_at=f"2026-10-{n:02d}T14:00:00Z", interval_s=21600)

    def picked(self, name="Acme"):
        cid = seed_company(self.conn, name)
        seed_plan_row(self.conn, cid, TODAY, "picked")
        return cid

    def unclear(self, body=UNCLEAR):
        cid = self.picked()
        pid = seed_posting(self.conn, cid, "Software Engineer, New Grad", body, "Boston, MA")
        rebuild(self.conn)
        return cid, pid


class TriggerTests(FlowBase):
    def test_trigger_1_board_unreadable_is_raised_by_the_crawler_on_the_third_error(self):
        board = seed_board(self.conn, seed_company(self.conn))
        self.read(board, OK)
        self.read(board, ERROR)
        self.read(board, ERROR)
        self.assertEqual(self.items("board_unreadable"), [])
        self.read(board, ERROR)
        (item,) = self.items("board_unreadable")
        self.assertEqual((item["raised_by"], item["state"], item["board_id"]), ("crawler", "open", board))
        self.assertEqual(item["evidence_key"], load_board(self.conn, board).streak_since)
        check_review_items(self, self.conn)

    def test_trigger_2_board_empty_is_raised_by_the_crawler_on_the_first_empty_read(self):
        h = PipelineHarness()
        self.addCleanup(h.close)
        h.run(through="read_boards")
        board = h.crawl.board_id()
        count = lambda: h.conn.execute("SELECT count(*) FROM review_item WHERE kind = 'board_empty' "
                                       "AND board_id = ?", (board,)).fetchone()[0]
        self.assertEqual(count(), 0)
        h.crawl.run(h.crawl.empty())
        (item,) = h.conn.execute("SELECT * FROM review_item WHERE kind = 'board_empty' AND board_id = ?",
                                 (board,)).fetchall()
        self.assertEqual((item["raised_by"], item["state"]), ("crawler", "open"))
        self.assertEqual(h.conn.execute("SELECT count(*) FROM posting WHERE board_id = ? AND closed_at IS NULL",
                                        (board,)).fetchone()[0] > 0, True)
        self.assertNotIn("twice in a row", queries.display_reason(h.conn, item))
        h.crawl.run(h.crawl.empty())  # the second empty read closes the postings
        self.assertEqual(count(), 1)
        item = h.conn.execute("SELECT * FROM review_item WHERE review_id = ?", (item["review_id"],)).fetchone()
        self.assertIn("twice in a row", queries.display_reason(h.conn, item))
        check_review_items(self, h.conn)

    def test_trigger_3_sponsorship_wording_raises_one_item_for_the_company(self):
        cid, _ = self.unclear()
        seed_posting(self.conn, cid, "Software Engineer, New Grad", UNCLEAR, "Boston, MA")
        rebuild(self.conn)
        report = self.sweep()
        self.assertEqual(report.raised.get("sponsorship_unclear"), 1)
        (item,) = self.items("sponsorship_unclear")
        self.assertEqual((item["company_id"], item["raised_by"], item["state"]), (cid, "rules", "open"))
        self.sweep()
        self.assertEqual(len(self.items("sponsorship_unclear")), 1)
        check_review_items(self, self.conn)

    def test_trigger_4_a_picked_company_with_no_size_raises_size_unknown(self):
        cid = self.picked()
        self.assertEqual(self.sweep().raised.get("size_unknown"), 1)
        (item,) = self.items("size_unknown")
        self.assertEqual((item["company_id"], item["raised_by"], item["evidence_key"]), (cid, "daily_list", "no-size"))
        check_review_items(self, self.conn)

    def test_trigger_5_an_emailing_company_with_no_domain_raises_domain_missing(self):
        cid = seed_company(self.conn, "Acme")
        seed_queued_message(self.conn, seed_email_person(self.conn, cid), "drafted")
        self.assertEqual(self.sweep().raised.get("domain_missing"), 1)
        (item,) = self.items("domain_missing")
        self.assertEqual((item["company_id"], item["raised_by"], item["evidence_key"]), (cid, "sender", "no-domain"))
        check_review_items(self, self.conn)


class SettledOnceTests(FlowBase):
    def settle(self, review_id):
        return apply_fix(self.conn, review_id, Fix("ok"), revision=1, now=NOW)

    def test_each_trigger_item_is_settled_once_and_no_producer_brings_it_back(self):
        self.unclear()
        self.picked("Sized Out")  # trigger 4
        seed_queued_message(self.conn, seed_email_person(self.conn, seed_company(self.conn, "Mailed")), "drafted")
        self.sweep()
        sweep_kinds = ("sponsorship_unclear", "size_unknown", "domain_missing")
        for kind in sweep_kinds:
            self.assertTrue(self.open_items(kind), kind)
        board = seed_board(self.conn, seed_company(self.conn, "Boarded"))
        for result in (ERROR, ERROR, ERROR):
            self.read(board, result)
        empty_board = seed_board(self.conn, seed_company(self.conn, "Emptied"), token="emptied")
        self.read(empty_board, EMPTY)
        self.assertEqual(len(self.open_items("board_unreadable")), 1)
        self.assertEqual(len(self.open_items("board_empty")), 1)
        for kind in sweep_kinds + ("board_unreadable", "board_empty"):
            for item in self.open_items(kind):
                self.settle(item["review_id"])
        total = self.count_items()
        # Every producer runs again on the same evidence.
        self.sweep()
        self.sweep()
        self.read(board, ERROR)  # the same run of errors goes on
        self.read(empty_board, EMPTY)  # the same run of empties goes on
        self.assertEqual(self.count_items(), total)
        for kind in sweep_kinds + ("board_unreadable", "board_empty"):
            self.assertEqual(self.open_items(kind), [], kind)
            self.assertEqual({r["resolution"] for r in self.items(kind)}, {"marked_ok"}, kind)

    def test_changed_evidence_raises_again_and_says_what_changed(self):
        board = seed_board(self.conn, seed_company(self.conn))
        for _ in range(3):
            self.read(board, ERROR)
        (first,) = self.open_items("board_unreadable")
        self.settle(first["review_id"])
        self.read(board, OK)  # the run ends
        for _ in range(3):
            self.read(board, ERROR)  # a new run starts
        (second,) = self.open_items("board_unreadable")
        self.assertNotEqual(second["evidence_key"], first["evidence_key"])
        note = queries.reopened_note(self.conn, second)
        self.assertIn("you marked it ok", note["previous"])
        self.assertIn("a new run of failed reads started", note["changed"])

        cid, pid = self.unclear()
        self.sweep()
        (said,) = self.open_items("sponsorship_unclear")
        self.settle(said["review_id"])
        self.conn.execute("UPDATE posting_body SET body = ? WHERE posting_id = ?", (OTHER_UNCLEAR, pid))
        rebuild(self.conn)
        self.sweep()
        (again,) = self.open_items("sponsorship_unclear")
        self.assertNotEqual(again["evidence_key"], said["evidence_key"])
        self.assertEqual(queries.reopened_note(self.conn, again)["changed"], "the posting wording changed")


class ContractTests(FlowBase):
    def test_every_kind_a_stream_raises_passes_check_review_items(self):
        cid = seed_company(self.conn, "Acme")
        other = seed_company(self.conn, "Acme Inc")
        for kind, spec in KINDS.items():
            for producer in spec.raised_by:
                if kind == "merge_candidate":
                    seed_item(self.conn, kind, cid, raised_by=producer, evidence_key=str(other))
                else:
                    seed_item(self.conn, kind, cid, raised_by=producer)
        # #13: Claude suggests a person no-go (a flagged item that names a person).
        person = seed_person(self.conn, cid)
        seed_item(self.conn, "flagged", cid, raised_by="claude", person_id=person,
                  evidence_key=f"claude-no-go:{person}:agency", reason="Claude thinks this person is an agency.")
        # #17: the import's flagged item.
        seed_item(self.conn, "flagged", cid, raised_by="import", evidence_key="import:ruled_out:42",
                  reason="The old dashboard ruled this company out.")
        check_review_items(self, self.conn)
        self.assertGreater(self.count_items(), len(KINDS))

    def test_the_check_fails_on_a_row_a_producer_may_not_write(self):
        cid = seed_company(self.conn, "Acme")
        other = seed_company(self.conn, "Acme Inc")
        board_company = seed_company(self.conn, "Elsewhere")
        cases = {
            "raised_by not named by the kind": lambda: seed_item(self.conn, "reader_disagrees", cid,
                                                                  raised_by="ranker"),
            "merge question with the higher id first": lambda: seed_item(self.conn, "merge_candidate", other,
                                                                         raised_by="source", evidence_key=str(cid)),
            "merge question with a key that is no id": lambda: seed_item(self.conn, "merge_candidate", cid,
                                                                         raised_by="source", evidence_key="abc"),
            "board item on another company's board": lambda: seed_item(
                self.conn, "board_empty", cid, raised_by="crawler",
                board_id=seed_board(self.conn, board_company, token="theirs")),
        }
        for name, make in cases.items():
            with self.subTest(name):
                make()
                with self.assertRaises(AssertionError):
                    check_review_items(self, self.conn)
                self.conn.execute("DELETE FROM review_item")
        with self.subTest("a dash in the reason"):
            item = seed_item(self.conn, "flagged", cid, raised_by="claude")
            self.conn.execute("UPDATE review_item SET reason = ? WHERE review_id = ?", (f"a {chr(0x2014)} b", item))
            with self.assertRaises(AssertionError):
                check_review_items(self, self.conn)
        with self.subTest("a blank evidence key"):
            self.conn.execute("DELETE FROM review_item")
            item = seed_item(self.conn, "flagged", cid, raised_by="claude")
            self.conn.execute("UPDATE review_item SET evidence_key = ' ' WHERE review_id = ?", (item,))
            with self.assertRaises(AssertionError):
                check_review_items(self, self.conn)


class StageTests(unittest.TestCase):
    def test_flow_needs_you_stage_after_plan(self):
        h = PipelineHarness()
        self.addCleanup(h.close)
        names = [name for name, _ in h.stages]
        self.assertEqual(names[-1], "needs_you", "the sweep runs last, on what every stage left")
        self.assertLess(names.index("rank"), names.index("needs_you"))
        h.run()
        beat = h.conn.execute("SELECT actor, detail FROM event WHERE entity = 'review_item' AND kind = 'sweep_run'"
                              ).fetchall()
        self.assertEqual(len(beat), 1)
        self.assertEqual(beat[0]["actor"], "system")
        self.assertIn("needs_you", INVARIANTS)
        INVARIANTS["needs_you"](h)
        check_review_items(self, h.conn)


if __name__ == "__main__":
    unittest.main()
