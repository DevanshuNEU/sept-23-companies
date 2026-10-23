"""The hide policy, the no-go write and the shared loaders (issue #6, Task 3; rulings 7, 9 and 2026-10-22).

The system's worst failure is hiding a company Devanshu could have reached, so most of these tests are about a
refusal that must not hide."""
import dataclasses
import unittest
from datetime import date
from unittest import mock

from outreach.crawl.base import ReadResult, Validators
from outreach.crawl.boardstate import load_board
from outreach.crawl.ingest import GUESSED_BOARDS, _before_first_good_read, _check_doubtful
from outreach.crawl.stale import load_stale
from outreach.ranking.common import (BOARD_DOUBTFUL_PREFIX, UNREAD_DOUBTFUL_FOUND_BY, age_days, canonical_ids,
                                     clean_text, doubtful_board_ids)
from outreach.ranking.config import default_ranker_config
from outreach.ranking.facts import load_facts
from outreach.ranking.pay import load_wages
from outreach.ranking.roles import load_roles
from outreach.ranking.hide import (Hide, HideInputs, Refusal, decide_hide, load_hide_inputs, posting_visible,
                                   write_no_go)
from outreach.rules import Decision
from tests import ranking_store as rs
from tests.storetest import fresh_store, seed_company

CFG = default_ranker_config()
TODAY = date(2026, 10, 22)
REFUSE = "We are unable to sponsor visas for this role."


def make_decision(**kw) -> Decision:
    base = dict(lane="engineering", level_tier="new_grad", level_ok=True, level_basis="Title says new grad",
                years_asked=None, years_quote="", us="yes", sponsorship="silent", sponsorship_quote="",
                citizenship=False, citizenship_quote="", eligibility="ok", eligibility_reason=None,
                eligibility_quote="", start_2027=False)
    base.update(kw)
    return Decision(**base)


class Store(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.co = seed_company(self.conn, "Acme")

    def inputs(self, stale=None) -> dict[int, HideInputs]:
        canon = canonical_ids(self.conn)
        return load_hide_inputs(self.conn, canon, doubtful_board_ids(self.conn),
                                load_stale(self.conn, TODAY, max_age_months=12) if stale is None else stale)

    def hide_for(self, company_id=None, stale=None) -> Hide | None:
        return decide_hide(self.inputs(stale)[company_id or self.co], CFG)

    def refusal(self, *, final=True, board_id=None, **fields) -> int:
        pid = rs.posting(self.conn, self.co, board_id=board_id, posted_on=fields.pop("posted_on", "2026-10-10"))
        rs.decision(self.conn, pid, sponsorship="no", sponsorship_quote=REFUSE, provisional=0 if final else 1,
                    **fields)
        return pid

    def count(self, table: str) -> int:
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class PostingVisibleTests(unittest.TestCase):
    def test_posting_visible_lets_interns_through_without_restating_passes(self):
        self.assertTrue(posting_visible(make_decision()))
        intern = make_decision(lane="intern", level_tier="not_stated", level_ok=False)
        self.assertTrue(posting_visible(intern))
        student = dataclasses.replace(intern, eligibility="excluded", eligibility_reason="current_student",
                                      eligibility_quote="Must be currently enrolled.")
        self.assertFalse(posting_visible(student))
        self.assertFalse(posting_visible(make_decision(lane="off_lane", level_ok=False)))
        self.assertFalse(posting_visible(make_decision(lane="not_engineering", level_ok=False)))
        self.assertFalse(posting_visible(make_decision(sponsorship="no", sponsorship_quote=REFUSE)))
        self.assertFalse(posting_visible(dataclasses.replace(intern, sponsorship="no", sponsorship_quote=REFUSE)))
        with mock.patch("outreach.ranking.hide.passes", return_value=False) as fake:
            for d in (make_decision(), intern):
                self.assertFalse(posting_visible(d))
            self.assertEqual(fake.call_count, 2)
            self.assertEqual(fake.call_args.args[0].lane, "engineering")  # the intern went through as engineering


class SizeTests(Store):
    def test_over_size_needs_evidence(self):
        self.assertIsNone(self.hide_for())  # no evidence: unknown size is neutral
        a, b, c, d = (seed_company(self.conn, n) for n in "ABCD")
        rs.evidence(self.conn, a, "size", value="400+", num_low=400, confidence="estimate")
        rs.evidence(self.conn, b, "size", value="400+", num_low=400, confidence="guess")
        rs.evidence(self.conn, c, "size", value="250-400", num_low=250, num_high=400, confidence="confirmed")
        rs.evidence(self.conn, d, "headcount", value="420", num_low=420, confidence="confirmed")
        got = self.inputs()
        hide = decide_hide(got[a], CFG)
        self.assertEqual((hide.kind, hide.provisional), ("over_size", False))
        self.assertIn("400", hide.reason)
        self.assertEqual(hide.to_json(), {"kind": "over_size", "reason": hide.reason, "provisional": False})
        self.assertIsNone(decide_hide(got[b], CFG))
        self.assertIsNone(decide_hide(got[c], CFG))
        self.assertEqual(decide_hide(got[d], CFG).kind, "over_size")
        self.assertEqual(got[d].size.low, 420)

    def test_the_hide_and_the_facts_pick_the_same_size_row(self):
        cases = {
            "size over a confirmed headcount": [("size", 50, "guess", 0), ("headcount", 420, "confirmed", 0)],
            "confirmed over estimate": [("size", 400, "estimate", 0), ("size", 80, "confirmed", 0)],
            "a hand-locked guess first": [("size", 80, "confirmed", 0), ("size", 500, "guess", 1)],
            "headcount alone": [("headcount", 420, "estimate", 0)],
        }
        ids = {}
        for name, rows in cases.items():
            cid = ids[name] = seed_company(self.conn, name)
            dup = seed_company(self.conn, name + " dup")
            for i, (fact, low, conf, locked) in enumerate(rows):
                self.conn.execute(
                    "INSERT INTO evidence(company_id, fact, value, num_low, num_high, confidence, locked, "
                    "source_kind, source_ref, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?)",
                    (dup if i % 2 else cid, fact, str(low), low, low, conf, locked, "hand" if locked else "linkedin",
                     f"2026-10-0{i + 1}T08:00:00Z"))
            rs.merge(self.conn, dup, cid)
        canon, doubtful = canonical_ids(self.conn), doubtful_board_ids(self.conn)
        stale = load_stale(self.conn, TODAY, max_age_months=12)
        hides = load_hide_inputs(self.conn, canon, doubtful, stale)
        facts = {f.company_id: f for f in load_facts(self.conn, canon, doubtful,
                                                     load_roles(self.conn, canon, doubtful, TODAY, stale), TODAY,
                                                     CFG, load_wages())}
        want = {"size over a confirmed headcount": 50, "confirmed over estimate": 80, "a hand-locked guess first": 500,
                "headcount alone": 420}
        for name, cid in ids.items():
            with self.subTest(case=name):
                self.assertEqual(hides[cid].size.low, want[name])
                self.assertEqual(facts[cid].size_low, want[name])

    def test_size_wins_over_headcount_when_both_exist(self):
        rs.evidence(self.conn, self.co, "size", value="50-100", num_low=50, num_high=100, confidence="estimate")
        rs.evidence(self.conn, self.co, "headcount", value="420", num_low=420, confidence="confirmed")
        self.assertEqual(self.inputs()[self.co].size.low, 50)
        self.assertIsNone(self.hide_for())


class RefusalTests(Store):
    def test_final_refusal_hides_and_writes_no_go_once(self):
        pid = rs.posting(self.conn, self.co, body=REFUSE, posted_on="2026-10-10")
        rs.decide_all(self.conn)  # the real rules read the refusal
        rs.make_final(self.conn, pid)
        hide = self.hide_for()
        self.assertEqual((hide.kind, hide.provisional, hide.posting_id), ("no_sponsorship", False, pid))
        self.assertEqual(hide.quote, REFUSE)
        self.assertTrue(write_no_go(self.conn, self.co, hide))
        self.assertFalse(write_no_go(self.conn, self.co, hide))
        rows = self.conn.execute("SELECT company_id, reason, source, source_ref, quote, lifted_at FROM no_go").fetchall()
        self.assertEqual([tuple(r) for r in rows], [(self.co, "no_sponsorship", "rules", str(pid), REFUSE, None)])
        events = self.conn.execute("SELECT entity, kind, actor, company_id FROM event WHERE entity = 'no_go'").fetchall()
        self.assertEqual([tuple(e) for e in events], [("no_go", "added", "rules", self.co)])
        self.assertEqual(self.hide_for().kind, "no_go")  # the written row now hides by itself

    def test_provisional_refusal_hides_provisionally_and_writes_nothing(self):
        self.refusal(final=False)
        hide = self.hide_for()
        self.assertEqual((hide.kind, hide.provisional), ("no_sponsorship", True))
        self.assertIn("hidden, pending reader", hide.reason)
        self.assertFalse(write_no_go(self.conn, self.co, hide))
        self.assertEqual(self.count("no_go"), 0)
        self.assertEqual(self.count("event"), 0)

    def test_one_final_refusal_among_provisional_ones_is_final(self):
        self.refusal(final=False)
        pid = self.refusal(final=True)
        hide = self.hide_for()
        self.assertEqual((hide.provisional, hide.posting_id), (False, pid))

    def test_refusals_that_say_nothing_about_his_roles_never_count(self):
        for fields in (dict(lane="intern", level_tier="not_stated"), dict(lane="off_lane", level_tier="not_stated"),
                       dict(lane="not_engineering", level_tier="not_stated"), dict(us="no")):
            with self.subTest(**fields):
                co = seed_company(self.conn, f"Co {fields}")
                pid = rs.posting(self.conn, co)
                rs.decision(self.conn, pid, sponsorship="no", sponsorship_quote=REFUSE, provisional=0, **fields)
                got = self.inputs()[co]
                self.assertEqual(got.refusals, ())
                self.assertIsNone(decide_hide(got, CFG))
        self.assertEqual(self.count("no_go"), 0)

    def test_a_hide_that_is_not_a_final_refusal_is_never_written(self):
        for hide in (Hide("over_size", "Size 400", False), Hide("no_go", "On the no-go list", False),
                     Hide("no_sponsorship", "hidden, pending reader", True, posting_id=1, quote="x")):
            with self.subTest(kind=hide.kind):
                self.assertFalse(write_no_go(self.conn, self.co, hide))
        self.assertEqual(self.count("no_go"), 0)

    def test_closed_and_undecided_postings_never_refuse(self):
        pid = rs.posting(self.conn, self.co, closed=True)
        rs.decision(self.conn, pid, sponsorship="no", sponsorship_quote=REFUSE, provisional=0)
        rs.posting(self.conn, self.co, body=REFUSE)  # no decision row
        self.assertIsNone(self.hide_for())


class NoGoTests(Store):
    def test_a_lifted_no_go_is_never_re_added_or_re_applied(self):
        rs.no_go(self.conn, self.co, lifted=True)
        self.refusal(final=True)
        got = self.inputs()[self.co]
        self.assertEqual(got.no_go, "lifted")
        self.assertIsNone(decide_hide(got, CFG))
        made_anyway = Hide("no_sponsorship", "x", False, posting_id=1, quote=REFUSE)
        self.assertFalse(write_no_go(self.conn, self.co, made_anyway))
        rows = self.conn.execute("SELECT lifted_at IS NOT NULL FROM no_go").fetchall()
        self.assertEqual([r[0] for r in rows], [1])

    def test_a_lifted_no_go_of_any_reason_cancels_the_size_hide(self):
        rs.no_go(self.conn, self.co, "over_300", lifted=True)
        rs.evidence(self.conn, self.co, "size", value="400+", num_low=400, confidence="confirmed")
        self.assertEqual(self.inputs()[self.co].no_go, "lifted")
        self.assertIsNone(self.hide_for())  # he put it back on the list: shown and ranked, never hidden
        other = seed_company(self.conn, "Other reason")
        rs.no_go(self.conn, other, "agency", lifted=True)
        rs.evidence(self.conn, other, "headcount", value="900", num_low=900, confidence="confirmed")
        self.assertIsNone(decide_hide(self.inputs()[other], CFG))

    def test_an_active_no_go_set_after_a_lift_hides_again(self):
        rs.no_go(self.conn, self.co, "over_300", lifted=True)
        rs.evidence(self.conn, self.co, "size", value="400+", num_low=400, confidence="confirmed")
        rs.no_go(self.conn, self.co, "said_no", created_at="2026-10-05T08:00:00Z")
        hide = self.hide_for()
        self.assertEqual(hide.kind, "no_go")
        self.assertIn("said no", hide.reason)

    def test_a_lifted_no_go_on_a_merged_away_duplicate_blocks_the_write_for_the_survivor(self):
        dup = seed_company(self.conn, "Acme Inc")
        rs.no_go(self.conn, dup, lifted=True)
        rs.merge(self.conn, dup, self.co)
        self.refusal(final=True)
        self.assertEqual(self.inputs()[self.co].no_go, "lifted")
        self.assertIsNone(self.hide_for())
        self.assertFalse(write_no_go(self.conn, self.co, Hide("no_sponsorship", "x", False, posting_id=1)))
        self.assertEqual(self.count("no_go"), 1)

    def test_a_merged_away_company_is_never_written_and_a_lift_up_its_chain_counts(self):
        # A merge that commits while the rank job computes leaves the job holding a refusal for the duplicate: its
        # survivor's lift is up the chain, and a no-go row on the duplicate would re-hide the survivor.
        dup = seed_company(self.conn, "Acme Inc")
        rs.no_go(self.conn, self.co, lifted=True)
        rs.merge(self.conn, dup, self.co)
        made = Hide("no_sponsorship", "x", False, posting_id=1, quote=REFUSE)
        self.assertFalse(write_no_go(self.conn, dup, made))
        bare = seed_company(self.conn, "Bare dup")  # no no-go row anywhere in its family: still refused
        rs.merge(self.conn, bare, self.co)
        self.assertFalse(write_no_go(self.conn, bare, made))
        self.assertEqual(self.count("no_go"), 1)
        self.assertEqual(self.inputs()[self.co].no_go, "lifted")

    def test_an_active_no_go_hides_with_its_own_reason(self):
        rs.no_go(self.conn, self.co, "defence_contractor")
        hide = self.hide_for()
        self.assertEqual((hide.kind, hide.provisional), ("no_go", False))
        self.assertIn("defence contractor", hide.reason)

    def test_an_active_no_go_counts_only_when_set_after_the_latest_lift(self):
        rs.no_go(self.conn, self.co, "agency", lifted=True, lifted_at="2026-10-01T08:00:00Z")
        rs.no_go(self.conn, self.co, "said_no", created_at="2026-10-02T08:00:00Z")
        self.assertEqual(self.inputs()[self.co].no_go, "active")
        self.assertIn("said no", self.hide_for().reason)
        older = seed_company(self.conn, "Older")
        rs.no_go(self.conn, older, "said_no", created_at="2026-09-01T08:00:00Z")
        rs.no_go(self.conn, older, "agency", lifted=True, lifted_at="2026-10-01T08:00:00Z")
        self.assertEqual(self.inputs()[older].no_go, "lifted")  # the lift is newer: he put it back on the list
        same = seed_company(self.conn, "Same second")
        rs.no_go(self.conn, same, "said_no", created_at="2026-10-01T08:00:00Z")
        rs.no_go(self.conn, same, "agency", lifted=True, lifted_at="2026-10-01T08:00:00Z")
        self.assertEqual(self.inputs()[same].no_go, "lifted")  # a tie is not "after"

    def test_a_lift_on_the_survivor_is_not_undone_by_an_older_active_row_on_a_duplicate(self):
        dup = seed_company(self.conn, "Acme Inc")
        rs.no_go(self.conn, dup, "no_sponsorship", source="rules", created_at="2026-09-01T08:00:00Z")
        rs.no_go(self.conn, self.co, "no_sponsorship", lifted=True, lifted_at="2026-10-01T08:00:00Z")
        rs.merge(self.conn, dup, self.co)
        got = self.inputs()[self.co]
        self.assertEqual(got.no_go, "lifted")
        self.assertIsNone(decide_hide(got, CFG))
        newer = seed_company(self.conn, "Acme Labs")
        rs.no_go(self.conn, newer, "said_no", created_at="2026-10-10T08:00:00Z")
        rs.merge(self.conn, newer, self.co)
        got = self.inputs()[self.co]
        self.assertEqual((got.no_go, got.no_go_reason), ("active", "said_no"))
        self.assertEqual(decide_hide(got, CFG).kind, "no_go")

    def test_a_rules_row_on_a_duplicate_after_the_lift_never_re_hides_the_survivor(self):
        # The crawler or an earlier rank run wrote a refusal row on the duplicate after he lifted the survivor;
        # the merge then joins them. His lift wins over automation; only his own row or a reply hides again.
        self.refusal(final=True)
        rs.no_go(self.conn, self.co, "no_sponsorship", lifted=True, lifted_at="2026-10-01T08:00:00Z")
        dup = seed_company(self.conn, "Acme Inc")
        rs.no_go(self.conn, dup, "no_sponsorship", source="rules", created_at="2026-10-05T08:00:00Z")
        rs.merge(self.conn, dup, self.co)
        got = self.inputs()[self.co]
        self.assertEqual(got.no_go, "lifted")
        hide = decide_hide(got, CFG)
        self.assertIsNone(hide)
        made = Hide("no_sponsorship", "x", False, posting_id=1, quote=REFUSE)
        self.assertFalse(write_no_go(self.conn, self.co, made))
        self.assertEqual(self.count("no_go"), 2)
        # Claude is not him, and an import replays old lists: neither undoes his lift.
        for source in ("claude", "import"):
            with self.subTest(source=source):
                other = seed_company(self.conn, f"Acme {source}")
                row = rs.no_go(self.conn, other, "agency", source=source, created_at="2026-10-06T08:00:00Z")
                rs.merge(self.conn, other, self.co)
                got = self.inputs()[self.co]
                self.assertEqual(got.no_go, "lifted")
                self.assertIsNone(decide_hide(got, CFG))
                self.conn.execute("DELETE FROM no_go WHERE no_go_id = ?", (row,))
        for source in ("reply", "devanshu"):
            with self.subTest(source=source):
                other = seed_company(self.conn, f"Acme {source}")
                row = rs.no_go(self.conn, other, "said_no", source=source, created_at="2026-10-06T08:00:00Z")
                rs.merge(self.conn, other, self.co)
                got = self.inputs()[self.co]
                self.assertEqual((got.no_go, got.no_go_reason), ("active", "said_no"))
                self.assertEqual(decide_hide(got, CFG).kind, "no_go")
                self.conn.execute("DELETE FROM no_go WHERE no_go_id = ?", (row,))


class TrustedBoardTests(Store):
    def test_postings_on_a_doubtful_board_never_hide(self):
        b = rs.board(self.conn, self.co)
        self.refusal(board_id=b)
        item = rs.flag_board(self.conn, self.co, b, "board match doubtful: org name differs")
        self.assertIn(b, doubtful_board_ids(self.conn))
        self.assertIsNone(self.hide_for())
        rs.resolve(self.conn, item, "marked_ok")
        self.assertNotIn(b, doubtful_board_ids(self.conn))
        self.assertEqual(self.hide_for().kind, "no_sponsorship")

    def test_only_an_open_crawler_doubtful_item_makes_a_board_doubtful(self):
        b = rs.board(self.conn, self.co)
        rs.flag_board(self.conn, self.co, b, "some other worry")
        rs.flag_board(self.conn, self.co, b, BOARD_DOUBTFUL_PREFIX + ": org name differs", raised_by="claude")
        self.assertEqual(doubtful_board_ids(self.conn), frozenset())

    def test_a_stapply_name_board_never_hides_before_its_first_read(self):
        for found_by in ("stapply_name", "slug_probe"):
            with self.subTest(found_by=found_by):
                co = seed_company(self.conn, found_by)
                b = rs.board(self.conn, co, found_by=found_by, last_read=None)
                pid = rs.posting(self.conn, co, board_id=b)
                rs.decision(self.conn, pid, sponsorship="no", sponsorship_quote=REFUSE, provisional=0)
                self.assertIsNone(decide_hide(self.inputs()[co], CFG))
                self.conn.execute("UPDATE board SET last_read = 'ok', last_read_at = '2026-10-21T08:00:00Z', "
                                  "streak_since = '2026-10-21T08:00:00Z' WHERE board_id = ?", (b,))
                self.assertEqual(decide_hide(self.inputs()[co], CFG).kind, "no_sponsorship")
                rs.flag_board(self.conn, co, b)
                self.assertIsNone(decide_hide(self.inputs()[co], CFG))

    def test_a_guessed_board_whose_first_reads_errored_is_still_doubtful(self):
        for found_by in ("stapply_name", "slug_probe"):
            with self.subTest(found_by=found_by):
                co = seed_company(self.conn, f"err {found_by}")
                b = rs.board(self.conn, co, found_by=found_by, last_read="error", error_streak=2)
                pid = rs.posting(self.conn, co, board_id=b)
                rs.decision(self.conn, pid, sponsorship="no", sponsorship_quote=REFUSE, provisional=0)
                rs.board_read(self.conn, b, "error")
                self.assertIn(b, doubtful_board_ids(self.conn))
                self.assertIsNone(decide_hide(self.inputs()[co], CFG))
        signs = (lambda b: rs.board_read(self.conn, b, "ok"), lambda b: rs.board_read(self.conn, b, "unchanged"),
                 lambda b: rs.read_state_event(self.conn, b, "empty"), lambda b: rs.read_state_event(self.conn, b, "ok"))
        for i, sign in enumerate(signs):
            with self.subTest(sign=i):
                co = seed_company(self.conn, f"seen {i}")
                b = rs.board(self.conn, co, found_by="stapply_name", last_read="error")
                rs.read_state_event(self.conn, b, "error")  # an error event is no sign of a good read
                self.assertIn(b, doubtful_board_ids(self.conn))
                sign(b)
                self.assertNotIn(b, doubtful_board_ids(self.conn))
        streak = rs.board(self.conn, self.co, found_by="slug_probe", last_read="error", empty_streak=1)
        self.assertNotIn(streak, doubtful_board_ids(self.conn))  # errors after an empty read: it was read once

    def test_an_inactive_board_counts_for_nothing(self):
        b = rs.board(self.conn, self.co, active=0)
        self.refusal(board_id=b)
        self.assertIn(b, doubtful_board_ids(self.conn))
        self.assertIsNone(self.hide_for())

    def test_an_import_board_never_read_is_trusted(self):
        b = rs.board(self.conn, self.co, found_by="import", last_read=None)
        self.refusal(board_id=b)
        self.assertEqual(self.hide_for().kind, "no_sponsorship")


class CrawlerContractTests(Store):
    """Ranking restates three crawler facts (outreach/crawl/ingest.py) without importing its private code. These
    tests pin each copy to the crawler's own function, so a change on either side fails here."""

    def test_the_crawlers_doubtful_reason_starts_with_the_prefix_ranking_reads(self):
        b = rs.board(self.conn, self.co, found_by="stapply_name", last_read=None)
        read = ReadResult("empty", (), Validators(), board_name="Someone Else Entirely")
        _check_doubtful(self.conn, load_board(self.conn, b), read, "2026-10-21T08:00:00Z")
        reasons = [r[0] for r in self.conn.execute("SELECT reason FROM review_item WHERE board_id = ?", (b,))]
        self.assertEqual(len(reasons), 1)
        self.assertTrue(reasons[0].startswith(BOARD_DOUBTFUL_PREFIX), reasons[0])
        rs.board_read(self.conn, b, "empty")  # read once: only the crawler's item keeps it doubtful now
        self.conn.execute("UPDATE board SET last_read = 'empty', last_read_at = '2026-10-21T08:00:00Z', "
                          "streak_since = '2026-10-21T08:00:00Z', empty_streak = 1 WHERE board_id = ?", (b,))
        self.assertIn(b, doubtful_board_ids(self.conn))

    def test_the_guessed_board_kinds_are_the_crawlers(self):
        self.assertEqual(frozenset(UNREAD_DOUBTFUL_FOUND_BY), frozenset(GUESSED_BOARDS))

    def test_before_the_first_good_read_matches_the_crawler_on_every_seeded_board(self):
        seeded = []
        for found_by in GUESSED_BOARDS:
            seeded.append(rs.board(self.conn, self.co, found_by=found_by, last_read=None))
            for last in ("ok", "empty", "unchanged"):
                seeded.append(rs.board(self.conn, self.co, found_by=found_by, last_read=last))
            only_errors = rs.board(self.conn, self.co, found_by=found_by, last_read="error", error_streak=2)
            rs.board_read(self.conn, only_errors, "error")
            rs.read_state_event(self.conn, only_errors, "error")
            seeded.append(only_errors)
            seeded.append(rs.board(self.conn, self.co, found_by=found_by, last_read="error", empty_streak=1))
            for sign in ("ok", "empty", "unchanged"):
                kept = rs.board(self.conn, self.co, found_by=found_by, last_read="error")
                rs.board_read(self.conn, kept, sign)
                seeded.append(kept)
            for to in ("ok", "empty"):
                evented = rs.board(self.conn, self.co, found_by=found_by, last_read="error")
                rs.read_state_event(self.conn, evented, to)
                seeded.append(evented)
        doubtful = doubtful_board_ids(self.conn)
        verdicts = set()
        for b in seeded:
            crawler = _before_first_good_read(self.conn, load_board(self.conn, b))
            verdicts.add(crawler)
            with self.subTest(board=b):
                self.assertEqual(b in doubtful, crawler)
        self.assertEqual(verdicts, {True, False})  # the seeds cover both answers


class ConflictTests(Store):
    def test_a_refusal_with_a_sponsoring_posting_hides_nothing(self):
        self.refusal(final=True)
        for stance in ("yes", "opt_only"):
            with self.subTest(stance=stance):
                pid = rs.posting(self.conn, self.co)
                rs.decision(self.conn, pid, sponsorship=stance)
                got = self.inputs()[self.co]
                self.assertTrue(got.conflict)
                self.assertEqual(got.sponsors, (pid,))
                self.assertIsNone(decide_hide(got, CFG))
                self.conn.execute("UPDATE posting SET closed_at = last_seen_at, close_reason = 'hand' "
                                  "WHERE posting_id = ?", (pid,))
        doubtful = rs.board(self.conn, self.co)
        rs.flag_board(self.conn, self.co, doubtful)
        pid = rs.posting(self.conn, self.co, board_id=doubtful)
        rs.decision(self.conn, pid, sponsorship="yes")
        got = self.inputs()[self.co]
        self.assertEqual(got.sponsors, ())
        self.assertFalse(got.conflict)
        self.assertEqual(decide_hide(got, CFG).kind, "no_sponsorship")
        self.assertEqual(self.count("no_go"), 0)

    def test_an_intern_posting_that_sponsors_also_cancels_a_refusal(self):
        self.refusal(final=True)
        pid = rs.posting(self.conn, self.co, "Software Engineering Intern")
        rs.decision(self.conn, pid, lane="intern", level_tier="not_stated", sponsorship="yes")
        self.assertIsNone(self.hide_for())


class StaleTests(Store):
    def test_a_stale_refusal_never_hides_and_writes_no_no_go(self):
        pid = self.refusal(final=True, posted_on="2016-03-02")
        stale = load_stale(self.conn, TODAY, max_age_months=12)
        self.assertEqual(stale[pid].reason, "posted 2016-03-02, over 12 months")
        got = self.inputs(stale)[self.co]
        self.assertEqual(got.refusals, ())
        self.assertIsNone(decide_hide(got, CFG))
        self.assertEqual(self.count("no_go"), 0)
        hide = self.hide_for(stale={})  # the same refusal, live
        self.assertEqual((hide.kind, hide.posting_id), ("no_sponsorship", pid))

    def test_a_refusal_absent_from_the_latest_read_is_stale_and_never_hides(self):
        b = rs.board(self.conn, self.co, last_read="empty")
        pid = self.refusal(final=True, board_id=b)
        stale = load_stale(self.conn, TODAY, max_age_months=12)
        self.assertIn("not on the board at its latest read", stale[pid].reason)
        self.assertIsNone(self.hide_for())

    def test_a_failed_read_keeps_a_refusal_live(self):
        b = rs.board(self.conn, self.co, last_read="error")
        self.refusal(final=True, board_id=b)
        self.assertEqual(load_stale(self.conn, TODAY, max_age_months=12), {})
        self.assertEqual(self.hide_for().kind, "no_sponsorship")

    def test_a_stale_sponsoring_posting_does_not_cancel_a_live_refusal(self):
        self.refusal(final=True)
        pid = rs.posting(self.conn, self.co, posted_on="2016-03-02")
        rs.decision(self.conn, pid, sponsorship="yes")
        got = self.inputs()[self.co]
        self.assertEqual(got.sponsors, ())
        self.assertFalse(got.conflict)
        self.assertEqual(decide_hide(got, CFG).kind, "no_sponsorship")
        live = self.inputs(stale={})[self.co]
        self.assertEqual(live.sponsors, (pid,))
        self.assertTrue(live.conflict)
        self.assertIsNone(decide_hide(live, CFG))


class MergedTests(Store):
    def test_merged_company_evidence_counts_for_the_survivor(self):
        dup = seed_company(self.conn, "Acme Inc")
        rs.evidence(self.conn, dup, "size", value="400+", num_low=400, confidence="confirmed")
        rs.merge(self.conn, dup, self.co)
        got = self.inputs()
        self.assertNotIn(dup, got)
        self.assertEqual(decide_hide(got[self.co], CFG).kind, "over_size")

    def test_merged_company_refusals_count_for_the_survivor(self):
        dup = seed_company(self.conn, "Acme Inc")
        rs.merge(self.conn, dup, self.co)
        pid = rs.posting(self.conn, dup)
        rs.decision(self.conn, pid, sponsorship="no", sponsorship_quote=REFUSE, provisional=0)
        self.assertEqual(self.hide_for().posting_id, pid)

    def test_canonical_ids_follow_a_chain_and_refuse_a_cycle(self):
        a, b = seed_company(self.conn, "A"), seed_company(self.conn, "B")
        rs.merge(self.conn, a, b)
        rs.merge(self.conn, b, self.co)
        canon = canonical_ids(self.conn)
        self.assertEqual((canon[a], canon[b], canon[self.co]), (self.co, self.co, self.co))
        x, y = seed_company(self.conn, "X"), seed_company(self.conn, "Y")
        rs.merge(self.conn, x, y)
        rs.merge(self.conn, y, x)
        with self.assertRaisesRegex(ValueError, "merged_into loops"):
            canonical_ids(self.conn)  # like identity.live_company: a loop is a store error, never a silent pick

    def test_every_surviving_company_has_inputs(self):
        other = seed_company(self.conn, "Quiet")
        got = self.inputs()
        self.assertEqual(got[other], HideInputs(other, None, "", (), (), None))
        self.assertIsNone(decide_hide(got[other], CFG))


class CommonTests(unittest.TestCase):
    def test_clean_text_replaces_dashes(self):
        self.assertEqual(clean_text("Engineer " + chr(0x2013) + " Backend " + chr(0x2014) + " NYC"),
                         "Engineer - Backend - NYC")
        self.assertEqual(clean_text("plain - text"), "plain - text")
        self.assertEqual(clean_text(""), "")

    def test_age_days_floors_at_zero_and_tolerates_junk(self):
        self.assertEqual(age_days("2026-10-19", TODAY), 3)
        self.assertEqual(age_days("2026-10-19T23:59:59Z", TODAY), 3)
        self.assertEqual(age_days("2026-10-30", TODAY), 0)
        self.assertEqual(age_days("2026-10-22", TODAY), 0)
        for junk in (None, "", "soon", "2026-13-40", "2026/10/19"):
            with self.subTest(junk=junk):
                self.assertIsNone(age_days(junk, TODAY))

    def test_refusal_and_hide_values(self):
        r = Refusal(posting_id=3, provisional=False, quote="q")
        self.assertEqual((r.posting_id, r.provisional, r.quote), (3, False, "q"))
        self.assertFalse(HideInputs(1, None, "", (r,), (), None).conflict)
        self.assertTrue(HideInputs(1, None, "", (r,), (4,), None).conflict)
        self.assertFalse(HideInputs(1, None, "", (), (4,), None).conflict)


class ReasonTextTests(Store):
    def test_hide_reasons_hold_no_dashes_and_one_line(self):
        quote = "We cannot sponsor " + chr(0x2014) + " sorry.\nThanks"
        pid = rs.posting(self.conn, self.co)
        rs.decision(self.conn, pid, sponsorship="no", sponsorship_quote=quote, provisional=0)
        hide = self.hide_for()
        self.assertFalse(set(hide.reason) & {chr(0x2013), chr(0x2014), "\n"})

    def test_a_quote_is_kept_whole_up_to_300_characters(self):
        whole = "We do not sponsor visas. " + "x" * 270
        self.assertEqual(len(whole), 295)
        pid = rs.posting(self.conn, self.co)
        rs.decision(self.conn, pid, sponsorship="no", sponsorship_quote=whole, provisional=0)
        self.assertIn(f'"{whole}"', self.hide_for().reason)
        long = seed_company(self.conn, "Long quote")
        pid = rs.posting(self.conn, long)
        rs.decision(self.conn, pid, sponsorship="no", sponsorship_quote="We do not sponsor visas. " + "y" * 400,
                    provisional=0)
        quoted = self.hide_for(long).reason.split('"')[1]
        self.assertEqual(len(quoted), 300)
        self.assertTrue(quoted.endswith("..."))


if __name__ == "__main__":
    unittest.main()
