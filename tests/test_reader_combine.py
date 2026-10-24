"""The reader and the rules together: a hide needs both, everything else is shown and flagged (#32, Task 4).

The pure tests build `Decision` and `PostingReading` by hand. The store tests use a scratch store and insert readings
straight into `posting_reading` (submit has its own tests), so each one controls exactly what the reader said.
"""
import dataclasses
import json
import unittest
from datetime import date
from unittest import mock

from outreach.reader import combine as combine_module
from outreach.reader.combine import (HIDE_FIELDS, Combined, FieldVerdict, apply_all, apply_reading, combine, compare,
                                     explain, reader_combiner, reading_state)
from outreach.reader.config import load_reader_config
from outreach.reader.types import FIELDS, FieldReading, PostingReading
from outreach.reader.version import reader_version
from outreach.rules import Decision, RulesetNotCurrent, passes, rebuild
from outreach.rules.config import default_config
from outreach.store import body_sha256, utcnow
from tests.storetest import fresh_store, seed_board, seed_company, seed_posting

VERSION = "rv-0000000000aa"
OTHER = "rv-0000000000bb"
SHA = "ab" * 32
NEW_GRAD = "Software Engineer, New Grad"
FILLER = " We build control software for warehouse robots and ship to real customers every week."
REFUSAL = "We do not sponsor visas for this role."
WILL = "We will sponsor visas for the right person."


def rules(**over) -> Decision:
    """The rules' answer for an engineering new-grad US posting that says nothing about sponsorship."""
    base = dict(lane="engineering", level_tier="new_grad", level_ok=True, level_basis="Title says new grad",
                years_asked=None, years_quote="", us="yes", sponsorship="silent", sponsorship_quote="",
                citizenship=False, citizenship_quote="", eligibility="ok", eligibility_reason=None,
                eligibility_quote="", start_2027=False)
    base.update(over)
    return Decision(**base)


def blank(name):
    if name in ("years_low", "years_high"):
        return FieldReading(None, "")
    if name in ("start_date", "location", "pay"):
        return FieldReading("", "")
    return FieldReading("unknown", "")


def reading(version=VERSION, **over) -> PostingReading:
    """Engineering yes (the lane every rules answer here has), every other field unknown or empty, then `over` as
    (value, quote)."""
    fields = {name: blank(name) for name in FIELDS}
    fields["engineering"] = FieldReading("yes", "Software Engineer, New Grad")
    for name, (value, quote) in over.items():
        fields[name] = FieldReading(value, quote)
    return PostingReading(7, SHA, version, fields)


def verdict(combined_or_verdicts, field) -> FieldVerdict:
    verdicts = combined_or_verdicts.verdicts if isinstance(combined_or_verdicts, Combined) else combined_or_verdicts
    return next(v for v in verdicts if v.field == field)


class PureCombineTests(unittest.TestCase):
    def test_agree_keeps_the_hide_and_turns_final(self):
        r = rules(sponsorship="no", sponsorship_quote=REFUSAL)
        c = combine(r, reading(sponsorship=("wont_sponsor", REFUSAL)))
        self.assertFalse(passes(c.decision))
        self.assertEqual(c.decision, r)
        self.assertFalse(c.provisional)
        self.assertEqual(c.reader_version, VERSION)
        self.assertEqual(verdict(c, "sponsorship").state, "agree")
        self.assertEqual(c.raised, ())

    def test_reader_unknown_relaxes_a_rules_hide(self):
        c = combine(rules(sponsorship="no", sponsorship_quote=REFUSAL), reading())
        self.assertEqual(c.decision.sponsorship, "unclear")
        self.assertEqual(c.decision.sponsorship_quote, REFUSAL)  # the rules' quote stays
        self.assertTrue(passes(c.decision))
        self.assertEqual([(v.field, v.state) for v in c.raised], [("sponsorship", "missing")])
        self.assertTrue(c.provisional)  # a raised field is never final (controller ruling 1)
        self.assertIsNone(c.reader_version)

    def test_reader_disagrees_with_rules_hide(self):
        c = combine(rules(sponsorship="no", sponsorship_quote=REFUSAL), reading(sponsorship=("will_sponsor", WILL)))
        self.assertTrue(passes(c.decision))
        self.assertEqual(verdict(c, "sponsorship").state, "disagree")
        self.assertEqual([v.field for v in c.raised], ["sponsorship"])
        self.assertTrue(c.provisional)

    def test_reader_hide_without_rules_never_hides(self):
        # Review Focus 1: a real quote returned with the opposite value. The rules say nothing, so nothing hides.
        r = rules()
        c = combine(r, reading(sponsorship=("wont_sponsor", "we will sponsor visas")))
        self.assertTrue(passes(c.decision))
        self.assertEqual(c.decision, r)
        v = verdict(c, "sponsorship")
        self.assertEqual((v.state, v.would_hide_rules, v.would_hide_reader), ("disagree", False, True))
        self.assertEqual(c.raised, (v,))
        self.assertTrue(c.provisional)

    def test_flipped_negation_against_the_real_rules(self):
        # The quote says the opposite of the value. The rules read the same words as a yes: shown, flagged.
        from outreach.rules import PostingInput, decide
        r = decide(PostingInput(NEW_GRAD, WILL + FILLER, "Boston, MA"))
        self.assertEqual(r.sponsorship, "yes")
        c = combine(r, reading(sponsorship=("wont_sponsor", "We will sponsor visas")))
        self.assertTrue(passes(c.decision))
        self.assertEqual(c.decision.sponsorship, "yes")
        self.assertEqual([(v.field, v.state) for v in c.raised], [("sponsorship", "disagree")])
        self.assertTrue(c.provisional)

    def test_citizenship_and_student_hides_follow_the_same_table(self):
        r = rules(citizenship=True, citizenship_quote="Must be a US citizen.")
        agree = combine(r, reading(citizenship_or_clearance=("yes", "Must be a US citizen.")))
        self.assertFalse(passes(agree.decision))
        self.assertFalse(agree.provisional)
        for value, state in (("no", "disagree"), ("unknown", "missing")):
            c = combine(r, reading(citizenship_or_clearance=(value, "Must be a US citizen." if value == "no" else "")))
            self.assertTrue(passes(c.decision), value)
            self.assertFalse(c.decision.citizenship)
            self.assertEqual(c.decision.citizenship_quote, "Must be a US citizen.")
            self.assertEqual([(v.field, v.state) for v in c.raised], [("citizenship_or_clearance", state)])
        reader_only = combine(rules(), reading(citizenship_or_clearance=("yes", "an active clearance")))
        self.assertTrue(passes(reader_only.decision))
        self.assertEqual([v.field for v in reader_only.raised], ["citizenship_or_clearance"])

    def test_student_window_hides_follow_the_same_table(self):
        r = rules(eligibility="excluded", eligibility_reason="current_student",
                  eligibility_quote="Must be currently enrolled.")
        agree = combine(r, reading(student_or_grad_window=("yes", "Must be currently enrolled.")))
        self.assertFalse(passes(agree.decision))
        self.assertFalse(agree.provisional)
        for value, state in (("no", "disagree"), ("unknown", "missing")):
            c = combine(r, reading(student_or_grad_window=(value, "open to all graduates" if value == "no" else "")))
            self.assertTrue(passes(c.decision), value)
            self.assertEqual((c.decision.eligibility, c.decision.eligibility_reason), ("ok", None))
            self.assertEqual([(v.field, v.state) for v in c.raised], [("student_or_grad_window", state)])
        reader_only = combine(rules(), reading(student_or_grad_window=("yes", "currently enrolled students")))
        self.assertTrue(passes(reader_only.decision))
        self.assertEqual([v.field for v in reader_only.raised], ["student_or_grad_window"])

    def test_location_goes_through_us_status(self):
        r = rules(us="no")
        us = combine(r, reading(location=("Remote, United States", "Remote, United States")))
        self.assertEqual(verdict(us, "location").state, "disagree")
        self.assertEqual(us.decision.us, "unknown")
        self.assertTrue(passes(us.decision))
        self.assertTrue(us.provisional)
        berlin = combine(r, reading(location=("Berlin, Germany", "Berlin, Germany")))
        self.assertEqual(verdict(berlin, "location").state, "agree")
        self.assertFalse(passes(berlin.decision))
        self.assertFalse(berlin.provisional)
        no_text = combine(r, reading())
        self.assertEqual(verdict(no_text, "location").state, "missing")
        self.assertTrue(passes(no_text.decision))
        reader_says_abroad = combine(rules(us="yes"), reading(location=("Berlin, Germany", "Berlin, Germany")))
        self.assertTrue(passes(reader_says_abroad.decision))
        self.assertEqual([v.field for v in reader_says_abroad.raised], ["location"])

    def test_flag_only_fields_never_change_passes(self):
        r = rules()
        c = combine(r, reading(engineering=("no", "a sales role"), level=("senior_plus", "senior engineer role"),
                               years_low=(5, "five years of experience"),
                               start_date=("starts in July 2027", "starts in July 2027")))
        self.assertEqual(c.decision, r)
        self.assertTrue(passes(c.decision))
        for field in ("engineering", "level", "years_low", "start_date"):
            self.assertEqual(verdict(c, field).state, "disagree", field)
            self.assertFalse(verdict(c, field).would_hide_reader)
        self.assertEqual(c.raised, ())
        self.assertFalse(c.provisional)

    def test_fields_with_no_rules_counterpart_are_reader_only(self):
        c = combine(rules(), reading(remote=("yes", "fully remote"), pay=("$120k", "$120k")))
        for field in ("years_high", "remote", "pay"):
            self.assertEqual(verdict(c, field).state, "reader_only")
        self.assertEqual(verdict(c, "pay").reader_value, "$120k")

    def test_no_reading_is_provisional_and_unchanged(self):
        r = rules(sponsorship="no", sponsorship_quote=REFUSAL)
        c = combine(r, None)
        self.assertEqual(c, Combined(r, True, None, ()))
        self.assertFalse(passes(c.decision))

    def test_unreadable_posting_fails_open(self):
        r = rules(sponsorship="no", sponsorship_quote=REFUSAL, us="no",
                  citizenship=True, citizenship_quote="Must be a US citizen.")
        c = combine(r, None, unreadable=True)
        self.assertTrue(passes(c.decision))
        self.assertEqual({v.field: v.state for v in c.verdicts if v.field in HIDE_FIELDS},
                         {f: "missing" for f in HIDE_FIELDS})
        self.assertEqual({v.field for v in c.raised}, {"sponsorship", "location", "citizenship_or_clearance"})
        self.assertTrue(c.provisional)
        self.assertIsNone(c.reader_version)

    def test_unreadable_posting_with_no_hide_stays_provisional(self):
        r = rules()
        c = combine(r, None, unreadable=True)
        self.assertEqual((c.decision, c.provisional, c.reader_version, c.raised), (r, True, None, ()))

    def test_refused_field_counts_as_missing(self):
        r = rules(sponsorship="no", sponsorship_quote=REFUSAL)
        c = combine(r, reading(sponsorship=("wont_sponsor", REFUSAL)), refused=frozenset({"sponsorship"}))
        v = verdict(c, "sponsorship")
        self.assertEqual((v.state, v.would_hide_reader), ("missing", False))
        self.assertTrue(passes(c.decision))
        self.assertEqual(c.raised, (v,))
        self.assertEqual(verdict(compare(r, reading(sponsorship=("wont_sponsor", REFUSAL)),
                                         frozenset({"sponsorship"}), default_config()), "sponsorship").state,
                         "missing")

    def test_untrusted_posting_never_turns_a_hide_final(self):
        # Controller ruling 2: a stale posting or one on a doubtful board keeps an agreed hide provisional.
        r = rules(sponsorship="no", sponsorship_quote=REFUSAL)
        c = combine(r, reading(sponsorship=("wont_sponsor", REFUSAL)), untrusted=True)
        self.assertFalse(passes(c.decision))
        self.assertTrue(c.provisional)
        self.assertIsNone(c.reader_version)
        shown = combine(rules(), reading(), untrusted=True)
        self.assertFalse(shown.provisional)  # nothing hides, so nothing needs the trust

    def test_an_agreed_hide_the_reader_says_is_not_engineering_stays_provisional(self):
        # Review fix 1: the reader doubts the lane, so the hide it agreed to is not evidence enough to be final.
        r = rules(sponsorship="no", sponsorship_quote=REFUSAL)
        for engineering in (("no", "a sales role"), ("unknown", "")):
            c = combine(r, reading(sponsorship=("wont_sponsor", REFUSAL), engineering=engineering))
            self.assertFalse(passes(c.decision), engineering)
            self.assertTrue(c.provisional, engineering)
            self.assertIsNone(c.reader_version)
            self.assertEqual([v.field for v in c.raised], ["engineering"], engineering)
        shown = combine(rules(), reading(engineering=("no", "a sales role")))
        self.assertEqual((shown.provisional, shown.raised), (False, ()))  # nothing hides: only a flag

    def test_a_rules_yes_the_reader_does_not_confirm_is_not_final(self):
        # Review fix 2: a final row tells ranking the reader confirmed the sponsorship wording.
        for stance in ("yes", "opt_only"):
            r = rules(sponsorship=stance, sponsorship_quote=WILL)
            for c in (combine(r, reading()),
                      combine(r, reading(sponsorship=("will_sponsor", WILL)), refused=frozenset({"sponsorship"}))):
                self.assertEqual(verdict(c, "sponsorship").state, "missing")
                self.assertTrue(passes(c.decision))
                self.assertEqual((c.provisional, c.reader_version, c.raised), (True, None, ()), stance)
        confirmed = combine(rules(sponsorship="yes", sponsorship_quote=WILL), reading(sponsorship=("will_sponsor", WILL)))
        self.assertFalse(confirmed.provisional)
        silent = combine(rules(), reading())
        self.assertFalse(silent.provisional)  # the rules claim nothing, so there is nothing to confirm

    def test_two_hides_one_agreed_one_relaxed_stays_hidden_and_provisional(self):
        r = rules(sponsorship="no", sponsorship_quote=REFUSAL, citizenship=True,
                  citizenship_quote="Must be a US citizen.")
        c = combine(r, reading(sponsorship=("wont_sponsor", REFUSAL)))
        self.assertFalse(passes(c.decision))  # the agreed refusal still hides
        self.assertFalse(c.decision.citizenship)
        self.assertTrue(c.provisional)        # but a raised field means the posting is not final


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.company = seed_company(self.conn, "Kestrel")
        self.cfg = dataclasses.replace(load_reader_config(), promoted_version=VERSION)

    def posting(self, body=REFUSAL + FILLER * 8, *, title=NEW_GRAD, location="Boston, MA"):
        pid = seed_posting(self.conn, self.company, title, body, location)
        rebuild(self.conn)
        return pid

    def store_reading(self, pid, version=VERSION, refused=(), **over):
        body = self.conn.execute("SELECT body FROM posting_body WHERE posting_id = ?", (pid,)).fetchone()[0]
        r = dataclasses.replace(reading(version, **over), posting_id=pid, body_sha256=body_sha256(body))
        fields = r.to_json()
        for name in refused:
            fields["fields"][name] = {"value": blank(name).value, "quote": ""}
        self.conn.execute(
            "INSERT INTO posting_reading(posting_id, body_sha256, reader_version, read_at, fields, verified, "
            "refused_fields) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pid, r.body_sha256, version, utcnow(), json.dumps(fields), 0 if refused else 1, json.dumps(list(refused))))
        return r

    def queue_row(self, pid, version=VERSION, state="refused"):
        body = self.conn.execute("SELECT body FROM posting_body WHERE posting_id = ?", (pid,)).fetchone()[0]
        self.conn.execute(
            "INSERT INTO read_queue(posting_id, body_sha256, reader_version, priority, state, attempts, last_error, "
            "enqueued_at) VALUES (?, ?, ?, 1, ?, 3, 'gave up after 3 leases', ?)",
            (pid, body_sha256(body), version, state, utcnow()))

    def row(self, pid):
        return self.conn.execute("SELECT * FROM current_decision WHERE posting_id = ?", (pid,)).fetchone()

    def items(self, pid=None):
        sql = "SELECT * FROM review_item WHERE kind = 'reader_disagrees'"
        args = ()
        if pid is not None:
            sql += " AND posting_id = ?"
            args = (pid,)
        return self.conn.execute(sql + " ORDER BY review_id", args).fetchall()


class ApplyTests(StoreCase):
    def test_apply_reading_writes_the_row_and_one_review_item(self):
        pid = self.posting()
        self.assertEqual(self.row(pid)["sponsorship"], "no")
        self.store_reading(pid, sponsorship=("will_sponsor", "sponsor visas for this role"))
        c = apply_reading(self.conn, pid, cfg=self.cfg)
        row = self.row(pid)
        # Controller ruling 1: a disagreement is shown and flagged and stays provisional.
        self.assertEqual((row["provisional"], row["reader_version"], row["sponsorship"]), (1, None, "unclear"))
        self.assertEqual(row["sponsorship_quote"], REFUSAL)
        self.assertEqual(c.decision.sponsorship, "unclear")
        items = self.items(pid)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual((item["raised_by"], item["state"], item["company_id"]), ("reader", "open", self.company))
        self.assertIn("sponsorship", item["reason"])
        self.assertIn("sponsor visas for this role", item["reason"])
        self.assertIn("will_sponsor", item["reason"])
        body = self.conn.execute("SELECT body FROM posting_body WHERE posting_id = ?", (pid,)).fetchone()[0]
        self.assertEqual(item["evidence_key"], f"{VERSION}:{body_sha256(body)[:12]}:sponsorship")
        before = [tuple(r) for r in self.conn.execute("SELECT * FROM decision ORDER BY posting_id")]
        with mock.patch("outreach.rules.rebuild.utcnow", return_value="2030-01-01T00:00:00Z"):
            apply_reading(self.conn, pid, cfg=self.cfg)  # a rewrite would move decided_at
        self.assertEqual(len(self.items(pid)), 1)
        self.assertEqual([tuple(r) for r in self.conn.execute("SELECT * FROM decision ORDER BY posting_id")], before)

    def test_an_agreed_hide_is_written_final(self):
        pid = self.posting()
        self.store_reading(pid, sponsorship=("wont_sponsor", REFUSAL), location=("Boston, MA", "Boston, MA"))
        apply_reading(self.conn, pid, cfg=self.cfg)
        row = self.row(pid)
        self.assertEqual((row["provisional"], row["reader_version"], row["sponsorship"]), (0, VERSION, "no"))
        self.assertEqual(self.items(pid), [])

    def test_apply_reading_clears_the_item_when_the_reader_later_agrees(self):
        pid = self.posting()
        self.store_reading(pid, version=OTHER)
        apply_reading(self.conn, pid, cfg=dataclasses.replace(self.cfg, promoted_version=OTHER))
        self.assertEqual([i["state"] for i in self.items(pid)], ["open"])
        self.store_reading(pid, sponsorship=("wont_sponsor", REFUSAL))
        apply_reading(self.conn, pid, cfg=self.cfg)
        item = self.items(pid)[0]
        self.assertEqual((item["state"], item["resolution"]), ("resolved", "cleared"))
        self.assertEqual(self.row(pid)["provisional"], 0)
        self.assertEqual(len(self.items(pid)), 1)

    def test_a_new_disagreement_replaces_the_open_item(self):
        pid = self.posting()
        self.store_reading(pid, version=OTHER)
        apply_reading(self.conn, pid, cfg=dataclasses.replace(self.cfg, promoted_version=OTHER))
        self.store_reading(pid, sponsorship=("will_sponsor", "sponsor visas for this role"))
        apply_reading(self.conn, pid, cfg=self.cfg)
        self.assertEqual([i["state"] for i in self.items(pid)], ["resolved", "open"])

    def test_a_resolved_item_is_not_reopened_on_the_same_evidence(self):
        pid = self.posting()
        self.store_reading(pid)
        apply_reading(self.conn, pid, cfg=self.cfg)
        self.conn.execute("UPDATE review_item SET state = 'resolved', resolution = 'marked_ok', resolved_at = ?",
                          (utcnow(),))
        apply_reading(self.conn, pid, cfg=self.cfg)
        self.assertEqual([(i["state"], i["resolution"]) for i in self.items(pid)], [("resolved", "marked_ok")])

    def test_candidate_version_reading_does_not_decide_until_promoted(self):
        pid = self.posting()
        candidate = reader_version(self.cfg)
        self.store_reading(pid, version=candidate, sponsorship=("will_sponsor", "sponsor visas for this role"))
        unpromoted = dataclasses.replace(self.cfg, promoted_version="")
        c = apply_reading(self.conn, pid, cfg=unpromoted)
        row = self.row(pid)
        self.assertEqual((row["provisional"], row["reader_version"], row["sponsorship"]), (1, None, "no"))
        self.assertEqual(c.verdicts, ())
        self.assertEqual(self.items(pid), [])
        shown = explain(self.conn, pid, cfg=unpromoted)
        self.assertEqual(verdict(shown, "sponsorship").state, "disagree")
        self.assertEqual(verdict(shown, "sponsorship").reader_quote, "sponsor visas for this role")

    def test_explain_prefers_the_promoted_version_and_reads_nothing_into_the_store(self):
        pid = self.posting()
        self.store_reading(pid, sponsorship=("wont_sponsor", REFUSAL))
        self.store_reading(pid, version=reader_version(self.cfg), sponsorship=("will_sponsor", "sponsor visas for this role"))
        before = self.conn.total_changes
        self.assertEqual(verdict(explain(self.conn, pid, cfg=self.cfg), "sponsorship").state, "agree")
        self.assertEqual(self.conn.total_changes, before)

    def test_apply_all_restores_combined_rows_after_a_rebuild(self):
        agreed, relaxed, unread = self.posting(), self.posting(), self.posting()
        self.store_reading(agreed, sponsorship=("wont_sponsor", REFUSAL))
        self.store_reading(relaxed, sponsorship=("will_sponsor", "sponsor visas for this role"))
        self.assertEqual(apply_all(self.conn, cfg=self.cfg), 2)
        combined = {pid: tuple(self.row(pid)) for pid in (agreed, relaxed, unread)}
        rebuild(self.conn)
        self.assertEqual(self.row(agreed)["provisional"], 1)
        self.assertEqual(self.row(relaxed)["sponsorship"], "no")
        with mock.patch("outreach.reader.submit.submit", side_effect=AssertionError("no reader call")):
            self.assertEqual(apply_all(self.conn, cfg=self.cfg), 2)
        for pid, before in combined.items():
            after = tuple(self.row(pid))
            self.assertEqual(after[3:], before[3:], pid)  # all but posting, ruleset and decided_at

    def test_apply_all_with_nothing_promoted_does_nothing(self):
        pid = self.posting()
        self.store_reading(pid, version=reader_version(self.cfg))
        self.assertEqual(apply_all(self.conn, cfg=dataclasses.replace(self.cfg, promoted_version="")), 0)
        self.assertEqual(self.row(pid)["provisional"], 1)

    def test_an_unreadable_queue_row_fails_open_and_raises_an_item(self):
        pid = self.posting()
        self.queue_row(pid)
        self.assertEqual(reading_state(self.conn, pid, promoted=VERSION)[1:], (frozenset(), True))
        self.assertEqual(apply_all(self.conn, cfg=self.cfg), 1)
        row = self.row(pid)
        self.assertEqual((row["sponsorship"], row["provisional"], row["reader_version"]), ("unclear", 1, None))
        self.assertEqual(len(self.items(pid)), 1)

    def test_a_partial_reading_counts_its_refused_fields_as_missing(self):
        pid = self.posting()
        self.store_reading(pid, refused=("sponsorship",), sponsorship=("wont_sponsor", REFUSAL))
        got, refused, unreadable = reading_state(self.conn, pid, promoted=VERSION)
        self.assertEqual((refused, unreadable), (frozenset({"sponsorship"}), False))
        apply_reading(self.conn, pid, cfg=self.cfg)
        self.assertEqual(self.row(pid)["sponsorship"], "unclear")
        self.assertEqual(self.row(pid)["provisional"], 1)

    def test_a_reading_of_an_old_body_is_not_used(self):
        pid = self.posting()
        self.store_reading(pid, sponsorship=("wont_sponsor", REFUSAL))
        self.conn.execute("UPDATE posting_body SET body = ? WHERE posting_id = ?", (REFUSAL + FILLER * 9, pid))
        self.assertEqual(reading_state(self.conn, pid, promoted=VERSION), (None, frozenset(), False))
        apply_reading(self.conn, pid, cfg=self.cfg)
        self.assertEqual(self.row(pid)["provisional"], 1)

    def test_a_hide_quote_no_longer_in_the_text_counts_as_refused(self):
        # The title and location are not in the body hash, so a quote checked against them at submit time can be
        # gone later. A hide field whose quote is gone is missing, never an agreement.
        pid = self.posting(location="Berlin, Germany")
        self.conn.execute("UPDATE posting SET country = 'Germany' WHERE posting_id = ?", (pid,))
        rebuild(self.conn)
        self.store_reading(pid, sponsorship=("wont_sponsor", REFUSAL), location=("Berlin, Germany", "Berlin, Germany"))
        apply_reading(self.conn, pid, cfg=self.cfg)
        self.assertEqual(self.row(pid)["provisional"], 0)
        self.conn.execute("UPDATE posting SET location = 'Munich' WHERE posting_id = ?", (pid,))
        self.assertEqual(reading_state(self.conn, pid, promoted=VERSION)[1], frozenset({"location"}))
        apply_reading(self.conn, pid, cfg=self.cfg)
        row = self.row(pid)
        self.assertEqual((row["us"], row["provisional"]), ("unknown", 1))

    def test_a_stale_posting_never_becomes_a_final_hide(self):
        pid = self.posting()
        self.conn.execute("UPDATE posting SET posted_on = '2025-01-01' WHERE posting_id = ?", (pid,))
        self.store_reading(pid, sponsorship=("wont_sponsor", REFUSAL))
        apply_reading(self.conn, pid, cfg=self.cfg, today=date(2026, 10, 24))
        row = self.row(pid)
        self.assertEqual((row["sponsorship"], row["provisional"], row["reader_version"]), ("no", 1, None))

    def test_a_posting_on_a_doubtful_board_never_becomes_a_final_hide(self):
        pid = self.posting()
        board = seed_board(self.conn, self.company)
        self.conn.execute("UPDATE board SET active = 0 WHERE board_id = ?", (board,))
        self.conn.execute("UPDATE posting SET board_id = ?, source_key = 'gh:1' WHERE posting_id = ?", (board, pid))
        self.store_reading(pid, sponsorship=("wont_sponsor", REFUSAL))
        apply_reading(self.conn, pid, cfg=self.cfg)
        self.assertEqual(self.row(pid)["provisional"], 1)
        self.conn.execute("UPDATE board SET active = 1 WHERE board_id = ?", (board,))
        apply_reading(self.conn, pid, cfg=self.cfg)
        self.assertEqual(self.row(pid)["provisional"], 0)

    def test_an_agreed_refusal_the_reader_calls_not_engineering_writes_no_no_go(self):
        from outreach.crawl.stale import load_stale
        from outreach.ranking.common import canonical_ids, doubtful_board_ids
        from outreach.ranking.config import default_ranker_config
        from outreach.ranking.hide import decide_hide, load_hide_inputs, write_no_go
        for engineering in (("no", "a sales role"), ("unknown", "")):
            pid = self.posting()
            self.store_reading(pid, sponsorship=("wont_sponsor", REFUSAL), engineering=engineering)
            apply_reading(self.conn, pid, cfg=self.cfg)
            row = self.row(pid)
            self.assertEqual((row["sponsorship"], row["provisional"], row["reader_version"]), ("no", 1, None))
            items = self.items(pid)
            self.assertEqual([(i["state"], i["evidence_key"].rsplit(":", 1)[1]) for i in items],
                             [("open", "engineering")])
            inputs = load_hide_inputs(self.conn, canonical_ids(self.conn), doubtful_board_ids(self.conn),
                                      load_stale(self.conn, date(2026, 10, 24)))[self.company]
            hide = decide_hide(inputs, default_ranker_config())
            self.assertTrue(hide.provisional)
            self.assertFalse(write_no_go(self.conn, self.company, hide))
            self.conn.execute("UPDATE posting SET closed_at = ?, close_reason = 'hand' WHERE posting_id = ?",
                              (utcnow(), pid))

    def test_pay_from_the_body_fills_an_empty_posting_pay(self):
        pid = self.posting(body=REFUSAL + " Pay is $120,000 to $140,000." + FILLER * 8)
        self.store_reading(pid, pay=("$120,000 to $140,000", "$120,000 to $140,000"))
        apply_reading(self.conn, pid, cfg=self.cfg)
        self.assertEqual(self.conn.execute("SELECT pay FROM posting WHERE posting_id = ?", (pid,)).fetchone()[0],
                         "$120,000 to $140,000")
        self.conn.execute("UPDATE posting SET pay = 'stated by the board' WHERE posting_id = ?", (pid,))
        apply_reading(self.conn, pid, cfg=self.cfg)
        self.assertEqual(self.conn.execute("SELECT pay FROM posting WHERE posting_id = ?", (pid,)).fetchone()[0],
                         "stated by the board")

    def test_apply_reading_raises_when_the_ruleset_is_not_current(self):
        pid = self.posting()
        self.conn.execute("UPDATE ruleset SET is_current = 0")
        with self.assertRaises(RulesetNotCurrent):
            apply_reading(self.conn, pid, cfg=self.cfg)

    def test_reader_combiner_reads_the_promoted_version(self):
        pid = self.posting()
        self.store_reading(pid, sponsorship=("wont_sponsor", REFUSAL))
        r = rules(sponsorship="no", sponsorship_quote=REFUSAL)
        self.assertFalse(reader_combiner(self.conn, self.cfg)(self.conn, pid, r).provisional)
        unpromoted = reader_combiner(self.conn, dataclasses.replace(self.cfg, promoted_version=""))
        self.assertEqual(unpromoted(self.conn, pid, r), Combined(r, True, None, ()))

    def test_reader_combiner_defaults_to_the_shipped_config(self):
        with mock.patch.object(combine_module, "load_reader_config", return_value=self.cfg) as loader:
            reader_combiner(self.conn)
        loader.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
