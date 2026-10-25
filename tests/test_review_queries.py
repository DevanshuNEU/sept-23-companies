import itertools
import unittest

from outreach.identity import raise_merge_candidate
from outreach.reader.combine import FieldVerdict, _reason as reader_stored_reason
from outreach.review.fixes import Fix, apply_fix
from outreach.review.items import clear_item, raise_item
from outreach.review import words
from outreach.review.queries import display_reason, reopened_note, settled_today
from tests import apiseed as s
from tests.storetest import fresh_store

NOW = "2026-11-10T15:00:00Z"
KEYS = itertools.count(1)
SINCE = "2026-11-05T08:00:00Z"


class QueriesTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.cid = s.company(self.conn, "Acme", "working")

    def row(self, rid):
        return self.conn.execute("SELECT * FROM review_item WHERE review_id = ?", (rid,)).fetchone()

    def board_item(self, kind, board, key=SINCE, reason="The Ashby board returned no jobs once.", created=SINCE):
        return raise_item(self.conn, kind=kind, company_id=self.cid, board_id=board, reason=reason, evidence_key=key,
                          raised_by="crawler", now=created).review_id

    def test_display_reason_for_a_board_is_built_from_live_state(self):
        board = s.board(self.conn, self.cid, "ashby", "acme", last_read="empty", last_read_at="2026-11-06T08:00:00Z",
                        empty_streak=2)
        self.conn.execute("UPDATE board SET streak_since = ? WHERE board_id = ?", (SINCE, board))
        for _ in range(3):
            s.posting(self.conn, self.cid, board_id=board, closed_at="2026-11-06T08:00:00Z",
                      close_reason="board_empty_twice")
        s.posting(self.conn, self.cid, board_id=board, closed_at="2026-11-01T08:00:00Z",
                  close_reason="board_empty_twice")  # an older run, before this streak
        rid = self.board_item("board_empty", board)
        text = display_reason(self.conn, self.row(rid))
        self.assertIn("twice in a row, so its 3 postings were closed", text)
        self.assertNotEqual(text, self.row(rid)["reason"])

    def test_display_reason_for_an_unreadable_board_names_the_error_and_the_last_good_read(self):
        board = s.board(self.conn, self.cid, "greenhouse", "acme", last_read="error",
                        last_read_at="2026-11-07T08:00:00Z", error_streak=3)
        self.conn.execute("UPDATE board SET streak_since = ?, last_error = 'HTTP 503' WHERE board_id = ?",
                          (SINCE, board))
        run = self.conn.execute("INSERT INTO job_run(job, started_at) VALUES ('read_boards', ?)",
                                ("2026-11-01T08:00:00Z",)).lastrowid
        self.conn.execute("INSERT INTO board_read(board_id, run_id, at, result, postings) VALUES (?, ?, ?, 'ok', 1)",
                          (board, run, "2026-11-01T08:00:00Z"))
        s.posting(self.conn, self.cid, board_id=board)
        rid = self.board_item("board_unreadable", board, reason="The board failed.")
        self.assertEqual(display_reason(self.conn, self.row(rid)),
                         "The Greenhouse board has answered with an error on every read since Nov 5 (HTTP 503). "
                         "An error never closes a company, so its 1 posting stays open with what we last saw on "
                         "Nov 1.")

    def test_display_reason_keeps_the_stored_reason_for_other_kinds(self):
        rid = raise_item(self.conn, kind="size_unknown", company_id=self.cid, reason="Nobody has its size.",
                         evidence_key="no-size", raised_by="daily_list").review_id
        self.assertEqual(display_reason(self.conn, self.row(rid)), "Nobody has its size.")

    def test_reopened_note_names_what_changed(self):
        board = s.board(self.conn, self.cid, "ashby", "acme", last_read="error", error_streak=3)
        old = self.board_item("board_unreadable", board, key="2026-10-20T08:00:00Z", created="2026-10-20T08:00:00Z")
        apply_fix(self.conn, old, Fix("ok"), revision=1, now="2026-10-21T15:00:00Z")
        new = self.board_item("board_unreadable", board, key=SINCE)
        self.assertEqual(reopened_note(self.conn, self.row(new)),
                         {"previous": "you marked it ok on Oct 21",
                          "changed": "a new run of failed reads started Nov 5"})

    def test_reopened_note_for_sponsorship_matches_without_the_posting(self):
        first = s.posting(self.conn, self.cid)
        second = s.posting(self.conn, self.cid)
        old = raise_item(self.conn, kind="sponsorship_unclear", company_id=self.cid, posting_id=first, reason="x.",
                         evidence_key="aaaa", raised_by="rules", now="2026-11-01T08:00:00Z").review_id
        clear_item(self.conn, old, "Gone.", "2026-11-02T08:00:00Z")
        new = raise_item(self.conn, kind="sponsorship_unclear", company_id=self.cid, posting_id=second, reason="y.",
                         evidence_key="bbbb", raised_by="rules", now=NOW).review_id
        self.assertEqual(reopened_note(self.conn, self.row(new)),
                         {"previous": "it cleared itself on Nov 2", "changed": "the posting wording changed"})

    def test_reopened_note_is_none_for_a_first_item(self):
        rid = raise_item(self.conn, kind="size_unknown", company_id=self.cid, reason="x.", evidence_key="no-size",
                         raised_by="daily_list").review_id
        self.assertIsNone(reopened_note(self.conn, self.row(rid)))
        other = s.company(self.conn, "Other")
        done = raise_item(self.conn, kind="size_unknown", company_id=other, reason="x.", evidence_key="old",
                          raised_by="daily_list", now="2026-11-01T08:00:00Z").review_id
        apply_fix(self.conn, done, Fix("ok"), revision=1, now=NOW)
        self.assertIsNone(reopened_note(self.conn, self.row(rid)))  # another company's item is no sibling

    def test_settled_today_counts_his_three_resolutions_by_new_york_date_and_not_cleared_ones(self):
        def resolved(resolution, at, key):
            return s._insert(self.conn, "review_item", {
                "kind": "flagged", "company_id": self.cid, "reason": "x.", "evidence_key": key,
                "raised_by": "claude", "state": "resolved", "resolution": resolution, "resolved_at": at,
                "created_at": "2026-11-01T08:00:00Z"})
        resolved("marked_ok", "2026-11-10T05:00:00Z", "a")  # Nov 10, 00:00 in New York
        resolved("marked_no_go", "2026-11-10T20:00:00Z", "b")
        resolved("field_edited", "2026-11-11T04:59:59Z", "c")  # still Nov 10 in New York
        resolved("cleared", "2026-11-10T16:00:00Z", "d")
        resolved("marked_ok", "2026-11-10T04:59:59Z", "e")  # Nov 9 in New York
        resolved("marked_ok", "2026-11-11T05:00:00Z", "f")  # Nov 11
        self.assertEqual(settled_today(self.conn, NOW), 3)
        self.assertEqual(settled_today(self.conn, "2026-11-11T03:00:00Z"), 3)  # 22:00 on Nov 10 in New York


def verdict(field, rules, reader, quote="", state="disagree"):
    return FieldVerdict(field, state, reader, rules, quote, False, False)


class PlainReasonsTest(unittest.TestCase):
    """Follow-up: a merge question, a reader disagreement and a shared-domain size check store technical text
    (`Basis domain, confidence strong`, JSON values, `Basis shared_domain`). Needs you shows plain sentences built
    from it; the stored reason stays as it is, and anything this code cannot read shows the stored text."""

    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.a = s.company(self.conn, "Mesh Systems", "working")
        self.b = s.company(self.conn, "Mesh", "backlog")
        self.posting = s.posting(self.conn, self.a, "Backend Engineer")

    def shown(self, rid: int) -> str:
        row = self.conn.execute("SELECT * FROM review_item WHERE review_id = ?", (rid,)).fetchone()
        text = display_reason(self.conn, row)
        self.assertEqual(display_reason(self.conn, rid), text)
        self.assertTrue(text.strip())
        return text

    def stored(self, rid: int) -> str:
        return self.conn.execute("SELECT reason FROM review_item WHERE review_id = ?", (rid,)).fetchone()[0]

    def merge(self, basis: str, detail: str, **kw) -> int:
        self.conn.execute("DELETE FROM review_item WHERE kind = 'merge_candidate'")
        self.assertTrue(raise_merge_candidate(self.conn, self.a, self.b, basis, detail, **kw))
        return self.conn.execute("SELECT review_id FROM review_item WHERE kind = 'merge_candidate'").fetchone()[0]

    def item(self, kind: str, reason: str, **kw) -> int:
        return raise_item(self.conn, kind=kind, company_id=self.a, reason=reason, evidence_key=f"k{next(KEYS)}",
                          **kw).review_id

    def reader(self, *verdicts) -> int:
        return self.item("reader_disagrees", reader_stored_reason(verdicts), raised_by="reader",
                         posting_id=self.posting)

    # ------------------------------------------------------------------------------------------- merge

    def test_a_merge_question_shows_a_plain_sentence_per_basis(self):
        label = "lca record 88 (Mesh)"
        cases = [
            ("domain", "both hold the domain mesh.example", None,
             "Both use the domain mesh.example, so they may be one company."),
            ("fein_conflict+domain", "both hold the domain mesh.example, but their FEINs differ (12-3456789 and "
             "98-7654321)", None,
             "Both use the domain mesh.example, but their tax ID numbers differ, so the sources could not tell "
             "whether they are one company."),
            ("fein_conflict+name_state", f"{label}: the same name and state (mesh), but FEIN 12-3456789 here and "
             "98-7654321 there, so they are kept apart", None,
             'Both names come down to "mesh" in the same state, but their tax ID numbers differ, so the sources '
             "could not tell whether they are one company."),
            ("name", f"{label}: the same name (mesh), but the state is CA here and NY there", None,
             'Both names come down to "mesh", but their states differ (CA and NY), so the sources could not tell '
             "whether they are one company."),
            ("name", f"{label}: the same name key (mesh), from a name with letters outside ASCII, which never "
             "matches strongly", None,
             'Both names come down to "mesh", but one is written with letters a name match cannot check, so the '
             "sources could not tell whether they are one company."),
            ("ambiguous_name_state", f"{label}: the same name (mesh) and state (CA) as 3 companies, so which one "
             "it is is unclear", None,
             'Both names come down to "mesh" in CA, and 3 companies match that, so the sources could not tell '
             "whether they are one company."),
            ("same_board", "both claim the ashby board mesh", None,
             "Both claim the same Ashby job board, mesh, so they may be one company."),
            ("domain+name_state", f"{label} matched #{self.a} by domain (mesh.example) and #{self.b} by name_state "
             f"(mesh); it was attached to #{self.a}", None,
             "One source record matched one company by its domain and the other by its name and state, so they "
             "may be one company."),
            ("source_ref", f"{label} matched #{self.a} by source_ref (88) and #{self.b} by source_ref (88); it was "
             f"attached to #{self.a}", None, "One source record points at both companies, so they may be one company."),
            ("alias:fein", f"{label} has the fein '12-3456789' (key 123456789), which already belongs to #{self.b}",
             None, "Both have the tax ID number 12-3456789, so they may be one company."),
            ("alias:linkedin", f"{label} has the linkedin 'mesh-systems' (key mesh-systems), which already belongs "
             f"to #{self.b}", None, "Both have the LinkedIn page mesh-systems, so they may be one company."),
            ("alias:ats", f"{label} has the ats 'ashby:mesh' (key ashby:mesh), which already belongs to #{self.b}",
             None, "Both claim the job board ashby:mesh, so they may be one company."),
            ("alias:dba", f"{label} has the dba \"Mesh's Shop\" (key meshs shop), which already belongs to "
             f"#{self.b}", None, 'Both go by the name "Mesh\'s Shop", so they may be one company.'),
            ("import_near_name", "the old sources spell 'Mesh Systems' and 'Mesh'; they may be one company", "weak",
             "The old lists spell the two names almost the same, so they may be one company."),
        ]
        for basis, detail, confidence, want in cases:
            with self.subTest(basis=basis, detail=detail):
                rid = self.merge(basis, detail, confidence=confidence)
                self.assertIn(f"Basis {basis}", self.stored(rid))  # the stored reason is untouched
                self.assertEqual(self.shown(rid), want)

    def test_a_known_basis_whose_detail_does_not_parse_still_reads_plainly(self):
        self.assertEqual(self.shown(self.merge("domain", "something new")),
                         "Both use the same domain, so they may be one company.")
        self.assertEqual(self.shown(self.merge("alias:fein", "something new")),
                         "Both have the same tax ID number, so they may be one company.")

    def test_an_unknown_basis_or_a_malformed_merge_reason_shows_the_stored_text(self):
        rid = self.merge("teleport", "it came from nowhere", confidence="weak")
        self.assertEqual(self.shown(rid), self.stored(rid))
        for reason in ("Maybe one company, who knows", "Basis domain, confidence strong", "x"):
            with self.subTest(reason=reason):
                self.conn.execute("UPDATE review_item SET reason = ? WHERE review_id = ?", (reason, rid))
                self.assertEqual(self.shown(rid), reason)

    # ------------------------------------------------------------------------------------------- reader

    def test_reader_disagrees_reads_as_the_mockup(self):
        rid = self.reader(verdict("sponsorship", "silent", "wont_sponsor",
                                  "We are unable to sponsor work visas for this position."))
        self.assertIn('rules say "silent", reader says "wont_sponsor"', self.stored(rid))
        self.assertEqual(self.shown(rid),
                         "The rules found nothing about sponsorship in this posting. The night reader found this "
                         'line and reads it as no sponsorship: "We are unable to sponsor work visas for this '
                         "position.\" Mark no-go if the reader is right; mark ok to keep the rules' reading.")

    def test_reader_disagrees_when_the_rules_hide_and_the_reader_does_not(self):
        rid = self.reader(verdict("sponsorship", "no", "will_sponsor", "We sponsor H-1B visas; ask us."),
                          verdict("citizenship_or_clearance", True, None, state="missing"))
        self.assertEqual(self.shown(rid),
                         "The rules read this posting as no sponsorship. The night reader found this line and reads "
                         'it as offering sponsorship: "We sponsor H-1B visas; ask us." The rules read this posting as '
                         "needing US citizenship or a clearance. The night reader gave no answer on this. Mark no-go "
                         "if the rules are right; mark ok to go with the reader's reading.")

    def test_reader_disagrees_on_place_window_and_lane(self):
        rid = self.reader(verdict("location", "yes", "London, UK", "London, UK"),
                          verdict("student_or_grad_window", "ok", "yes", "You must be currently enrolled in a degree."),
                          verdict("engineering", "engineering", "no", "Account Executive"))
        self.assertEqual(self.shown(rid),
                         "The rules read this job as in the US. The night reader reads the place as London, UK. "
                         "The rules found no student or graduation limit in this posting. The night reader found "
                         'this line and reads it as open only to current students or a set graduation window: "You '
                         'must be currently enrolled in a degree." The rules read this as an engineering role. The '
                         'night reader found this line and reads it as not an engineering role: "Account Executive" '
                         "Mark no-go if this posting rules the company out; mark ok if it does not.")

    def test_a_reader_reason_this_code_cannot_read_shows_the_stored_text(self):
        unknown_field = self.reader(verdict("pay", "x", "$100k"))
        unknown_value = self.reader(verdict("sponsorship", "maybe", "wont_sponsor"))
        for rid in (unknown_field, unknown_value):
            self.assertEqual(self.shown(rid), self.stored(rid))
        for reason in ("The reader and the rules disagree on sponsorship: rules say silent", "Reader disagrees."):
            rid = self.item("reader_disagrees", reason, raised_by="reader", posting_id=self.posting)
            self.assertEqual(self.shown(rid), reason)

    # ------------------------------------------------------------------------------------------- size

    def shared(self, name: str, others: str) -> int:
        cid = self.a
        reason = (f"{name} (#{cid}). Basis shared_domain, confidence weak: shares copperline.example with "
                  f"{others}: it may be a subsidiary; size unknown")
        return self.item("size_check", reason, raised_by="source")

    def test_a_shared_domain_size_check_reads_as_the_mockup(self):
        self.conn.execute("UPDATE company SET name = 'Copperline' WHERE company_id = ?", (self.a,))
        self.assertEqual(self.shown(self.shared("Copperline", "2 other employers")),
                         "Copperline shares its domain, copperline.example, with 2 other employers in the visa "
                         "filings, so it may be part of a larger company. Nobody has recorded its size.")
        self.assertEqual(self.shown(self.shared("Copperline", "1 other employers")),
                         "Copperline shares its domain, copperline.example, with 1 other employer in the visa "
                         "filings, so it may be part of a larger company. Nobody has recorded its size.")
        self.assertEqual(self.shown(self.shared("Copperline", "other employers")),
                         "Copperline shares its domain, copperline.example, with other employers, so it may be part "
                         "of a larger company. Nobody has recorded its size.")

    def test_the_shown_name_is_the_company_name_now(self):
        self.assertTrue(self.shown(self.shared("Old Name", "2 other employers")).startswith("Mesh Systems shares"))

    def test_other_size_checks_and_malformed_ones_show_the_stored_text(self):
        plain = ("Check the size: shares a domain with a bigger company. Ranking treats it as not small until you "
                 "confirm. The company stays visible.")
        broken = "Mesh Systems (#1). Basis shared_domain, confidence weak: shares"
        for reason in (plain, broken):
            rid = self.item("size_check", reason, raised_by="ranker")
            self.assertEqual(self.shown(rid), reason)

    # ------------------------------------------------------------------------------------------- copy rules

    def test_every_new_sentence_follows_the_copy_rules(self):
        d = f"x{chr(0x2014)}y{chr(0x2013)}z"
        outs = [words.shared_domain_size_reason(d, d, 2), words.shared_domain_size_reason(d, d, None),
                words.merge_reason("domain", f"both hold the domain {d}"),
                words.merge_reason("same_board", f"both claim the {d} board {d}"),
                words.merge_reason("name", f"r: the same name ({d}), but the state is {d} here and {d} there"),
                words.merge_reason("alias:dba", f"r has the dba '{d}' (key x), which already belongs to #2"),
                words.reader_reason([("location", "no", d, d), ("sponsorship", "silent", "wont_sponsor", d)]),
                words.reader_reason([("sponsorship", "no", None, "")])]
        for basis in ("domain", "fein_conflict+domain", "name", "ambiguous_name_state", "same_board", "source_ref",
                      "domain+fein", "alias:fein", "alias:ats", "alias:linkedin", "alias:name", "import_near_name"):
            outs.append(words.merge_reason(basis, "unreadable detail"))
        for text in outs:
            with self.subTest(text=text):
                self.assertTrue(text and text.strip())
                self.assertEqual(text, words.clean(text))
                self.assertFalse(any(c in text for c in (chr(0x2013), chr(0x2014))))
                for banned in ("streak", "missed", "overdue", "behind", "keep it up"):
                    self.assertNotIn(banned, text.lower())
        self.assertIsNone(words.merge_reason("teleport", "x"))
        self.assertIsNone(words.reader_reason([("pay", "x", "y", "")]))
        self.assertIsNone(words.reader_reason([]))


    # ------------------------------------------------------------------------------------------- fix round 1

    def assert_one_short_line(self, text: str) -> None:
        self.assertTrue(text.strip())
        self.assertNotIn("\n", text)
        self.assertNotIn("\r", text)
        for c in "<>&":
            self.assertNotIn(c, text)
        self.assertLess(len(text), 700)
        self.assertEqual(text, words.clean(text))

    def test_outside_text_is_one_short_plain_line(self):
        hostile = "Acme\nCorp <img src=x onerror=alert(1)> & Co"
        long = "x " * 2500  # 5,000 characters
        outs = [
            words.shared_domain_size_reason(hostile, hostile, 2),
            words.shared_domain_size_reason(None, None, None),
            words.shared_domain_size_reason(12345, long, 3),
            words.merge_reason("domain", f"both hold the domain {'d' * 5000}.example"),
            words.merge_reason("same_board", f"both claim the ashby board {hostile}"),
            words.merge_reason("name", f"r: the same name ({long}), but the state is {long} here and CA there"),
            words.merge_reason("ambiguous_name_state", "r: the same name (<b>) and state (C&A) as 3 companies"),
            words.merge_reason("alias:dba", f"r has the dba {hostile!r} (key x), which already belongs to #2"),
            words.merge_reason("alias:fein", f"r has the fein {long!r} (key x), which already belongs to #2"),
            words.reader_reason([("sponsorship", "silent", "wont_sponsor", "We cannot sponsor. " * 260)]),
            words.reader_reason([("location", "yes", hostile, hostile)]),
            words.reader_reason([("location", "yes", 77, None)]),
        ]
        for text in outs:
            with self.subTest(text=str(text)[:80]):
                self.assertIsNotNone(text)
                self.assert_one_short_line(text)
        self.assertIn("Acme Corp img src=x onerror=alert(1) Co shares its domain", outs[0])
        self.assertIn("...", outs[2])
        self.assertIn("...", outs[9])

    def test_a_long_or_hostile_stored_reason_reads_as_one_short_line(self):
        self.conn.execute("UPDATE company SET name = ? WHERE company_id = ?",
                          ("Mesh\nSystems <script>x</script>", self.a))
        size = self.shared("Mesh", "2 other employers")
        reader = self.reader(verdict("sponsorship", "silent", "wont_sponsor", "We cannot sponsor. " * 260))
        for rid in (size, reader):
            self.assert_one_short_line(self.shown(rid))

    def test_a_null_or_empty_stored_reason_never_crashes(self):
        for kind in ("merge_candidate", "size_check", "reader_disagrees", "board_empty", "size_unknown"):
            for reason in (None, ""):
                with self.subTest(kind=kind, reason=reason):
                    row = {"kind": kind, "reason": reason, "board_id": None, "company_id": self.a}
                    self.assertEqual(display_reason(self.conn, row), "")

    def test_an_engineering_lane_these_words_do_not_know_shows_the_stored_text(self):
        rid = self.reader(verdict("engineering", "banana", "yes", "Software Engineer"))
        self.assertIn('rules say "banana"', self.stored(rid))
        self.assertEqual(self.shown(rid), self.stored(rid))
        self.assertEqual(self.shown(self.reader(verdict("engineering", "off_lane", "yes", "Software Engineer"))),
                         "The rules read this as not an engineering role. The night reader found this line and "
                         'reads it as an engineering role: "Software Engineer" Mark no-go if this posting rules the '
                         "company out; mark ok if it does not.")

if __name__ == "__main__":
    unittest.main()
