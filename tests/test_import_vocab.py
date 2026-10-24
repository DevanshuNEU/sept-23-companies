import unittest

from outreach.importer.vocab import (
    FROZEN_ATS,
    MAYBE_FROZEN_ATS,
    NOON_NY,
    ContactPlan,
    RadarEffect,
    band_stage,
    board_frozen,
    contact_status_plan,
    curated_channel,
    curated_outcome,
    followups_count,
    note_reply,
    noon_ny,
    ny_date,
    radar_event,
    req_named,
    role_kind,
    to_utc,
    tracker_channel,
    tracker_stage,
)


class VocabTest(unittest.TestCase):
    def table(self, fn, cases):
        for value, want in cases:
            with self.subTest(value=value):
                self.assertEqual(fn(value), want)

    def test_role_kind_table(self):
        self.table(role_kind, [
            ("founder", "founder"), ("recruiter", "recruiter"), ("hiring-manager", "manager"),
            ("hiring manager", "manager"), ("engineer", "engineer"), ("alum", "alum"), ("invalid", "other"),
            # contacts.json kinds map to themselves
            ("manager", "manager"), ("other", "other"),
            # case and spaces do not matter
            (" Hiring  Manager ", "manager"), ("Founder", "founder"),
            # anything else is not a guess
            ("Software Engineer", None), ("cto", None), ("", None), (None, None),
        ])

    def test_contact_status_table(self):
        P = ContactPlan
        self.table(contact_status_plan, [
            ("drafted", P("draft", "none", "open", None, None, False)),
            ("approved", P("draft", "none", "open", None, None, True)),
            ("failed", P("draft", "none", "open", None, None, False)),
            ("skipped", P("skipped", "none", "closed", "skipped", None, False)),
            ("sent", P("sent", "none", "open", None, None, False)),
            ("replied", P("sent", "none", "replied", None, None, False)),
            ("bounced", P("sent", "none", "bounced", None, None, False)),
            ("followup_ready", P("sent", "draft", "open", None, None, False)),
            ("followup_approved", P("sent", "draft", "open", None, None, True)),
            ("followup_sent", P("sent", "sent", "open", None, None, False)),
            ("closed", P("sent", "none", "closed", "no_reply", None, False)),
            ("sending", P(None, None, None, None, "unclear_send", False)),
            ("send_unknown", P(None, None, None, None, "unclear_send", False)),
            ("queued", None), ("", None),
        ])
        # replied and closed carry a sent follow-up when the contact has followupSentAt
        self.assertEqual(contact_status_plan("replied", followup_sent=True).message1, "sent")
        self.assertEqual(contact_status_plan("closed", followup_sent=True).message1, "sent")
        self.assertEqual(contact_status_plan("sent", followup_sent=True).message1, "none")
        # no plan can say approved: an old approval is a draft he approves again
        for status in ("approved", "followup_approved"):
            plan = contact_status_plan(status)
            self.assertNotIn("approved", (plan.message0, plan.message1))

    def test_note_reply_table_and_free_text_is_none(self):
        self.table(note_reply, [
            (None, ([], None)), ("null", ([], None)), ("n/a", ([], None)), ("N/A", ([], None)), ("", ([], None)),
            ("none", ([], None)),
            ("replied", (["replied"], "unsorted")),
            ("polite-no", (["replied"], "said_no")),
            ("soft-positive", (["replied"], "interested")),
            ("scheduled-call", (["replied", "interview"], "interested")),
            ("take-home", (["replied", "interview"], "interested")),
            ("linkedin-referral", (["replied", "referred"], "interested")),
            ("linkedin-referred-me", (["replied", "referred"], "interested")),
            ("bounced", (["bounced"], None)),
            ("said they would pass it on", None), ("maybe", None),
        ])

    def test_curated_outcome_table(self):
        self.table(curated_outcome, [
            ("none", ([], None)), ("", ([], None)),
            ("ooo", (["auto_reply"], None)),
            ("bounce", (["bounced"], None)),
            ("no", (["replied"], "said_no")),
            ("soft", (["replied"], "unsorted")),
            ("routed", (["replied", "referred"], "interested")),
            ("call", (["replied", "interview"], "interested")),
            ("CALL", (["replied", "interview"], "interested")),
            ("ghosted", None),
        ])

    def test_tracker_stage_table(self):
        self.table(tracker_stage, [
            ("Not started", []), ("Researching", []),
            ("Referral asked", ["touch"]), ("Applied", ["application"]), ("Replied", ["replied"]),
            ("Interviewing", ["interview"]), ("Offer", ["offer"]), ("Closed", ["close"]),
            ("Ghosted", None), ("", None),
        ])
        self.table(tracker_channel, [("LinkedIn", "linkedin_request"), ("Email", "email"), ("", "other"),
                                     (None, "other"), ("Fax", None)])
        self.table(curated_channel, [("email", "email"), ("linkedin", "linkedin_request"), ("imessage", "other"),
                                     ("Email", "email"), ("pigeon", None)])

    def test_radar_event_table(self):
        E = RadarEffect
        self.table(radar_event, [
            ("applied", E(True, None, (), None, None)),
            ("reply", E(True, None, ("replied",), "unsorted", None)),
            ("screen", E(True, "screen", ("replied",), "interested", None)),
            ("onsite", E(True, "interview", ("interview",), None, None)),
            ("offer", E(True, "offer", ("offer",), None, None)),
            ("rejected", E(True, "rejected", (), None, None)),
            ("withdrawn", E(True, "closed", (), None, None)),
            ("opened", E(False, None, (), None, "opened_only")),
            ("ignored", E(False, None, (), None, "triage_ignore")),
            ("unignored", E(False, None, (), None, "triage_ignore")),
            ("archived", None),
        ])

    def test_band_stage_table(self):
        self.table(band_stage, [("seed", "seed"), ("A", "series_a"), ("B", "series_b"), ("growth", None),
                                ("large", None), ("agency", None), ("", None), ("Z", None)])
        self.assertEqual(FROZEN_ATS, frozenset({"amazon", "apple", "google", "eightfold"}))
        self.assertEqual(MAYBE_FROZEN_ATS, frozenset({"workday", "oracle_orc"}))
        self.assertTrue(board_frozen("google", None))
        self.assertFalse(board_frozen("workday", None))
        self.assertFalse(board_frozen("workday", 300))
        self.assertTrue(board_frozen("workday", 301))
        self.assertFalse(board_frozen("greenhouse", 5000))

    def test_curated_req_and_fu(self):
        self.table(req_named, [("", None), ("y", True), ("yes", True), ("R-100", True), ("n", False),
                               ("no", False), ("false", False), ("0", False), ("none", False)])
        self.table(followups_count, [("", 0), ("y", 1), ("yes", 1), ("2", 2), ("n", 0), ("no", 0),
                                     ("often", None)])

    def test_date_only_is_noon_new_york(self):
        self.assertEqual(NOON_NY.hour, 12)
        self.assertEqual(noon_ny("2026-11-02"), "2026-11-02T17:00:00Z")  # after the clocks go back (EST)
        self.assertEqual(noon_ny("2026-10-30"), "2026-10-30T16:00:00Z")  # before (EDT)
        self.assertEqual(to_utc("2026-10-30"), "2026-10-30T16:00:00Z")
        self.assertEqual(to_utc("2026-10-06T09:14:00-04:00"), "2026-10-06T13:14:00Z")
        self.assertIsNone(to_utc(""))
        # the New York day never moves: a late evening send is still that day in New York
        self.assertEqual(ny_date("2026-10-07T02:30:00Z"), "2026-10-06")
        self.assertEqual(ny_date(noon_ny("2026-11-02")), "2026-11-02")
        with self.assertRaises(ValueError):
            noon_ny("2026-13-01")


if __name__ == "__main__":
    unittest.main()
