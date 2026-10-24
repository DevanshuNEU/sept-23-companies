import json
import unittest
from unittest import mock

from outreach.review import ReviewError
from outreach.review.fixes import Fix, apply_fix, reopen_item
from outreach.review.items import raise_item
from tests import apiseed as s
from tests.storetest import fresh_store

NOW = "2026-11-10T15:00:00Z"
SOURCE = "Their careers page"


def edit(url, source=SOURCE):
    return Fix("edit", field="board_url", value=url, source=source)


class BoardFixTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.cid = s.company(self.conn, "Acme", "working")
        self.old = s.board(self.conn, self.cid, "ashby", "acmeold", last_read="error", error_streak=3)
        self.posting = s.posting(self.conn, self.cid, "Engineer", board_id=self.old)
        self.closed = s.posting(self.conn, self.cid, "Old role", board_id=self.old, closed_at="2026-11-02T00:00:00Z")
        self.item = self.raise_item("board_unreadable")

    def raise_item(self, kind, board_id=None):
        return raise_item(self.conn, kind=kind, company_id=self.cid, reason=f"About {kind}.", evidence_key="streak",
                          raised_by="crawler", board_id=board_id or self.old, now=NOW).review_id

    def fix(self, fix, review_id=None):
        return apply_fix(self.conn, review_id or self.item, fix, revision=1, now=NOW)

    def refused(self, code, fix, review_id=None):
        with self.assertRaises(ReviewError) as caught:
            self.fix(fix, review_id)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        return caught.exception.message

    def count(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()[0]

    def board(self, ats, token):
        return self.conn.execute("SELECT * FROM board WHERE ats = ? AND token = ?", (ats, token)).fetchone()

    def assert_nothing_changed(self):
        self.assertEqual(self.board("ashby", "acmeold")["active"], 1)
        self.assertEqual(self.count("SELECT count(*) FROM board"), 1)
        self.assertEqual(self.count("SELECT count(*) FROM posting WHERE closed_at IS NULL"), 1)
        row = self.conn.execute("SELECT state, revision FROM review_item WHERE review_id = ?", (self.item,)).fetchone()
        self.assertEqual(tuple(row), ("open", 1))
        self.assertEqual(self.count("SELECT count(*) FROM event WHERE entity = 'board'"), 0)
        self.assertEqual(self.count("SELECT count(*) FROM review_item WHERE kind = 'merge_candidate'"), 0)

    def test_pasted_board_url_adds_a_hand_board_due_now_and_retires_the_old_one(self):
        result = self.fix(edit("https://jobs.lever.co/acme"))
        new = self.board("lever", "acme")
        self.assertEqual((new["company_id"], new["found_by"], new["active"], new["next_read_at"], new["created_at"]),
                         (self.cid, "hand", 1, None, NOW))
        self.assertEqual(self.board("ashby", "acmeold")["active"], 0)
        self.assertEqual(result.resolution, "field_edited")
        self.assertEqual(result.note, "Job board set to lever:acme by you, from Their careers page")
        row = self.conn.execute("SELECT state, resolution, resolution_note FROM review_item WHERE review_id = ?",
                                (self.item,)).fetchone()
        self.assertEqual(tuple(row), ("resolved", "field_edited", result.note))
        found = self.conn.execute("SELECT actor, detail FROM event WHERE entity = 'board' AND kind = 'found'").fetchone()
        self.assertEqual(found["actor"], "devanshu")
        replaced = self.conn.execute("SELECT * FROM event WHERE entity = 'board' AND kind = 'replaced'").fetchone()
        self.assertEqual((replaced["entity_id"], replaced["company_id"], replaced["actor"], replaced["at"]),
                         (self.old, self.cid, "devanshu", NOW))
        detail = json.loads(replaced["detail"])
        self.assertEqual((detail["old"], detail["new"], detail["closed_postings"], detail["source"]),
                         ("ashby:acmeold", "lever:acme", 1, SOURCE))
        resolved = json.loads(self.conn.execute("SELECT detail FROM event WHERE kind = 'resolved' AND entity_id = ?",
                                                (self.item,)).fetchone()[0])
        self.assertEqual((resolved["field"], resolved["value"], resolved["closed_postings"]),
                         ("board_url", "lever:acme", 1))

    def test_an_existing_inactive_board_of_the_company_is_turned_on_and_due_now(self):
        other = s.board(self.conn, self.cid, "lever", "acme", active=0)
        self.conn.execute("UPDATE board SET next_read_at = '2026-12-01T00:00:00Z' WHERE board_id = ?", (other,))
        self.fix(edit("https://jobs.lever.co/acme"))
        row = self.board("lever", "acme")
        self.assertEqual((row["board_id"], row["active"], row["next_read_at"]), (other, 1, None))
        self.assertEqual(self.board("ashby", "acmeold")["active"], 0)

    def test_old_postings_are_closed_with_reason_hand_and_the_new_board_is_active_in_the_same_transaction(self):
        self.fix(edit("https://jobs.lever.co/acme"))
        rows = {r["posting_id"]: tuple(r) for r in self.conn.execute(
            "SELECT posting_id, closed_at, close_reason FROM posting")}
        self.assertEqual(rows[self.posting], (self.posting, NOW, "hand"))
        self.assertEqual(rows[self.closed][2], "gone_from_board")  # an already closed posting keeps its reason
        # A write that fails after the board work leaves nothing: the old board still reads, no new board exists.
        self.setUp()
        with mock.patch("outreach.review.fixes.resolve_item", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.fix(edit("https://jobs.lever.co/acme"))
        self.assert_nothing_changed()

    def test_non_board_url_is_refused_with_the_example(self):
        message = self.refused("bad_request", edit("https://acme.example/jobs"))
        self.assertEqual(message, "That address is not a job board we can read. Paste the board's own address, "
                                  "for example https://jobs.ashbyhq.com/company. Nothing was saved.")
        self.assert_nothing_changed()

    def test_the_same_board_is_refused(self):
        message = self.refused("bad_request", edit("https://jobs.ashbyhq.com/acmeold"))
        self.assertEqual(message, "That is the board we already read. If the board is right, mark ok. "
                                  "Nothing was saved.")
        self.assert_nothing_changed()

    def test_unsupported_host_names_the_host(self):
        message = self.refused("bad_request", edit("https://jobs.jobvite.com/acme/jobs"))
        self.assertIn("jobs.jobvite.com", message)
        self.assertTrue(message.endswith("Nothing was saved."))
        self.assert_nothing_changed()

    def test_a_board_owned_by_another_company_is_a_conflict_and_nothing_changes(self):
        other = s.company(self.conn, "Taken Co")
        s.board(self.conn, other, "greenhouse", "taken")
        message = self.refused("conflict", edit("https://boards.greenhouse.io/taken"))
        self.assertIn(f"Taken Co (#{other})", message)
        self.assertTrue(message.endswith("Nothing was saved."))
        self.assertEqual(self.count("SELECT count(*) FROM board WHERE company_id = ?", self.cid), 1)
        self.assertEqual(self.board("ashby", "acmeold")["active"], 1)
        self.assertEqual(self.count("SELECT count(*) FROM posting WHERE closed_at IS NULL"), 1)
        self.assertEqual(self.count("SELECT count(*) FROM review_item WHERE kind = 'merge_candidate'"), 0)
        self.assertEqual(self.count("SELECT count(*) FROM event WHERE entity = 'board'"), 0)

    def test_a_board_claimed_by_an_alias_of_another_company_is_a_conflict(self):
        from outreach.store import add_alias
        other = s.company(self.conn, "Taken Co")
        add_alias(self.conn, other, "greenhouse:taken", "ats")
        self.refused("conflict", edit("https://boards.greenhouse.io/taken"))
        self.assertEqual(self.count("SELECT count(*) FROM board WHERE token = 'taken'"), 0)
        self.assertEqual(self.count("SELECT count(*) FROM review_item WHERE kind = 'merge_candidate'"), 0)

    def test_a_board_owned_by_a_company_merged_into_this_one_is_allowed(self):
        loser = s.company(self.conn, "Acme Inc")
        s.board(self.conn, loser, "greenhouse", "acmeinc")
        self.conn.execute("UPDATE company SET merged_into = ?, status = 'removed', remove_reason = 'merged' "
                          "WHERE company_id = ?", (self.cid, loser))
        self.fix(edit("https://boards.greenhouse.io/acmeinc"))
        self.assertEqual(self.board("greenhouse", "acmeinc")["active"], 1)
        self.assertEqual(self.board("ashby", "acmeold")["active"], 0)

    def test_the_board_the_item_names_must_belong_to_the_companys_survivor(self):
        other = s.company(self.conn, "Other")
        theirs = s.board(self.conn, other, "ashby", "otherco")
        posting = s.posting(self.conn, other, board_id=theirs)
        item = self.raise_item("board_empty", board_id=theirs)
        self.refused("conflict", edit("https://jobs.lever.co/acme"), item)
        self.assertEqual(self.board("ashby", "otherco")["active"], 1)
        self.assertEqual(self.count("SELECT closed_at IS NULL FROM posting WHERE posting_id = ?", posting), 1)
        self.assertEqual(self.count("SELECT count(*) FROM board WHERE token = 'acme'"), 0)

    def test_a_failed_add_leaves_the_old_board_active(self):
        for outcome in ({"return_value": "conflict"}, {"side_effect": ValueError("bad token")}):
            with self.subTest(outcome=outcome):
                with mock.patch("outreach.review.boardfix.add_board", **outcome):
                    self.refused("conflict" if "return_value" in outcome else "bad_request",
                                 edit("https://jobs.lever.co/acme"))
                self.assert_nothing_changed()

    def test_a_missing_source_is_refused_before_anything_is_written(self):
        self.refused("bad_request", Fix("edit", field="board_url", value="https://jobs.lever.co/acme", source=" "))
        self.assert_nothing_changed()

    def test_the_other_board_item_on_the_same_board_settles_but_one_on_another_board_stays_open(self):
        second = s.board(self.conn, self.cid, "greenhouse", "acmetwo")
        same_board = self.raise_item("board_empty")
        other_board = raise_item(self.conn, kind="board_empty", company_id=self.cid, reason="About it.",
                                 evidence_key="streak", raised_by="crawler", board_id=second, now=NOW).review_id
        result = self.fix(edit("https://jobs.lever.co/acme"))
        self.assertEqual(result.settled, (same_board,))
        states = {r[0]: r[1] for r in self.conn.execute("SELECT review_id, state FROM review_item")}
        self.assertEqual((states[same_board], states[other_board]), ("resolved", "open"))

    def test_board_edit_cannot_be_reopened(self):
        result = self.fix(edit("https://jobs.lever.co/acme"))
        with self.assertRaises(ReviewError) as caught:
            reopen_item(self.conn, self.item, revision=result.revision, now=NOW)
        self.assertEqual(caught.exception.code, "not_allowed")
        self.assertIn("locked", caught.exception.message)
        self.assertEqual(self.board("ashby", "acmeold")["active"], 0)


if __name__ == "__main__":
    unittest.main()
