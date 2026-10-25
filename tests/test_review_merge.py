import json
import unittest

from outreach.review import ReviewError, words
from outreach.review.config import ReviewConfig
from outreach.review.fixes import Fix, apply_fix, reopen_item
from outreach.review.items import raise_item
from outreach.review.merge import MergeCompare, MergeSide, merge_companies, merge_compare, unmerge_company
from outreach.review.triggers import sweep_needs_you
from outreach.store import mark_shared_key, name_key
from tests import apiseed as s
from tests.storetest import fresh_store

NOW = "2026-11-10T15:00:00Z"
LATER = "2026-11-10T16:00:00Z"
CFG = ReviewConfig(sponsorship_top_n=150, planned_days=4, stale_days=14)

try:
    from outreach.identity import raise_merge_candidate, resolve_candidate
    from outreach.sources.base import Candidate
    HAVE_IDENTITY = True
except ImportError:  # pragma: no cover
    HAVE_IDENTITY = False


class MergeBase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def pair(self, a_name="Bolt", b_name="Bolt Labs", a_cols=None, **b_cols):
        a = s.company(self.conn, a_name, **(a_cols or {"status": "working"}))
        b = s.company(self.conn, b_name, **b_cols)
        return a, b, self.question(a, b)

    def question(self, a, b, reason=None):
        low, high = sorted((a, b))
        return raise_item(self.conn, kind="merge_candidate", company_id=low, evidence_key=str(high),
                          reason=reason or f"Maybe one company: #{low} and #{high}.", raised_by="source",
                          now=NOW).review_id

    def company(self, cid):
        return self.conn.execute("SELECT * FROM company WHERE company_id = ?", (cid,)).fetchone()

    def item(self, rid):
        return self.conn.execute("SELECT * FROM review_item WHERE review_id = ?", (rid,)).fetchone()

    def open_pair(self, x, y):
        low, high = sorted((x, y))
        return self.conn.execute("SELECT * FROM review_item WHERE kind = 'merge_candidate' AND company_id = ? "
                                 "AND evidence_key = ? AND state = 'open'", (low, str(high))).fetchone()

    def merged_event(self, lose_id):
        return self.conn.execute("SELECT * FROM event WHERE entity = 'company' AND kind = 'merged' AND entity_id = ? "
                                 "ORDER BY event_id DESC", (lose_id,)).fetchone()

    def refused(self, code, fn, *args, **kw):
        with self.assertRaises(ReviewError) as caught:
            fn(*args, **kw)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        return caught.exception.message


class CompareTest(MergeBase):
    def test_merge_compare_reads_both_sides_and_suggests_the_one_in_use(self):
        a, b, item = self.pair(a_cols={"status": "candidate"}, status="working")
        s.evidence(self.conn, a, "domain", "bolt.io")
        s.evidence(self.conn, b, "size", "11-50", low=11, high=50, confidence="estimate")
        s.evidence(self.conn, b, "stage", "seed")
        s.alias(self.conn, b, "Bolt Labs Inc")
        s.board(self.conn, b, "ashby", "boltlabs")
        s.posting(self.conn, b)
        s.touch(self.conn, s.person(self.conn, b, "Pat"))
        s.person(self.conn, a, "Ann")
        s.person(self.conn, a, "Abe")
        got = merge_compare(self.conn, item)
        self.assertIsInstance(got, MergeCompare)
        self.assertEqual(got.a, MergeSide(a, "Bolt", "bolt.io", None, None, (), (), 0, 2, 0, "candidate"))
        self.assertEqual(got.b, MergeSide(b, "Bolt Labs", None, "11-50", "seed", ("Bolt Labs Inc",),
                                          ("ashby:boltlabs",), 1, 1, 1, "working"))
        self.assertEqual((got.why, got.suggested_keep, got.warning), (self.item(item)["reason"], "b", None))

    def test_suggested_keep_falls_back_to_people_then_the_lower_id(self):
        a, b, item = self.pair()
        self.assertEqual(merge_compare(self.conn, item).suggested_keep, "a")
        s.person(self.conn, b)
        self.assertEqual(merge_compare(self.conn, item).suggested_keep, "b")

    def test_merge_compare_is_only_for_a_merge_question(self):
        cid = s.company(self.conn)
        rid = raise_item(self.conn, kind="flagged", company_id=cid, reason="x.", evidence_key="k",
                         raised_by="claude").review_id
        self.refused("not_allowed", merge_compare, self.conn, rid)
        self.refused("not_found", merge_compare, self.conn, 999)

    def test_merge_confirm_warns_on_a_tie_company(self):
        a, b, item = self.pair()
        s.event(self.conn, b, "created", s.T0, actor="source", detail={"tie": True})
        self.assertEqual(merge_compare(self.conn, item).warning,
                         "This record collects name-only mentions that could be either company.")


class MergeTest(MergeBase):
    def test_merge_sets_merged_into_removes_and_records_what_it_cleared(self):
        a, b, item = self.pair(a_cols={"status": "working", "paused_at": "2026-11-01T12:00:00Z"},
                               status="working", paused_at="2026-11-02T12:00:00Z", pause_note="Wait for Q1")
        merge_companies(self.conn, keep_id=a, lose_id=b, review_id=item, now=NOW)
        lost = self.company(b)
        self.assertEqual((lost["merged_into"], lost["status"], lost["remove_reason"], lost["close_reason"],
                          lost["paused_at"], lost["pause_note"], lost["revision"], lost["status_at"]),
                         (a, "removed", "merged", None, None, "", 2, NOW))
        event = self.merged_event(b)
        detail = json.loads(event["detail"])
        self.assertEqual((event["actor"], event["company_id"], event["at"]), ("devanshu", b, NOW))
        self.assertEqual({k: detail[k] for k in ("previous_status", "previous_close_reason", "previous_paused_at",
                                                 "previous_pause_note", "kept_id", "review_id")},
                         {"previous_status": "working", "previous_close_reason": None,
                          "previous_paused_at": "2026-11-02T12:00:00Z", "previous_pause_note": "Wait for Q1",
                          "kept_id": a, "review_id": item})
        alias = self.conn.execute("SELECT company_id, source FROM company_alias WHERE alias_key = 'boltlabs'").fetchone()
        self.assertEqual(tuple(alias), (a, "merge"))

    def test_a_closed_loser_keeps_its_close_reason_in_the_event(self):
        a, b, item = self.pair(a_cols={"status": "closed", "close_reason": "other"}, status="closed",
                               close_reason="not_now")
        merge_companies(self.conn, keep_id=a, lose_id=b, now=NOW)
        detail = json.loads(self.merged_event(b)["detail"])
        self.assertEqual((detail["previous_status"], detail["previous_close_reason"]), ("closed", "not_now"))
        self.assertIsNone(self.company(b)["close_reason"])

    def test_loser_name_alias_is_not_an_error_and_moves_nothing(self):
        a, b, item = self.pair()
        s.alias(self.conn, b, "Bolt Labs", source="name")
        merge_companies(self.conn, keep_id=a, lose_id=b, now=NOW)
        self.assertEqual(self.conn.execute("SELECT company_id FROM company_alias WHERE alias_key = 'boltlabs'"
                                           ).fetchone()[0], b)
        self.assertFalse(json.loads(self.merged_event(b)["detail"])["alias_added"])

    def test_merge_into_a_merged_company_is_a_conflict(self):
        a, b, item = self.pair()
        elsewhere = s.company(self.conn, "Elsewhere")
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                          "WHERE company_id = ?", (elsewhere, a))
        self.assertEqual(self.refused("conflict", merge_companies, self.conn, keep_id=a, lose_id=b, now=NOW),
                         "One of these was already merged into another company. Keep them separate here, then "
                         "merge from that company's page.")
        self.refused("conflict", merge_companies, self.conn, keep_id=b, lose_id=a, now=NOW)
        self.assertIsNone(self.company(b)["merged_into"])
        self.conn.execute("UPDATE company SET merged_into = ? WHERE company_id = ?", (b, a))
        self.assertEqual(self.refused("conflict", merge_companies, self.conn, keep_id=b, lose_id=a, now=NOW),
                         "These are already one company.")
        self.refused("not_allowed", merge_companies, self.conn, keep_id=b, lose_id=b, now=NOW)
        self.refused("not_found", merge_companies, self.conn, keep_id=b, lose_id=999, now=NOW)

    def test_merging_away_a_paused_or_stopped_company_into_a_live_one_is_refused(self):
        a, b, item = self.pair(status="working", paused_at="2026-11-02T12:00:00Z")
        self.assertEqual(self.refused("not_allowed", merge_companies, self.conn, keep_id=a, lose_id=b, now=NOW),
                         "This one is paused. Keep the paused company, or unpause it first.")
        merge_companies(self.conn, keep_id=b, lose_id=a, now=NOW)  # keeping the paused one is fine
        c, d, _ = self.pair("Closed Keep", "Closed Lose", status="closed", close_reason="not_now")
        self.assertEqual(self.refused("not_allowed", merge_companies, self.conn, keep_id=c, lose_id=d, now=NOW),
                         "This one is closed. Keep the closed company, or reopen it first.")
        e, f, _ = self.pair("Removed Keep", "Removed Lose", status="removed", remove_reason="not_a_fit")
        self.assertEqual(self.refused("not_allowed", merge_companies, self.conn, keep_id=e, lose_id=f, now=NOW),
                         "This one is removed. Keep the removed company, or restore it first.")

    def test_merge_settles_the_losers_other_items(self):
        a, b, item = self.pair()
        lost_items = [raise_item(self.conn, kind=k, company_id=b, reason="x.", evidence_key="k", raised_by=r,
                                 now=NOW).review_id for k, r in (("size_unknown", "daily_list"), ("flagged", "claude"))]
        keeper_item = raise_item(self.conn, kind="flagged", company_id=a, reason="x.", evidence_key="k",
                                 raised_by="claude", now=NOW).review_id
        result = apply_fix(self.conn, item, Fix("merge", keep="a"), revision=1, now=NOW)
        self.assertEqual((result.resolution, result.note, result.revision),
                         ("field_edited", "Merged Bolt Labs into Bolt by you", 2))
        self.assertEqual(set(result.settled), set(lost_items))
        for rid in lost_items:
            self.assertEqual((self.item(rid)["resolution"], self.item(rid)["resolution_note"]),
                             ("cleared", "Merged into Bolt."))
        self.assertEqual(self.item(keeper_item)["state"], "open")
        event = self.conn.execute("SELECT detail FROM event WHERE entity = 'review_item' AND kind = 'resolved' "
                                  "AND entity_id = ?", (item,)).fetchone()
        detail = json.loads(event[0])
        self.assertEqual((detail["fix"], detail["kept_id"], detail["merged_id"]), ("merge", a, b))

    def test_merge_keep_b_merges_a_into_b(self):
        a, b, item = self.pair()
        apply_fix(self.conn, item, Fix("merge", keep="b"), revision=1, now=NOW)
        self.assertEqual(self.company(a)["merged_into"], b)
        self.assertIsNone(self.company(b)["merged_into"])

    def test_a_refused_merge_leaves_the_item_open(self):
        a, b, item = self.pair(status="working", paused_at="2026-11-02T12:00:00Z")
        with self.assertRaises(ReviewError):
            apply_fix(self.conn, item, Fix("merge", keep="a"), revision=1, now=NOW)
        self.assertEqual((self.item(item)["state"], self.item(item)["revision"]), ("open", 1))
        with self.assertRaises(ReviewError) as caught:
            apply_fix(self.conn, item, Fix("merge"), revision=1, now=NOW)
        self.assertEqual(caught.exception.code, "bad_request")

    def test_keep_separate_is_ok_and_merges_nothing(self):
        a, b, item = self.pair()
        result = apply_fix(self.conn, item, Fix("ok"), revision=1, now=NOW)
        self.assertEqual((result.resolution, result.note), ("marked_ok", words.keep_separate_note()))
        self.assertIsNone(self.company(b)["merged_into"])
        self.assertEqual(self.company(b)["status"], "candidate")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM company_alias").fetchone()[0], 0)

    def test_no_message_or_person_row_changes_in_a_merge(self):
        a, b, item = self.pair()
        t = s.touch(self.conn, s.person(self.conn, b, "Pat Dup"))
        s.message(self.conn, t, 0)
        s._insert(self.conn, "no_go", {"company_id": b, "reason": "agency", "source": "devanshu", "created_at": s.T0})
        tables = ("person", "touch", "message", "no_go", "evidence")
        before = {t: [tuple(r) for r in self.conn.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in tables}
        apply_fix(self.conn, item, Fix("merge", keep="a"), revision=1, now=NOW)
        unmerge_company(self.conn, b, revision=2, now=NOW)
        for t in tables:
            self.assertEqual([tuple(r) for r in self.conn.execute(f"SELECT * FROM {t} ORDER BY 1")], before[t], t)

    def test_merge_settles_pair_items_that_became_one_company_or_a_duplicate(self):
        a = s.company(self.conn, "Mesh")
        b = s.company(self.conn, "Mesh Systems", "working")
        c = s.company(self.conn, "Meshy")
        d = s.company(self.conn, "Mesh Inc")
        ac, bc, ab, ad = self.question(a, c), self.question(b, c), self.question(a, b), self.question(a, d)
        merge_companies(self.conn, keep_id=b, lose_id=a, now=NOW)  # straight from a company page: no item
        note = "Same company after merging Mesh"
        self.assertEqual((self.item(ab)["resolution"], self.item(ab)["resolution_note"]), ("cleared", note))
        self.assertEqual((self.item(ac)["resolution"], self.item(ac)["resolution_note"]), ("cleared", note))
        self.assertEqual(self.item(bc)["state"], "open")  # the pair that still stands is kept
        # (b, d) is still a question and only this item asked it: it moves to the keeper.
        self.assertEqual((self.item(ad)["resolution"], self.item(ad)["resolution_note"]),
                         ("cleared", "Moved to Mesh Systems after merging Mesh"))
        self.assertEqual(self.open_pair(b, d)["raised_by"], "source")


class MovedPairTest(MergeBase):
    """A merge question that pairs the loser with a third company still asks a real question: it moves to the
    keeper, and an undo takes the move back (review 8-10, Important 1)."""

    def open_questions(self):
        return {(r["company_id"], int(r["evidence_key"])) for r in self.conn.execute(
            "SELECT company_id, evidence_key FROM review_item WHERE kind = 'merge_candidate' AND state = 'open'")}

    def test_a_merge_fix_moves_a_question_with_a_third_company_to_the_keeper_and_undo_takes_it_back(self):
        a = s.company(self.conn, "Mesh")
        b = s.company(self.conn, "Mesh Systems", "working")
        d = s.company(self.conn, "Mesh Inc")
        ab, ad = self.question(a, b), self.question(a, d)
        result = apply_fix(self.conn, ab, Fix("merge", keep="b"), revision=1, now=NOW)
        self.assertEqual((self.item(ad)["resolution"], self.item(ad)["resolution_note"]),
                         ("cleared", "Moved to Mesh Systems after merging Mesh"))
        moved = self.open_pair(b, d)
        self.assertTrue(moved["reason"].startswith("After merging Mesh: "), moved["reason"])
        self.assertEqual(self.open_questions(), {(b, d)})
        self.assertIn(ad, result.settled)
        detail = json.loads(self.conn.execute(
            "SELECT detail FROM event WHERE entity = 'review_item' AND kind = 'resolved' AND entity_id = ?",
            (ab,)).fetchone()[0])
        self.assertEqual(detail["raised"], [moved["review_id"]])
        sweep_needs_you(self.conn, now=NOW, cfg=CFG)
        self.assertEqual(self.open_questions(), {(b, d)})  # the sweep leaves the moved question alone
        reopen_item(self.conn, ab, revision=2, now=LATER)
        self.assertIsNone(self.company(a)["merged_into"])
        self.assertEqual(self.open_questions(), {(a, b), (a, d)})
        self.assertEqual(self.item(moved["review_id"])["resolution"], "cleared")

    def test_a_question_where_the_loser_is_the_higher_id_moves_too_and_survives_the_sweep(self):
        x = s.company(self.conn, "Bolt Group")
        a = s.company(self.conn, "Bolt")
        b = s.company(self.conn, "Bolt Labs", "working")
        ab, xa = self.question(a, b), self.question(x, a)
        apply_fix(self.conn, ab, Fix("merge", keep="b"), revision=1, now=NOW)
        sweep_needs_you(self.conn, now=NOW, cfg=CFG)
        self.assertEqual(self.open_questions(), {(x, b)})
        self.assertEqual(self.item(xa)["resolution"], "cleared")
        reopen_item(self.conn, ab, revision=2, now=LATER)
        self.assertEqual(self.open_questions(), {(a, b), (x, a)})

    def test_the_sweep_moves_a_question_whose_side_another_writer_merged(self):
        c = s.company(self.conn, "Nova")
        a = s.company(self.conn, "Nova Labs")
        keep = s.company(self.conn, "Nova AI", "working")
        ca = self.question(c, a)
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                          "WHERE company_id = ?", (keep, a))
        sweep_needs_you(self.conn, now=NOW, cfg=CFG)
        self.assertEqual((self.item(ca)["resolution"], self.item(ca)["resolution_note"]),
                         ("cleared", "Moved to Nova AI after merging Nova Labs"))
        self.assertEqual(self.open_questions(), {(c, keep)})

    def test_a_moved_pair_he_already_settled_stays_settled(self):
        c = s.company(self.conn, "Nova")
        a = s.company(self.conn, "Nova Labs")
        keep = s.company(self.conn, "Nova AI", "working")
        settled = self.question(c, keep)
        apply_fix(self.conn, settled, Fix("ok"), revision=1, now=NOW)
        ca, ak = self.question(c, a), self.question(a, keep)
        apply_fix(self.conn, ak, Fix("merge", keep="b"), revision=1, now=NOW)
        self.assertEqual(self.item(ca)["resolution"], "cleared")
        self.assertEqual((self.item(settled)["state"], self.item(settled)["resolution"]), ("resolved", "marked_ok"))
        self.assertEqual(self.open_questions(), set())


class UnmergeTest(MergeBase):
    def test_unmerge_restores_status_close_reason_and_pause_exactly(self):
        for keep_cols, cols in (
                ({"status": "working", "paused_at": "2026-11-01T12:00:00Z"},
                 {"status": "working", "paused_at": "2026-11-02T12:00:00Z", "pause_note": "Wait for Q1"}),
                ({"status": "closed", "close_reason": "other"},
                 {"status": "closed", "close_reason": "not_now", "status_note": "Later"}),
                ({"status": "removed", "remove_reason": "not_a_fit"},
                 {"status": "removed", "remove_reason": "agency"}),
                ({"status": "working"}, {"status": "backlog"})):
            with self.subTest(status=cols["status"]):
                a, b, item = self.pair(f"Keep {cols['status']}{len(cols)}", f"Lose {cols['status']}{len(cols)}",
                                       a_cols=keep_cols, **cols)
                before = dict(self.company(b))
                merge_companies(self.conn, keep_id=a, lose_id=b, review_id=item, now=NOW)
                self.assertEqual(unmerge_company(self.conn, b, revision=before["revision"] + 1, now=NOW),
                                 before["revision"] + 2)
                after = dict(self.company(b))
                for key in ("revision", "status_at"):
                    before.pop(key), after.pop(key)
                self.assertEqual(after, before)
                event = self.conn.execute("SELECT * FROM event WHERE entity = 'company' AND kind = 'unmerged' "
                                          "AND entity_id = ?", (b,)).fetchone()
                self.assertEqual(event["actor"], "devanshu")

    def test_unmerge_after_the_keeper_was_merged_elsewhere(self):
        a, b, item = self.pair()
        c = s.company(self.conn, "Bolt Holdings", "working")
        merge_companies(self.conn, keep_id=a, lose_id=b, now=NOW)
        merge_companies(self.conn, keep_id=c, lose_id=a, now=NOW)
        unmerge_company(self.conn, b, revision=2, now=NOW)
        self.assertEqual((self.company(b)["merged_into"], self.company(b)["status"]), (None, "candidate"))
        self.assertEqual(self.company(a)["merged_into"], c)

    def test_unmerge_of_an_unmerged_company_is_not_allowed(self):
        cid = s.company(self.conn)
        self.assertEqual(self.refused("not_allowed", unmerge_company, self.conn, cid, revision=1), "Not merged.")
        self.refused("not_found", unmerge_company, self.conn, 999, revision=1)

    def test_unmerge_stale_revision_conflicts(self):
        a, b, item = self.pair()
        merge_companies(self.conn, keep_id=a, lose_id=b, now=NOW)
        self.refused("conflict", unmerge_company, self.conn, b, revision=1)
        self.assertEqual(self.company(b)["merged_into"], a)


class SweepAndIdentityTest(MergeBase):
    @unittest.skipUnless(HAVE_IDENTITY, "outreach.identity is not on this branch")
    def test_sweep_settles_a_domain_merge_item_once_the_domain_is_shared(self):
        a = s.company(self.conn, "Acme")
        b = s.company(self.conn, "Acme Robotics")
        c = s.company(self.conn, "Beta")
        d = s.company(self.conn, "Beta Labs")
        raise_merge_candidate(self.conn, a, b, "domain", "both hold the domain acme.io")
        raise_merge_candidate(self.conn, c, d, "domain", "both hold the domain beta.io")
        mark_shared_key(self.conn, "domain", "acme.io", "3 employers: A, B, C")
        sweep_needs_you(self.conn, now=NOW, cfg=CFG)
        rows = {r["company_id"]: r for r in self.conn.execute("SELECT * FROM review_item")}
        self.assertEqual((rows[a]["resolution"], rows[a]["resolution_note"]),
                         ("cleared", "acme.io is shared by several employers, so it no longer joins them"))
        self.assertEqual(rows[c]["state"], "open")

    @unittest.skipUnless(HAVE_IDENTITY, "outreach.identity is not on this branch")
    def test_a_candidate_with_the_losers_name_resolves_to_the_keeper(self):
        keep = s.company(self.conn, "Northwind", "working")
        lose = s.company(self.conn, "Northwind Labs")
        s.evidence(self.conn, keep, "hq", "Boston, MA")
        self.conn.execute("INSERT INTO company_name_key(name_key, company_id, strong) VALUES (?, ?, 1)",
                          (name_key("Northwind Labs"), lose))
        self.conn.execute("INSERT INTO source(source_name, version) VALUES ('test', '1')")
        item = self.question(keep, lose)
        apply_fix(self.conn, item, Fix("merge", keep="a"), revision=1, now=NOW)
        companies = self.conn.execute("SELECT count(*) FROM company").fetchone()[0]
        got = resolve_candidate(self.conn, Candidate(name="Northwind Labs", domain=None, keys={}, source_ref="r1",
                                                     state="MA"), source_name="test")
        self.assertEqual((got.company_id, got.created), (keep, False))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM company").fetchone()[0], companies)


if __name__ == "__main__":
    unittest.main()
