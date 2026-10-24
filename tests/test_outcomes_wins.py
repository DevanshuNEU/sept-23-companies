import unittest

from outreach.outcomes import wins

REPLY_KINDS = ("unsorted", "interested", "not_now", "said_no", "asked_to_stop")


class WinIdTest(unittest.TestCase):
    def test_win_id_round_trips_for_every_kind(self):
        for touch_id in (1, 2, 17, 123456):
            for kind in wins.WIN_KINDS:
                self.assertEqual(wins.decode_win_id(wins.win_id(touch_id, kind)), (touch_id, kind))

    def test_win_id_is_touch_times_eight_plus_the_kind_index(self):
        self.assertEqual(wins.WIN_KINDS, ("accepted", "replied", "referred", "interview", "offer"))
        self.assertEqual(wins.win_id(5, "accepted"), 40)
        self.assertEqual(wins.win_id(5, "offer"), 44)

    def test_win_ids_are_all_different(self):
        ids = {wins.win_id(t, k) for t in range(1, 50) for k in wins.WIN_KINDS}
        self.assertEqual(len(ids), 49 * 5)

    def test_a_bad_kind_or_id_is_a_value_error(self):
        with self.assertRaises(ValueError):
            wins.win_id(1, "quiet")
        with self.assertRaises(ValueError):
            wins.win_id(0, "accepted")
        with self.assertRaises(ValueError):
            wins.decode_win_id(8 + 5)    # index 5 is no kind
        with self.assertRaises(ValueError):
            wins.decode_win_id(3)        # touch 0 does not exist


class ClassifyTest(unittest.TestCase):
    def test_log_wins_are_referrals_interviews_offers_and_interested_replies(self):
        self.assertTrue(wins.is_log_win("referred", None))
        self.assertTrue(wins.is_log_win("interview", None))
        self.assertTrue(wins.is_log_win("offer", None))
        self.assertTrue(wins.is_log_win("replied", "interested"))
        self.assertFalse(wins.is_log_win("accepted", None))
        for kind in ("unsorted", "not_now", "said_no", "asked_to_stop", None):
            self.assertFalse(wins.is_log_win("replied", kind), kind)

    def test_recognition_includes_accepts_and_any_human_reply_but_not_said_no_or_asked_to_stop(self):
        # Ruling 7: a said-no reply is not recognition.
        self.assertTrue(wins.is_recognition("accepted", None))
        for kind in ("interested", "not_now", "unsorted"):
            self.assertTrue(wins.is_recognition("replied", kind), kind)
        for kind in ("said_no", "asked_to_stop", "auto_reply"):
            self.assertFalse(wins.is_recognition("replied", kind), kind)
        self.assertFalse(wins.is_recognition("referred", None))
        self.assertFalse(wins.is_recognition("quiet", None))

    def test_a_reply_with_no_kind_yet_is_treated_as_unsorted(self):
        self.assertTrue(wins.is_recognition("replied", None))


class WinLineTest(unittest.TestCase):
    def test_win_line_wording(self):
        self.assertEqual(wins.win_line("accepted", "Dana Whitfield", "Distyl AI", None, "9:41"),
                         "Dana Whitfield accepted your request. 9:41")
        self.assertEqual(wins.win_line("interview", "Dana Whitfield", "Distyl AI", None, "14:05"),
                         "Interview at Distyl AI. 14:05")

    def test_every_line_has_no_dash_and_ends_with_the_time(self):
        for kind in wins.WIN_KINDS:
            for reply_kind in (None, *REPLY_KINDS):
                for person in ("Dana Whitfield", None):
                    line = wins.win_line(kind, person, "Distyl AI", reply_kind, "9:41")
                    self.assertNotIn(chr(0x2014), line)
                    self.assertNotIn(chr(0x2013), line)
                    self.assertNotIn(" - ", line)
                    self.assertTrue(line.endswith(" 9:41"), line)

    def test_reply_lines_say_what_kind_of_reply_it_was(self):
        self.assertEqual(wins.win_line("replied", "Dana Whitfield", "Distyl AI", "interested", "9:41"),
                         "Dana Whitfield replied and sounds interested. 9:41")
        self.assertEqual(wins.win_line("replied", "Dana Whitfield", "Distyl AI", "not_now", "9:41"),
                         "Dana Whitfield replied, not right now. 9:41")
        self.assertEqual(wins.win_line("replied", "Dana Whitfield", "Distyl AI", "unsorted", "9:41"),
                         "Dana Whitfield replied. 9:41")
        self.assertEqual(wins.win_line("referred", "Dana Whitfield", "Distyl AI", None, "9:41"),
                         "Dana Whitfield referred you at Distyl AI. 9:41")
        self.assertEqual(wins.win_line("offer", "Dana Whitfield", "Distyl AI", None, "9:41"),
                         "Offer from Distyl AI. 9:41")

    def test_a_touch_with_no_person_names_the_company(self):
        # An application has no person.
        self.assertEqual(wins.win_line("replied", None, "Distyl AI", "unsorted", "9:41"),
                         "Distyl AI replied. 9:41")
        self.assertEqual(wins.win_line("accepted", "", "Distyl AI", None, "9:41"),
                         "Distyl AI accepted your request. 9:41")

    def test_no_time_means_no_trailing_space(self):
        self.assertEqual(wins.win_line("interview", None, "Distyl AI", None, ""), "Interview at Distyl AI.")

    def test_an_unknown_kind_is_a_value_error(self):
        with self.assertRaises(ValueError):
            wins.win_line("quiet", "Dana", "Distyl AI", None, "9:41")


if __name__ == "__main__":
    unittest.main()
