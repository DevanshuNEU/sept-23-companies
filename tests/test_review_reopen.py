import json
import unittest

from outreach.review import ReviewError
from outreach.review.config import ReviewConfig
from outreach.review.fixes import Fix, apply_fix, can_undo, reopen_item
from outreach.review.items import clear_item, raise_item
from outreach.review.triggers import sweep_needs_you
from tests import apiseed as s
from tests.review_seed import seed_email_person, seed_plan_row, seed_queued_message
from tests.storetest import fresh_store

NOW = "2026-11-10T15:00:00Z"
LATER = "2026-11-10T16:00:00Z"
TODAY = "2026-11-10"
CFG = ReviewConfig(sponsorship_top_n=150, planned_days=4, stale_days=14)


class ReopenBase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.cid = s.company(self.conn, "Acme", "working")

    def item(self, kind="flagged", company_id=None, key="k", raised_by="claude", **kw):
        return raise_item(self.conn, kind=kind, company_id=company_id or self.cid, reason=f"About {kind}.",
                          evidence_key=key, raised_by=raised_by, now=NOW, **kw).review_id

    def fix(self, rid, fix, revision=1):
        return apply_fix(self.conn, rid, fix, revision=revision, now=NOW)

    def reopen(self, rid, revision):
        return reopen_item(self.conn, rid, revision=revision, now=LATER)

    def row(self, rid):
        return self.conn.execute("SELECT * FROM review_item WHERE review_id = ?", (rid,)).fetchone()

    def refused(self, code, rid, revision):
        with self.assertRaises(ReviewError) as caught:
            self.reopen(rid, revision)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        return caught.exception.message

    def no_gos(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM no_go ORDER BY no_go_id")]


class ReopenTest(ReopenBase):
    def test_reopen_an_ok_item_puts_it_back_open_with_a_higher_revision(self):
        rid = self.item()
        self.fix(rid, Fix("ok"))
        self.assertTrue(can_undo(self.conn, rid))
        result = self.reopen(rid, 2)
        self.assertEqual((result.review_id, result.revision, result.resolution, result.company_id),
                         (rid, 3, "open", self.cid))
        row = self.row(rid)
        self.assertEqual((row["state"], row["resolution"], row["resolved_at"], row["resolution_note"], row["revision"]),
                         ("open", None, None, "", 3))
        (event,) = self.conn.execute("SELECT * FROM event WHERE entity = 'review_item' AND kind = 'reopened'").fetchall()
        self.assertEqual((event["entity_id"], event["actor"], event["company_id"], event["idempotency_key"]),
                         (rid, "devanshu", self.cid, f"review-reopened:{rid}:3"))
        self.assertEqual(json.loads(event["detail"])["previous_resolution"], "marked_ok")
        self.assertFalse(can_undo(self.conn, rid))

    def test_reopen_a_no_go_lifts_the_row_it_added(self):
        rid = self.item()
        others = [self.item("size_unknown", key="no-size", raised_by="daily_list"),
                  self.item("domain_missing", key="no-domain", raised_by="sender")]
        self.fix(rid, Fix("no_go", reason="agency", source="Their site"))
        for other in others:
            self.assertEqual(self.row(other)["resolution"], "cleared")
        self.reopen(rid, 2)
        (row,) = self.no_gos()
        self.assertEqual((row["lifted_at"], row["lift_reason"]), (LATER, "Undone from Needs you"))
        self.assertEqual(self.row(rid)["state"], "open")
        for other in others:  # what the no-go settled comes back with it
            self.assertEqual(self.row(other)["state"], "open")

    def test_reopen_a_no_go_keeps_a_row_that_was_already_there(self):
        first = s._insert(self.conn, "no_go", {"company_id": self.cid, "reason": "not_a_fit", "source": "devanshu",
                                               "created_at": s.T0})
        rid = self.item()
        self.fix(rid, Fix("no_go", reason="agency", source="Their site"))
        before = self.no_gos()
        self.reopen(rid, 2)
        self.assertEqual(self.no_gos(), before)
        self.assertIsNone(self.no_gos()[0]["lifted_at"])
        self.assertEqual(self.no_gos()[0]["no_go_id"], first)
        self.assertEqual(self.row(rid)["state"], "open")

    def test_reopen_a_person_no_go_lifts_the_person_row(self):
        person = s.person(self.conn, self.cid, "Pat Lee")
        rid = self.item(person_id=person, key=f"claude-no-go:{person}:said_no")
        self.fix(rid, Fix("no_go", source="Their reply"))
        self.reopen(rid, 2)
        (row,) = self.no_gos()
        self.assertEqual((row["person_id"], row["lift_reason"]), (person, "Undone from Needs you"))

    def test_undoing_one_of_two_no_go_confirmations_keeps_the_row_the_other_relies_on(self):
        person = s.person(self.conn, self.cid, "Pat Lee")
        first = self.item(person_id=person, key=f"claude-no-go:{person}:said_no")
        second = self.item(person_id=person, key=f"claude-no-go:{person}:stop_reply")
        self.fix(first, Fix("no_go", source="Their reply"))
        self.fix(second, Fix("no_go", source="Their second reply"))
        self.assertTrue(can_undo(self.conn, first))
        self.reopen(first, 2)
        (row,) = self.no_gos()
        self.assertIsNone(row["lifted_at"])  # the second confirmation still stands
        self.assertEqual(self.row(first)["state"], "open")
        self.reopen(second, 2)  # the last confirmation taken back: the row the first one added is lifted
        (row,) = self.no_gos()
        self.assertEqual(row["lift_reason"], "Undone from Needs you")

    def test_undoing_a_no_go_keeps_the_row_when_an_api_settled_item_names_the_same_person(self):
        person = s.person(self.conn, self.cid, "Pat Lee")
        first = self.item(person_id=person, key=f"claude-no-go:{person}:said_no")
        self.fix(first, Fix("no_go", source="Their reply"))
        api_item = s._insert(self.conn, "review_item", {
            "kind": "flagged", "company_id": self.cid, "person_id": person, "reason": "Pat again.",
            "evidence_key": "api", "raised_by": "claude", "state": "resolved", "resolution": "marked_no_go",
            "resolved_at": NOW, "created_at": NOW})
        s._insert(self.conn, "event", {
            "at": NOW, "recorded_at": NOW, "entity": "review_item", "entity_id": api_item, "company_id": self.cid,
            "kind": "review_resolved", "actor": "devanshu",
            "detail": json.dumps({"fix": "no_go", "no_go_created": False, "person_id": person})})
        self.reopen(first, 2)
        self.assertIsNone(self.no_gos()[0]["lifted_at"])

    def test_reopen_a_merge_unmerges_and_reopens(self):
        b = s.company(self.conn, "Acme Labs", "backlog")
        rid = self.item("merge_candidate", key=str(b), raised_by="source")
        lost_item = self.item("size_unknown", company_id=b, key="no-size", raised_by="daily_list")
        before = dict(self.conn.execute("SELECT * FROM company WHERE company_id = ?", (b,)).fetchone())
        self.fix(rid, Fix("merge", keep="a"))
        self.assertTrue(can_undo(self.conn, rid))
        self.reopen(rid, 2)
        after = dict(self.conn.execute("SELECT * FROM company WHERE company_id = ?", (b,)).fetchone())
        for key in ("revision", "status_at"):
            before.pop(key), after.pop(key)
        self.assertEqual(after, before)
        self.assertEqual(self.row(rid)["state"], "open")
        self.assertEqual(self.row(lost_item)["state"], "open")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM event WHERE kind = 'unmerged'").fetchone()[0], 1)

    def test_reopen_a_merge_already_unmerged_from_the_company_page_only_reopens(self):
        from outreach.review.merge import unmerge_company
        b = s.company(self.conn, "Acme Labs")
        rid = self.item("merge_candidate", key=str(b), raised_by="source")
        self.fix(rid, Fix("merge", keep="a"))
        unmerge_company(self.conn, b, revision=2, now=NOW)
        self.reopen(rid, 2)
        self.assertEqual(self.row(rid)["state"], "open")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM event WHERE kind = 'unmerged'").fetchone()[0], 1)

    def test_undo_of_an_older_merge_is_refused_when_the_company_was_merged_again_since(self):
        from outreach.review.merge import unmerge_company
        b = s.company(self.conn, "Acme Labs")
        first = self.item("merge_candidate", key=str(b), raised_by="source")
        self.fix(first, Fix("merge", keep="a"))  # B into A
        unmerge_company(self.conn, b, revision=2, now=NOW)  # from the company page; the first stays resolved
        self.conn.execute("UPDATE company SET status = 'closed', close_reason = 'not_now', revision = revision + 1 "
                          "WHERE company_id = ?", (b,))
        self.conn.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?",
                          (self.cid,))  # a closed company merges only into one that stops mail too
        second = self.item("merge_candidate", company_id=b, key=str(self.cid), raised_by="source")
        self.fix(second, Fix("merge", keep="b"))  # B into A again, from another question
        self.assertFalse(can_undo(self.conn, first))
        message = self.refused("not_allowed", first, 2)
        self.assertIn("merged again", message)
        lost = self.conn.execute("SELECT merged_into, status FROM company WHERE company_id = ?", (b,)).fetchone()
        self.assertEqual(tuple(lost), (self.cid, "removed"))
        self.assertEqual((self.row(first)["state"], self.row(second)["state"]), ("resolved", "resolved"))
        # The later merge is the one to take back, and it restores what that merge recorded.
        self.assertTrue(can_undo(self.conn, second))
        self.reopen(second, 2)
        lost = self.conn.execute("SELECT merged_into, status, close_reason FROM company WHERE company_id = ?",
                                 (b,)).fetchone()
        self.assertEqual(tuple(lost), (None, "closed", "not_now"))

    def test_a_second_tab_reopening_after_the_first_gets_a_conflict(self):
        rid = self.item()
        self.fix(rid, Fix("ok"))
        self.reopen(rid, 2)  # the first tab
        self.assertIn("changed since you loaded it", self.refused("conflict", rid, 2))  # the second, on what it loaded

    def test_reopen_an_edit_is_not_allowed_with_the_lock_message(self):
        rid = self.item("size_unknown", key="no-size", raised_by="daily_list")
        self.fix(rid, Fix("edit", field="size", value="45", source="LinkedIn"))
        self.assertEqual(self.refused("not_allowed", rid, 2),
                         "A value you set is locked and cannot be taken back here.")
        self.assertEqual(self.row(rid)["state"], "resolved")

    def test_reopen_twice_or_stale_is_refused(self):
        rid = self.item()
        self.fix(rid, Fix("ok"))
        self.refused("conflict", rid, 1)
        self.assertEqual(self.row(rid)["state"], "resolved")
        self.reopen(rid, 2)
        self.refused("not_allowed", rid, 3)  # open now: nothing to take back
        self.refused("not_found", 999, 1)

    def test_a_reopened_item_survives_the_next_sweep_while_its_condition_holds(self):
        seed_plan_row(self.conn, self.cid, TODAY, "picked")
        sweep_needs_you(self.conn, now=NOW, cfg=CFG)
        (rid,) = [r[0] for r in self.conn.execute("SELECT review_id FROM review_item WHERE kind = 'size_unknown'")]
        self.fix(rid, Fix("ok"))
        sweep_needs_you(self.conn, now=NOW, cfg=CFG)
        self.assertEqual(self.row(rid)["state"], "resolved")
        self.reopen(rid, 2)
        sweep_needs_you(self.conn, now=LATER, cfg=CFG)
        self.assertEqual(self.row(rid)["state"], "open")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM review_item").fetchone()[0], 1)

    def test_can_undo_is_false_for_edits_cleared_items_and_a_no_go_that_was_lifted_since(self):
        edited = self.item("size_unknown", key="no-size", raised_by="daily_list")
        self.fix(edited, Fix("edit", field="size", value="45", source="LinkedIn"))
        cleared = self.item(key="c")
        clear_item(self.conn, cleared, "Went away.", NOW)
        still_open = self.item(key="o")
        other = s.company(self.conn, "Other")
        lifted = self.item(company_id=other, key="l")
        self.fix(lifted, Fix("no_go", reason="agency", source="x"))
        self.conn.execute("UPDATE no_go SET lifted_at = ?, lift_reason = 'Changed my mind' WHERE company_id = ?",
                          (NOW, other))
        for rid in (edited, cleared, still_open, lifted):
            self.assertFalse(can_undo(self.conn, rid), rid)
        self.assertFalse(can_undo(self.conn, 999))
        self.refused("not_allowed", cleared, 2)
        self.refused("not_allowed", lifted, 2)
        self.assertEqual(self.no_gos()[0]["lift_reason"], "Changed my mind")

    def test_reopen_changes_no_message(self):
        for state in ("planned", "drafted", "approved"):
            seed_queued_message(self.conn, seed_email_person(self.conn, self.cid), state)
        b = s.company(self.conn, "Acme Labs")
        seed_queued_message(self.conn, seed_email_person(self.conn, b), "approved")
        before = [tuple(r) for r in self.conn.execute("SELECT * FROM message ORDER BY message_id")]
        ok, no_go = self.item(key="1"), self.item(key="2")
        merge = self.item("merge_candidate", key=str(b), raised_by="source")
        self.fix(ok, Fix("ok"))
        self.fix(merge, Fix("merge", keep="a"))
        self.fix(no_go, Fix("no_go", reason="agency", source="x"))
        for rid in (ok, no_go, merge):
            self.reopen(rid, 2)
        self.assertEqual([tuple(r) for r in self.conn.execute("SELECT * FROM message ORDER BY message_id")], before)


if __name__ == "__main__":
    unittest.main()
