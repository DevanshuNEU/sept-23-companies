import json
import sqlite3
import unittest
from unittest import mock

from outreach.review import ReviewError, words
from outreach.review.fixes import Fix, FixResult, apply_fix
from outreach.review.items import raise_item
from tests import apiseed as s
from tests.review_seed import seed_email_person, seed_queued_message
from tests.storetest import fresh_store

NOW = "2026-11-10T15:00:00Z"


class FixBase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.cid = s.company(self.conn, "Acme", "working")

    def item(self, kind="size_unknown", company_id=None, key=None, raised_by=None, **kw):
        company_id = company_id or self.cid
        if kind in ("sponsorship_unclear", "reader_disagrees") and "posting_id" not in kw:
            kw["posting_id"] = s.posting(self.conn, company_id)
        if kind in ("board_unreadable", "board_empty") and "board_id" not in kw:
            kw["board_id"] = s.board(self.conn, company_id)
        defaults = {"size_unknown": "daily_list", "domain_missing": "sender", "sponsorship_unclear": "rules",
                    "size_guess": "ranker", "size_check": "ranker", "flagged": "claude", "merge_candidate": "source",
                    "reader_disagrees": "reader", "careers_changed": "crawler", "board_unreadable": "crawler",
                    "board_empty": "crawler"}
        n = self.conn.execute("SELECT count(*) FROM review_item").fetchone()[0]
        return raise_item(self.conn, kind=kind, company_id=company_id, reason=f"About {kind}.",
                          evidence_key=key or f"k{n}", raised_by=raised_by or defaults[kind], now=NOW,
                          **kw).review_id

    def fix(self, review_id, fix, revision=1, **kw):
        return apply_fix(self.conn, review_id, fix, revision=revision, now=NOW, **kw)

    def row(self, review_id):
        return self.conn.execute("SELECT * FROM review_item WHERE review_id = ?", (review_id,)).fetchone()

    def count(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()[0]

    def refused(self, code, review_id, fix, revision=1):
        with self.assertRaises(ReviewError) as caught:
            self.fix(review_id, fix, revision)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        return caught.exception.message

    def resolved_events(self, review_id):
        return self.conn.execute("SELECT * FROM event WHERE entity = 'review_item' AND kind = 'resolved' "
                                 "AND entity_id = ?", (review_id,)).fetchall()


class OkAndEditTest(FixBase):
    def test_ok_resolves_marked_ok_and_writes_nothing_else(self):
        item = self.item("flagged")
        other = self.item("size_unknown")
        before = (self.count("SELECT count(*) FROM evidence"), self.count("SELECT count(*) FROM no_go"))
        result = self.fix(item, Fix("ok"))
        self.assertEqual(result, FixResult(item, 2, "marked_ok", words.ok_note(), self.cid, ()))
        row = self.row(item)
        self.assertEqual((row["state"], row["resolution"], row["resolution_note"], row["resolved_at"]),
                         ("resolved", "marked_ok", "Marked ok by you", NOW))
        self.assertEqual((self.count("SELECT count(*) FROM evidence"), self.count("SELECT count(*) FROM no_go")),
                         before)
        self.assertEqual(self.row(other)["state"], "open")
        (event,) = self.resolved_events(item)
        self.assertEqual((event["actor"], event["company_id"], event["idempotency_key"]),
                         ("devanshu", self.cid, f"review-resolved:{item}:2"))

    def test_ok_on_a_merge_question_is_keep_separate(self):
        b = s.company(self.conn, "Acme Labs")
        item = self.item("merge_candidate", key=str(b))
        result = self.fix(item, Fix("ok"))
        self.assertEqual((result.resolution, result.note), ("marked_ok", words.keep_separate_note()))
        self.assertIsNone(self.conn.execute("SELECT merged_into FROM company WHERE company_id = ?", (b,)).fetchone()[0])

    def test_edit_size_resolves_field_edited_with_the_note_and_the_fact_wins(self):
        s.evidence(self.conn, self.cid, "size", "11-50", confidence="confirmed", source_kind="linkedin", low=11,
                   high=50)
        item = self.item("size_unknown")
        result = self.fix(item, Fix("edit", field="size", value="about 45", source="Their about page"))
        note = words.fix_note("size", "45", "Their about page")
        self.assertEqual((result.resolution, result.note, result.revision), ("field_edited", note, 2))
        self.assertEqual(self.row(item)["resolution_note"], note)
        fact = self.conn.execute("SELECT * FROM company_fact WHERE company_id = ? AND fact = 'size'",
                                 (self.cid,)).fetchone()
        self.assertEqual((fact["value"], fact["locked"], fact["source_kind"], fact["source_ref"], fact["confidence"]),
                         ("45", 1, "hand", "Their about page", "estimate"))
        s.evidence(self.conn, self.cid, "size", "500", confidence="confirmed", source_kind="website", low=500,
                   recorded_at="2026-11-12T08:00:00Z")
        self.assertEqual(self.conn.execute("SELECT value FROM company_fact WHERE company_id = ? AND fact = 'size'",
                                           (self.cid,)).fetchone()[0], "45")

    def test_edit_sponsorship_carries_the_items_posting(self):
        item = self.item("sponsorship_unclear")
        posting = self.row(item)["posting_id"]
        self.fix(item, Fix("edit", field="sponsorship", value="OPT only", source="The posting"))
        ev = self.conn.execute("SELECT * FROM evidence WHERE locked = 1").fetchone()
        self.assertEqual((ev["fact"], ev["value"], ev["posting_id"], ev["confidence"]),
                         ("sponsorship", "opt_only", posting, "confirmed"))
        self.assertEqual(self.row(item)["resolution_note"], "Sponsorship set to OPT only by you, from The posting")

    def test_edit_sponsorship_no_is_not_allowed_and_writes_nothing(self):
        item = self.item("sponsorship_unclear")
        self.assertEqual(self.refused("not_allowed", item, Fix("edit", field="sponsorship", value="No", source="x")),
                         "Sponsorship No is a no-go. Use Mark no-go.")
        self.assertEqual(self.row(item)["state"], "open")
        self.assertEqual(self.count("SELECT count(*) FROM evidence"), 0)

    def test_edit_domain_settles_the_crawlers_no_usable_domain_item_too(self):
        mine = self.item("domain_missing", raised_by="sender", key="no-domain")
        crawler = self.item("domain_missing", raised_by="crawler", key="no-usable-domain")
        result = self.fix(mine, Fix("edit", field="domain", value="https://www.Acme.io/jobs", source="Their site"))
        self.assertEqual(result.settled, (crawler,))
        row = self.row(crawler)
        self.assertEqual((row["state"], row["resolution"], row["resolution_note"]),
                         ("resolved", "cleared", "Settled by the domain you set."))
        self.assertEqual(self.conn.execute("SELECT value FROM company_fact WHERE company_id = ? AND fact = 'domain'",
                                           (self.cid,)).fetchone()[0], "acme.io")

    def test_size_edit_settles_every_open_size_item_of_the_company_but_not_its_other_items(self):
        item = self.item("size_unknown")
        guess = self.item("size_guess")
        check = self.item("size_check")
        domain = self.item("domain_missing")
        flagged = self.item("flagged")
        elsewhere = self.item("size_guess", company_id=s.company(self.conn, "Other"))
        result = self.fix(item, Fix("edit", field="size", value="45", source="LinkedIn"))
        self.assertEqual(set(result.settled), {guess, check})
        for rid in (guess, check):
            self.assertEqual(self.row(rid)["resolution"], "cleared")
        for rid in (domain, flagged, elsewhere):
            self.assertEqual(self.row(rid)["state"], "open")

    def test_edit_without_a_source_is_bad_request_and_writes_nothing(self):
        item = self.item("size_unknown")
        for source in (None, "", "   "):
            with self.subTest(source=source):
                self.refused("bad_request", item, Fix("edit", field="size", value="45", source=source))
        for value in (None, "", "lots"):
            with self.subTest(value=value):
                self.refused("bad_request", item, Fix("edit", field="size", value=value, source="LinkedIn"))
        self.assertEqual(self.row(item)["state"], "open")
        self.assertEqual(self.row(item)["revision"], 1)
        self.assertEqual(self.count("SELECT count(*) FROM evidence"), 0)
        self.assertEqual(self.resolved_events(item), [])

    def test_edit_field_must_match_the_kind(self):
        size = self.item("size_unknown")
        self.refused("bad_request", size, Fix("edit", field="domain", value="acme.io", source="x"))
        self.refused("bad_request", size, Fix("edit", value="45", source="x"))
        domain = self.item("domain_missing")
        self.refused("bad_request", domain, Fix("edit", field="size", value="45", source="x"))
        flagged = self.item("flagged")
        self.refused("not_allowed", flagged, Fix("edit", field="size", value="45", source="x"))
        self.assertEqual(self.count("SELECT count(*) FROM evidence"), 0)
        for rid in (size, domain, flagged):
            self.assertEqual(self.row(rid)["state"], "open")

    def test_a_bad_fix_kind_is_bad_request(self):
        item = self.item("flagged")
        self.refused("bad_request", item, Fix("delete"))

    def test_board_edit_needs_a_source_and_unknown_review_item(self):
        board = self.item("board_unreadable")
        self.refused("bad_request", board, Fix("edit", field="board_url", value="https://jobs.lever.co/acme"))
        self.assertEqual(self.row(board)["state"], "open")
        self.refused("not_found", 999, Fix("ok"))


class NoGoTest(FixBase):
    def test_no_go_writes_the_row_with_source_ref_and_the_posting_quote_and_settles_the_other_items(self):
        posting = s.posting(self.conn, self.cid)
        s.decision(self.conn, posting, sponsorship="no", sponsorship_quote="We do not sponsor visas.")
        item = self.item("sponsorship_unclear", posting_id=posting)
        others = [self.item("size_unknown"), self.item("flagged"), self.item("domain_missing")]
        elsewhere = self.item("flagged", company_id=s.company(self.conn, "Other"))
        result = self.fix(item, Fix("no_go", reason="no_sponsorship", source="The posting"))
        row = self.conn.execute("SELECT * FROM no_go").fetchone()
        self.assertEqual((row["company_id"], row["person_id"], row["reason"], row["source"], row["source_ref"],
                          row["quote"], row["note"], row["created_at"], row["lifted_at"]),
                         (self.cid, None, "no_sponsorship", "devanshu", "The posting", "We do not sponsor visas.",
                          "From Needs you.", NOW, None))
        self.assertEqual((result.resolution, result.note), ("marked_no_go", words.no_go_note("no_sponsorship")))
        self.assertEqual(set(result.settled), set(others))
        for rid in others:
            self.assertEqual(tuple(self.row(rid))[self.row(rid).keys().index("state"):][:3],
                             ("resolved", "cleared", "This company is on the no-go list."))
        self.assertEqual(self.row(elsewhere)["state"], "open")
        (event,) = self.resolved_events(item)
        detail = json.loads(event["detail"])
        self.assertEqual((detail["no_go_id"], detail["no_go_created"], detail["reason"]),
                         (row["no_go_id"], True, "no_sponsorship"))

    def test_the_quote_is_only_for_no_sponsorship(self):
        posting = s.posting(self.conn, self.cid)
        s.decision(self.conn, posting, sponsorship="no", sponsorship_quote="We do not sponsor visas.")
        item = self.item("sponsorship_unclear", posting_id=posting)
        self.fix(item, Fix("no_go", reason="agency", source="Their site"))
        self.assertEqual(self.conn.execute("SELECT quote FROM no_go").fetchone()[0], "")

    def test_no_go_needs_a_source_and_a_known_reason(self):
        item = self.item("flagged")
        self.refused("bad_request", item, Fix("no_go", reason="agency", source=" "))
        self.refused("bad_request", item, Fix("no_go", reason="because", source="x"))
        self.refused("bad_request", item, Fix("no_go", source="x"))
        self.assertEqual(self.count("SELECT count(*) FROM no_go"), 0)
        self.assertEqual(self.row(item)["state"], "open")

    def test_no_go_on_a_company_already_on_the_list_adds_no_second_row(self):
        first = s._insert(self.conn, "no_go", {"company_id": self.cid, "reason": "not_a_fit", "source": "devanshu",
                                               "created_at": s.T0})
        item = self.item("flagged")
        result = self.fix(item, Fix("no_go", reason="agency", source="Their site"))
        self.assertEqual(self.count("SELECT count(*) FROM no_go"), 1)
        self.assertEqual((result.resolution, result.note), ("marked_no_go", words.already_no_go_note()))
        detail = json.loads(self.resolved_events(item)[0]["detail"])
        self.assertEqual((detail["no_go_id"], detail["no_go_created"]), (first, False))

    def test_a_lifted_no_go_does_not_count_as_on_the_list(self):
        s._insert(self.conn, "no_go", {"company_id": self.cid, "reason": "not_a_fit", "source": "devanshu",
                                       "created_at": s.T0, "lifted_at": "2026-11-02T12:00:00Z",
                                       "lift_reason": "Changed my mind"})
        item = self.item("flagged")
        self.fix(item, Fix("no_go", reason="agency", source="Their site"))
        self.assertEqual(self.count("SELECT count(*) FROM no_go WHERE lifted_at IS NULL"), 1)

    def test_no_go_on_a_claude_person_suggestion_writes_a_person_no_go_not_a_company_one(self):
        person = s.person(self.conn, self.cid, "Pat Lee")
        item = raise_item(self.conn, kind="flagged", company_id=self.cid, person_id=person,
                          reason='Claude suggests no-go for Pat Lee: they said no. Reply: "Not hiring."',
                          evidence_key=f"claude-no-go:{person}:said_no", raised_by="claude", actor="claude",
                          reopen=False, now=NOW).review_id
        other = self.item("size_unknown")
        result = self.fix(item, Fix("no_go", source="Their reply"))
        rows = self.conn.execute("SELECT * FROM no_go").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["company_id"], rows[0]["person_id"], rows[0]["source"], rows[0]["reason"],
                          rows[0]["source_ref"], rows[0]["quote"]),
                         (None, person, "devanshu", "said_no", "Their reply",
                          'Claude suggests no-go for Pat Lee: they said no. Reply: "Not hiring."'))
        self.assertEqual(result.resolution, "marked_no_go")
        self.assertEqual(result.settled, ())
        self.assertEqual(self.row(other)["state"], "open")
        self.assertEqual(self.conn.execute("SELECT status FROM company WHERE company_id = ?", (self.cid,)).fetchone()[0],
                         "working")

    def test_a_person_no_go_takes_his_reason_over_the_keys_and_keeps_an_existing_row(self):
        person = s.person(self.conn, self.cid, "Pat Lee")
        item = raise_item(self.conn, kind="flagged", company_id=self.cid, person_id=person, reason="Pat may be off.",
                          evidence_key=f"claude-no-go:{person}:said_no", raised_by="claude", now=NOW).review_id
        self.fix(item, Fix("no_go", reason="not_a_fit", source="Their profile"))
        self.assertEqual(self.conn.execute("SELECT reason FROM no_go").fetchone()[0], "not_a_fit")
        again = raise_item(self.conn, kind="flagged", company_id=self.cid, person_id=person, reason="Pat again.",
                           evidence_key="other", raised_by="claude", now=NOW).review_id
        self.fix(again, Fix("no_go", reason="said_no", source="x"))
        self.assertEqual(self.count("SELECT count(*) FROM no_go"), 1)

    def test_merge_candidate_refuses_no_go_and_reader_kinds_refuse_edit(self):
        b = s.company(self.conn, "Acme Labs")
        merge = self.item("merge_candidate", key=str(b))
        self.assertEqual(self.refused("not_allowed", merge, Fix("no_go", reason="agency", source="x")),
                         "A merge question has no no-go. Merge it or keep them separate.")
        self.refused("not_allowed", merge, Fix("edit", field="domain", value="acme.io", source="x"))
        reader = self.item("reader_disagrees")
        self.refused("not_allowed", reader, Fix("edit", field="sponsorship", value="yes", source="x"))
        flagged = self.item("flagged")
        self.refused("not_allowed", flagged, Fix("merge", keep="a"))
        for rid in (merge, reader, flagged):
            self.assertEqual(self.row(rid)["state"], "open")
        self.assertEqual(self.count("SELECT count(*) FROM no_go"), 0)


class SafetyTest(FixBase):
    def test_stale_revision_changes_nothing_and_a_second_call_is_not_allowed(self):
        item = self.item("size_unknown")
        self.conn.execute("UPDATE review_item SET revision = 2 WHERE review_id = ?", (item,))
        self.refused("conflict", item, Fix("edit", field="size", value="45", source="LinkedIn"), revision=1)
        self.assertEqual(self.count("SELECT count(*) FROM evidence"), 0)
        self.assertEqual(self.row(item)["state"], "open")
        self.fix(item, Fix("no_go", reason="agency", source="x"), revision=2)
        message = self.refused("not_allowed", item, Fix("no_go", reason="agency", source="x"), revision=3)
        self.assertEqual(message, "Already resolved.")
        self.refused("not_allowed", item, Fix("ok"), revision=2)  # a second tab with the old revision
        self.assertEqual(self.count("SELECT count(*) FROM no_go"), 1)
        self.assertEqual(len(self.resolved_events(item)), 1)

    def test_a_failure_part_way_leaves_nothing(self):
        item = self.item("size_unknown")
        sibling = self.item("size_guess")

        def boom(*args, **kwargs):
            assert self.conn.execute("SELECT state FROM review_item WHERE review_id = ?",
                                     (item,)).fetchone()[0] == "resolved"  # the resolve statement ran
            raise sqlite3.IntegrityError("boom")

        with mock.patch("outreach.review.fixes.write_hand_fact", side_effect=boom):
            with self.assertRaises(sqlite3.IntegrityError):
                self.fix(item, Fix("edit", field="size", value="45", source="LinkedIn"))
        self.assertEqual((self.row(item)["state"], self.row(item)["revision"]), ("open", 1))
        self.assertEqual(self.row(sibling)["state"], "open")
        self.assertEqual(self.resolved_events(item), [])
        self.assertEqual(self.count("SELECT count(*) FROM evidence"), 0)
        self.assertFalse(self.conn.in_transaction)

    def test_the_event_records_the_fix_and_the_previous_winning_fact(self):
        old = s.evidence(self.conn, self.cid, "size", "11-50", confidence="confirmed", source_kind="linkedin", low=11,
                         high=50)
        item = self.item("size_check")
        self.fix(item, Fix("edit", field="size", value="45", source="LinkedIn"))
        (event,) = self.resolved_events(item)
        detail = json.loads(event["detail"])
        new = self.conn.execute("SELECT evidence_id FROM evidence WHERE locked = 1").fetchone()[0]
        self.assertEqual((detail["fix"], detail["field"], detail["value"], detail["source"], detail["evidence_id"]),
                         ("edit", "size", "45", "LinkedIn", new))
        self.assertEqual((detail["previous_fact"]["evidence_id"], detail["previous_fact"]["value"]), (old, "11-50"))
        self.assertIsNone(detail["no_go_id"])
        self.assertFalse(detail["no_go_created"])
        self.assertEqual((event["actor"], event["company_id"]), ("devanshu", self.cid))
        first = self.item("domain_missing")
        self.fix(first, Fix("edit", field="domain", value="acme.io", source="Site"))
        self.assertIsNone(json.loads(self.resolved_events(first)[0]["detail"])["previous_fact"])

    def test_a_fix_on_a_merged_away_companys_item_writes_on_that_company(self):
        keep = s.company(self.conn, "Keep", "working")
        lost = s.company(self.conn, "Lost", "removed", remove_reason="merged", merged_into=keep)
        domain = self.item("domain_missing", company_id=lost)
        flagged = self.item("flagged", company_id=lost)
        self.fix(domain, Fix("edit", field="domain", value="lost.io", source="Site"))
        self.fix(flagged, Fix("no_go", reason="agency", source="Site"))
        self.assertEqual(self.conn.execute("SELECT company_id FROM evidence WHERE locked = 1").fetchone()[0], lost)
        self.assertEqual(self.conn.execute("SELECT company_id FROM no_go").fetchone()[0], lost)

    def test_no_fix_changes_any_message(self):
        for state in ("planned", "drafted", "approved"):
            seed_queued_message(self.conn, seed_email_person(self.conn, self.cid), state)
        person = s.person(self.conn, self.cid, "Pat Lee")
        before = [tuple(r) for r in self.conn.execute("SELECT * FROM message ORDER BY message_id")]
        self.assertEqual(sorted(r[3] for r in self.conn.execute("SELECT * FROM message")),
                         ["approved", "drafted", "planned"])
        fixes = [("flagged", Fix("ok")), ("size_unknown", Fix("edit", field="size", value="45", source="x")),
                 ("domain_missing", Fix("edit", field="domain", value="acme.io", source="x")),
                 ("sponsorship_unclear", Fix("edit", field="sponsorship", value="yes", source="x")),
                 ("flagged", Fix("no_go", reason="said_no", source="x"))]
        for kind, fix in fixes:
            with self.subTest(fix=fix.kind, kind=kind):
                self.fix(self.item(kind), fix)
                self.assertEqual([tuple(r) for r in self.conn.execute("SELECT * FROM message ORDER BY message_id")],
                                 before)
        person_item = raise_item(self.conn, kind="flagged", company_id=self.cid, person_id=person, reason="Pat.",
                                 evidence_key=f"claude-no-go:{person}:said_no", raised_by="claude", now=NOW).review_id
        self.fix(person_item, Fix("no_go", source="x"))
        self.assertEqual([tuple(r) for r in self.conn.execute("SELECT * FROM message ORDER BY message_id")], before)


if __name__ == "__main__":
    unittest.main()
