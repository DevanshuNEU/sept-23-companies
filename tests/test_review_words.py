import unittest

from outreach.review import words
from outreach.review.kinds import KINDS, NO_GO_REASONS

EN_DASH, EM_DASH = chr(0x2013), chr(0x2014)
DASHES = (EN_DASH, EM_DASH)


def board(kind="board_unreadable", **kw):
    args = dict(ats="ashby", error_streak=5, empty_streak=0, last_error="HTTP 503", since="2026-10-22T08:04:00Z",
                last_ok_at="2026-10-19T08:04:00Z", open_postings=2, closed_postings=0)
    args.update(kw)
    return words.board_reason(kind, **args)


class WordsTest(unittest.TestCase):
    def test_clean_and_day(self):
        self.assertEqual(words.clean(f"a{EN_DASH}b{EM_DASH}c - d"), "a-b-c - d")
        self.assertEqual(words.day("2026-10-22T08:04:00Z"), "Oct 22")
        # The app's day is New York: 02:00 UTC on Nov 3 is still Nov 2 there.
        self.assertEqual(words.day("2026-11-03T02:00:00Z"), "Nov 2")
        self.assertEqual(words.day("2026-11-03T15:00:00Z"), "Nov 3")
        self.assertEqual(words.day("2026-01-05T12:00:00Z"), "Jan 5")

    def test_sponsorship_reason(self):
        self.assertEqual(
            words.sponsorship_reason("Hex", "Software Engineer, New Grad"),
            'The "Software Engineer, New Grad" posting is neither a clear yes nor a clear no on sponsorship. '
            "Setting it to No adds Hex to the no-go list.")
        out = words.sponsorship_reason("Hex", f"Engineer {EM_DASH} Platform")
        self.assertIn('"Engineer - Platform"', out)
        self.assertFalse(any(d in out for d in DASHES))

    def test_size_reason_picked_and_planned(self):
        self.assertEqual(
            words.size_reason("Trigger.dev", "picked"),
            "You picked Trigger.dev, and nobody has recorded its size. Over 300 people hides a company, so one "
            "number with a source settles it.")
        self.assertEqual(
            words.size_reason("Wave", "planned"),
            "Wave is in today's plan, and nobody has recorded its size. Over 300 people hides a company, so one "
            "number with a source settles it.")
        with self.assertRaises(ValueError):
            words.size_reason("Wave", "other")

    def test_domain_reason(self):
        self.assertEqual(
            words.domain_reason("Cerebrium"),
            "Cerebrium has people queued for email and no email domain on file. Add the domain so each address can "
            "be checked against it.")

    def test_board_reason_unreadable(self):
        self.assertEqual(
            board(),
            "The Ashby board has answered with an error on every read since Oct 22 (HTTP 503). An error never "
            "closes a company, so its 2 postings stay open with what we last saw on Oct 19.")
        self.assertEqual(
            board(last_ok_at=None),
            "The Ashby board has answered with an error on every read since Oct 22 (HTTP 503). An error never "
            "closes a company, so its 2 postings stay open.")
        self.assertIn("its 1 posting stays open with what we last saw on Oct 19.", board(open_postings=1))
        zero = board(open_postings=0)
        self.assertEqual(zero, "The Ashby board has answered with an error on every read since Oct 22 (HTTP 503). "
                               "An error never closes a company.")
        self.assertNotIn("(", board(last_error=""))
        self.assertIn("The Lever board", board(ats="lever"))
        self.assertIn("The SmartRecruiters board", board(ats="smartrecruiters"))
        self.assertIn("The Somenewats board", board(ats="somenewats"))

    def test_board_reason_empty(self):
        self.assertEqual(
            board("board_empty", ats="greenhouse", empty_streak=1, open_postings=3, error_streak=0),
            "The Greenhouse board returned no jobs once. Its 3 postings stay open until a second empty read.")
        self.assertEqual(
            board("board_empty", ats="greenhouse", empty_streak=2, closed_postings=3, open_postings=0),
            "The Greenhouse board returned no jobs twice in a row, so its 3 postings were closed. If they moved "
            "boards, give the new one. If they stopped hiring, mark ok.")
        self.assertEqual(
            board("board_empty", ats="greenhouse", empty_streak=2, closed_postings=0, open_postings=0),
            "The Greenhouse board returned no jobs twice in a row. It had no open postings to close. If they moved "
            "boards, give the new one. If they stopped hiring, mark ok.")
        self.assertIn("its 1 posting was closed", board("board_empty", empty_streak=2, closed_postings=1))
        self.assertIn("3 times in a row", board("board_empty", empty_streak=3, closed_postings=1))
        self.assertIn("Its 1 posting stays open until", board("board_empty", empty_streak=1, open_postings=1))
        with self.assertRaises(ValueError):
            board("size_unknown")

    def test_reopened_names_the_change_per_kind(self):
        def prev(resolution, at="2026-11-03T15:00:00Z"):
            return {"resolution": resolution, "resolved_at": at}
        out = words.reopened("board_unreadable", prev("marked_ok"), "2026-11-12T08:04:00Z")
        self.assertEqual(out, {"previous": "you marked it ok on Nov 3",
                               "changed": "a new run of failed reads started Nov 12"})
        self.assertEqual(words.reopened("sponsorship_unclear", prev("marked_no_go"), "abc"),
                         {"previous": "you added it to the no-go list on Nov 3",
                          "changed": "the posting wording changed"})
        self.assertEqual(words.reopened("board_empty", prev("field_edited"), "2026-11-12T08:04:00Z"),
                         {"previous": "you changed it on Nov 3", "changed": "a new run of empty reads started Nov 12"})
        for kind in KINDS:
            out = words.reopened(kind, prev("marked_ok"), "2026-11-12T08:04:00Z")
            self.assertTrue(out["changed"] and out["previous"], kind)
            self.assertFalse(any(d in v for v in out.values() for d in DASHES), kind)

    def test_reopened_board_key_that_is_not_a_time_still_says_what_changed(self):
        # The Needs-you view builds this for every open item: one odd key must not break the whole list.
        out = words.reopened("board_unreadable", {"resolution": "marked_ok", "resolved_at": "2026-11-03T15:00:00Z"},
                             "streak:2026-11-12")
        self.assertEqual(out, {"previous": "you marked it ok on Nov 3", "changed": "a new run of failed reads started"})

    def test_notes(self):
        self.assertEqual(words.fix_note("size", "45", "LinkedIn company page"),
                         "Size set to 45 by you, from LinkedIn company page")
        self.assertEqual(words.fix_note("domain", "cerebrium.ai", "their site"),
                         "Email domain set to cerebrium.ai by you, from their site")
        self.assertEqual(words.fix_note("sponsorship", "yes", "the posting"),
                         "Sponsorship set to Yes by you, from the posting")
        self.assertIn("set to OPT only by you", words.fix_note("sponsorship", "opt_only", "x"))
        self.assertIn("set to No by you", words.fix_note("sponsorship", "no", "x"))
        self.assertEqual(words.fix_note("board_url", "https://jobs.example/acme", "their careers page"),
                         "Job board set to https://jobs.example/acme by you, from their careers page")
        self.assertEqual(words.fix_note("size", "45", ""), "Size set to 45 by you")
        with self.assertRaises(ValueError):
            words.fix_note("colour", "red", "x")
        self.assertEqual(words.no_go_note("no_sponsorship"), "Added to the no-go list by you: does not sponsor")
        phrases = [words.no_go_note(r) for r in NO_GO_REASONS]
        self.assertEqual(len(set(phrases)), len(NO_GO_REASONS))
        for text in phrases:
            self.assertTrue(text.startswith("Added to the no-go list by you: ") and len(text) > 33, text)
        with self.assertRaises(ValueError):
            words.no_go_note("because")
        self.assertEqual(words.ok_note(), "Marked ok by you")

    def test_no_string_holds_a_dash(self):
        d = f"x{EM_DASH}y{EN_DASH}z"
        outs = [
            words.sponsorship_reason(d, d),
            words.size_reason(d, "picked"), words.size_reason(d, "planned"),
            words.domain_reason(d),
            board("board_unreadable", ats=d, last_error=d),
            board("board_empty", ats=d, empty_streak=2, closed_postings=2),
            board("board_empty", ats=d, empty_streak=1, open_postings=2),
            words.fix_note("size", d, d), words.fix_note("domain", d, d), words.fix_note("sponsorship", d, d),
            words.fix_note("board_url", d, d),
        ]
        for kind in KINDS:
            outs.extend(words.reopened(kind, {"resolution": "marked_ok", "resolved_at": "2026-11-03T15:00:00Z"},
                                       "2026-11-12T08:04:00Z").values())
        outs.extend(words.no_go_note(r) for r in NO_GO_REASONS)
        outs.append(words.ok_note())
        for text in outs:
            self.assertFalse(any(c in text for c in DASHES), text)
        self.assertIn("x-y-z", outs[0])


if __name__ == "__main__":
    unittest.main()
