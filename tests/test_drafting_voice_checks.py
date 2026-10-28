"""outreach.drafting.voice_checks: the pure voice checks on a finished draft body. Every one warns or passes."""
import unittest

from outreach.drafting.voice_checks import voice_checks

CLEAN = ("Hi Sam,\n\nYour title is the one on the posting. I want it.\n\nI built a small search tool for agents. "
         "It finds the right function first 94% of the time on a research eval of 665 queries across many "
         "codebases, which took a long while to get right. Who owns the req?\n\nhttps://www.devanshuchicholikar.com\n"
         "https://opencodeintel.com\n\nThanks,\nDevanshu Chicholikar")
GROUND = "OpenCodeIntel: 94% Hit@1 on a 665-query research eval. Wrote it since 2025."


def one(code, body, kind="engineer", **kw):
    found = [c for c in voice_checks(body, kind, **kw) if c["code"] == code]
    assert len(found) == 1, f"{code}: {found}"
    return found[0]


class VoiceChecksCase(unittest.TestCase):
    def test_clean_body_passes_every_check(self):
        found = voice_checks(CLEAN, "engineer", ground=GROUND)
        self.assertEqual({c["severity"] for c in found}, {"pass"}, [c for c in found if c["severity"] != "pass"])

    def test_no_check_is_a_block(self):
        bad = "I'd love to work here. Your resume lists a lane. I'm on F-1 OPT. We have 12345 users."
        self.assertNotIn("block", {c["severity"] for c in voice_checks(bad, "engineer", ground="")})

    def test_voice_eager_flags_happy_to(self):
        self.assertEqual(one("voice_eager", CLEAN + " Happy to chat.")["severity"], "warn")

    def test_voice_eager_passes_calm_words(self):
        self.assertEqual(one("voice_eager", CLEAN)["severity"], "pass")

    def test_voice_house_word_flags_genuinely(self):
        self.assertEqual(one("voice_house_word", "I am genuinely into this.")["severity"], "warn")

    def test_voice_house_word_passes_clean(self):
        self.assertEqual(one("voice_house_word", CLEAN)["severity"], "pass")

    def test_voice_system_word_flags_lane(self):
        self.assertEqual(one("voice_system_word", "That is the Digital Engagement lane.")["severity"], "warn")

    def test_voice_system_word_passes_clean(self):
        self.assertEqual(one("voice_system_word", CLEAN)["severity"], "pass")

    def test_voice_recital_flags_your_resume_lists(self):
        self.assertEqual(one("voice_recital", "Your resume lists a FastAPI system.")["severity"], "warn")

    def test_voice_recital_flags_i_noticed_that_you(self):
        self.assertEqual(one("voice_recital", "I noticed that you shipped a thing.")["severity"], "warn")

    def test_voice_recital_passes_clean(self):
        self.assertEqual(one("voice_recital", CLEAN)["severity"], "pass")

    def test_voice_rhythm_flags_not_x_but_y(self):
        self.assertEqual(one("voice_rhythm", "It is not a model problem, it's an accountability problem.")["severity"],
                         "warn")

    def test_voice_rhythm_flags_even_sentences(self):
        body = ("I built a tool for search. It runs on a small box. It reads a lot of code. "
                "It scores each hit well. It ships to a few users.")
        self.assertEqual(one("voice_rhythm", body)["severity"], "warn")

    def test_short_body_skips_rhythm(self):
        self.assertEqual(one("voice_rhythm", "I built it. It works. Who owns it?")["severity"], "pass")

    def test_voice_rhythm_passes_varied_sentences(self):
        self.assertEqual(one("voice_rhythm", CLEAN)["severity"], "pass")

    def test_voice_opt_line_flags_a_non_recruiter(self):
        self.assertEqual(one("voice_opt_line", "I'm on F-1 OPT and can start now.", "engineer")["severity"], "warn")

    def test_recruiter_may_carry_the_opt_line(self):
        self.assertEqual(one("voice_opt_line", "I'm on F-1 OPT and can start now.", "recruiter")["severity"], "pass")

    def test_voice_opt_line_passes_a_clean_engineer_body(self):
        self.assertEqual(one("voice_opt_line", CLEAN, "founder")["severity"], "pass")

    def test_voice_ungrounded_flags_a_number_not_in_the_sources(self):
        c = one("voice_ungrounded", "It handled 12,345 users and 94% hits.", ground=GROUND)
        self.assertEqual(c["severity"], "warn")
        self.assertIn("12,345", c["detail"])
        self.assertNotIn("94%", c["detail"])

    def test_voice_ungrounded_ignores_years(self):
        self.assertEqual(one("voice_ungrounded", "Valid through 2029 and since 2025.", ground="")["severity"], "pass")

    def test_no_digits_passes_ungrounded(self):
        self.assertEqual(one("voice_ungrounded", "No numbers at all here.", ground="")["severity"], "pass")

    def test_a_number_that_is_only_part_of_a_bigger_ground_number_is_ungrounded(self):
        c = one("voice_ungrounded", "It had 66 queries and 20 people.", ground="665-query eval since 2025")
        self.assertEqual(c["severity"], "warn")
        self.assertIn("66", c["detail"])
        self.assertIn("20", c["detail"])

    def test_a_thousands_comma_matches_the_plain_number_in_the_ground(self):
        self.assertEqual(one("voice_ungrounded", "1,808 tests.", ground="1808 Vitest tests")["severity"], "pass")
        self.assertEqual(one("voice_ungrounded", "1808 tests.", ground="1,808 Vitest tests")["severity"], "pass")

    def test_a_percent_matches_its_number_on_a_boundary(self):
        self.assertEqual(one("voice_ungrounded", "94% first.", ground="94% Hit@1")["severity"], "pass")
        self.assertEqual(one("voice_ungrounded", "9% first.", ground="94% Hit@1")["severity"], "warn")

    def test_ground_none_skips_ungrounded(self):
        self.assertNotIn("voice_ungrounded", [c["code"] for c in voice_checks("It handled 99999 users.", "engineer")])

    def test_links_do_not_count_as_numbers(self):
        self.assertEqual(one("voice_ungrounded", "See https://example.com/p/12345 for it.", ground="")["severity"],
                         "pass")


if __name__ == "__main__":
    unittest.main()
