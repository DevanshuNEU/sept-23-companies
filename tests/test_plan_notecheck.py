import tempfile
import unittest
from pathlib import Path

from outreach.drafts.checks import load_banned
from outreach.plan.config import default_plan_config
from outreach.plan.notecheck import Fact, NoteCheck, check_note, is_ready, load_unconfirmed

FIX = Path(__file__).resolve().parent / "fixtures" / "plan"
CFG = default_plan_config()
BANNED, _OK = load_banned(FIX / "banned_phrases.txt", scopes=("both", "dm"))
UNCONFIRMED = load_unconfirmed(FIX / "me.md")
REASON = "I read your post on the queue rewrite"
BODY = f"Hi Dana, {REASON} and built a similar job runner. I would like to hear how your platform team works."
LINK = "https://acme.example/blog/queue"
FACTS = (Fact("queue rewrite", "Dana's post", LINK, True),)


def run(body=BODY, reason=REASON, state="proposed", facts=FACTS, banned=None, unconfirmed=None):
    return check_note(body, reason, state, facts, cfg=CFG, banned=BANNED if banned is None else banned,
                      unconfirmed=UNCONFIRMED if unconfirmed is None else unconfirmed)


def codes(checks, severity=None):
    return [c.code for c in checks if severity is None or c.severity == severity]


def padded(n: int) -> str:
    """A note of exactly n characters that still holds the reason line."""
    body = f"Hi Dana, {REASON} and built a similar job runner."
    return body + "x" * (n - len(body))


class NoteCheckTest(unittest.TestCase):
    def test_fixtures_load(self):
        self.assertEqual([t for t, _ in BANNED], ["synergy", "passionate about"])
        self.assertEqual(UNCONFIRMED, ["STEM OPT", "H-1B", "installs"])

    def test_clean_note_has_only_the_unconfirmed_reason_warning(self):
        checks = run()
        self.assertEqual(codes(checks), ["reason_unconfirmed"])
        self.assertEqual(checks[0].severity, "warn")
        self.assertEqual(codes(run(state="confirmed")), [])
        self.assertEqual(codes(run(state="written")), [])

    def test_length_boundary(self):
        # #9: a connection note may be up to 300 characters (LinkedIn Premium), not 200
        self.assertEqual(CFG.get("note", "max_chars"), 300)
        self.assertEqual(len(padded(300)), 300)
        self.assertNotIn("note_too_long", codes(run(body=padded(300))))
        too_long = [c for c in run(body=padded(301)) if c.code == "note_too_long"]
        self.assertEqual([c.severity for c in too_long], ["block"])
        self.assertEqual(too_long[0].detail, "301 characters; the limit is 300. Cut 1.")
        # each emoji and curly quote is one character
        tail = "\U0001F600“”"
        body = padded(297) + tail
        self.assertEqual(len(body), 300)
        self.assertNotIn("note_too_long", codes(run(body=body)))
        self.assertIn("note_too_long", codes(run(body=body + "x")))

    def test_empty_note_blocks(self):
        self.assertIn("note_empty", codes(run(body="   ")))

    def test_banned_phrase_from_both_and_dm_scopes_but_not_email_scope(self):
        hit = run(body=BODY + " Great synergy.")
        self.assertEqual([c.code for c in hit if c.code == "banned_phrase"], ["banned_phrase"])
        banned = [c for c in hit if c.code == "banned_phrase"][0]
        self.assertEqual(banned.severity, "block")
        start = (BODY + " Great synergy.").index("synergy")
        self.assertEqual(banned.spans, ((start, start + 7),))
        self.assertIn("banned_phrase", codes(run(body=BODY + " I am passionate about queues.")))
        self.assertNotIn("banned_phrase", codes(run(body=BODY + " Hope this finds you well.")))

    def test_one_banned_check_per_phrase(self):
        both = run(body=BODY + " Synergy, and passionate about it.")
        self.assertEqual(codes(both).count("banned_phrase"), 2)

    def test_extra_banned_referral_demand(self):
        checks = run(body=f"Hi Dana, {REASON}. Can you refer me?")
        self.assertIn("banned_phrase", codes(checks, "block"))

    def test_ms_from_northeastern_blocks(self):
        checks = run(body=f"Hi Dana, {REASON}. I have an MS from Northeastern.")
        self.assertIn("banned_phrase", codes(checks, "block"))

    def test_dash_blocks(self):
        for dash in ("\u2013", "\u2014"):
            checks = run(body=BODY.replace("job runner", f"job{dash}runner"))
            self.assertIn("dash", codes(checks, "block"))
            dash_check = [c for c in checks if c.code == "dash"][0]
            self.assertEqual(len(dash_check.spans), 1)

    def test_placeholder_blocks(self):
        for text in ("TODO", "ASK: x", "{{x}}"):
            self.assertIn("placeholder", codes(run(body=f"{BODY} {text}"), "block"), text)

    def test_unconfirmed_me_fact_blocks(self):
        for value in ("H-1B", "STEM OPT", "installs"):
            self.assertIn("unconfirmed_fact", codes(run(body=f"{BODY} I need {value} support."), "block"), value)
        self.assertNotIn("unconfirmed_fact", codes(run(body=BODY.replace("platform", "installsystem"))))
        self.assertEqual(codes(run(unconfirmed=[])), ["reason_unconfirmed"])

    def test_unreadable_me_list_blocks(self):
        checks = check_note(BODY, REASON, "confirmed", FACTS, cfg=CFG, banned=BANNED, unconfirmed=None)
        self.assertEqual(codes(checks, "block"), ["unconfirmed_list_unreadable"])

    def test_link_rules(self):
        self.assertIn("link_shortener", codes(run(body=f"{BODY} See bit.ly/abc"), "block"))
        self.assertIn("link_shortener", codes(run(body=f"{BODY} See https://t.co/abc"), "block"))
        three = run(body=f"{BODY} https://a.example/1 https://b.example/2 https://c.example/3")
        self.assertIn("too_many_links", codes(three, "warn"))
        self.assertNotIn("too_many_links", codes(run(body=f"{BODY} https://a.example/1 https://b.example/2")))
        self.assertNotIn("too_many_links", codes(run()))
        self.assertNotIn("link_shortener", codes(run()))

    def test_fact_without_a_safe_link_blocks(self):
        for url in ("", "http://a.example/x", "https://user:pw@a.example/x", "https://www.linkedin.com/in/x",
                    "https://lnkd.in/abc", "javascript:alert(1)", "https://bit.ly/x", "https://a.example/ x"):
            checks = run(facts=(Fact("queue rewrite", "src", url, True),))
            self.assertIn("fact_no_link", codes(checks, "block"), url)
        self.assertNotIn("fact_no_link", codes(run(facts=(Fact("queue rewrite", "src", "https://a.example/x", True),))))

    def test_fact_not_in_note_warns(self):
        checks = run(facts=(Fact("Series B", "press", "https://a.example/x", True),))
        self.assertIn("fact_not_in_note", codes(checks, "warn"))
        quiet = run(facts=(Fact("Series B", "press", "https://a.example/x", False),))
        self.assertNotIn("fact_not_in_note", codes(quiet))
        loud = run(facts=(Fact("QUEUE REWRITE", "press", "https://a.example/x", True),))
        self.assertNotIn("fact_not_in_note", codes(loud))

    def test_number_not_linked_warns(self):
        body = BODY + " You grew to 40 people in 2025."
        checks = run(body=body)
        self.assertIn("number_not_linked", codes(checks, "warn"))
        nums = [c for c in checks if c.code == "number_not_linked"][0]
        self.assertEqual([body[a:b] for a, b in nums.spans], ["40", "2025"])
        covered = run(body=body, facts=FACTS + (Fact("40 people in 2025", "press", "https://a.example/x", True),))
        self.assertNotIn("number_not_linked", codes(covered))
        # a fact with no safe link does not vouch for its number
        unsafe = run(body=body, facts=(Fact("40 people", "press", "", True),))
        self.assertIn("number_not_linked", codes(unsafe))
        # digits inside a link are the link's business
        self.assertNotIn("number_not_linked", codes(run(body=BODY + " https://a.example/p2025")))

    def test_reason_line_rules(self):
        self.assertIn("reason_missing", codes(run(reason=""), "block"))
        self.assertIn("reason_missing", codes(run(reason="  "), "block"))
        self.assertIn("reason_not_in_note", codes(run(reason="Something else entirely"), "block"))
        self.assertIn("reason_unconfirmed", codes(run(state="proposed"), "warn"))
        for state in ("confirmed", "written"):
            self.assertNotIn("reason_unconfirmed", codes(run(state=state)))
        self.assertFalse(is_ready(run(state="proposed"), "proposed"))
        self.assertTrue(is_ready(run(state="confirmed"), "confirmed"))
        self.assertTrue(is_ready(run(state="written"), "written"))
        blocked = run(state="confirmed", body=BODY + " Synergy.")
        self.assertFalse(is_ready(blocked, "confirmed"))

    def test_unreadable_banned_list_blocks_instead_of_passing(self):
        """`load_banned` returns an empty list when the file cannot be used: the note must not pass on that."""
        checks = run(state="confirmed", banned=[])
        self.assertEqual(codes(checks, "block"), ["banned_list_unreadable"])
        self.assertFalse(is_ready(checks, "confirmed"))

    def test_to_json_has_the_check_shape(self):
        check = [c for c in run(body=BODY + " Synergy.") if c.code == "banned_phrase"][0]
        data = check.to_json()
        self.assertEqual(set(data), {"code", "severity", "title", "detail", "fix", "spans"})
        self.assertIsNone(data["fix"])
        self.assertTrue(all(set(s) == {"field", "start", "end"} and s["field"] == "body" for s in data["spans"]))
        self.assertEqual(NoteCheck.from_json(data), check)

    def test_no_dashes_in_check_text(self):
        for c in run(body="x" * 300 + " TODO synergy \u2014 bit.ly/a", reason="", facts=(Fact("a", "b", "", True),)):
            for text in (c.title, c.detail):
                self.assertNotRegex(text, "[‒-―]")


# The Kabir case (2026-10-26): his own 297 character note, saved over a night draft whose reason line and fact list
# describe Claude's text, not his.
KABIR_REASON = "I saw the forward deployed engineer role in New York."
KABIR_FACTS = (Fact("forward deployed engineer role in New York", "Careers page", "https://tessera.example/jobs/fde",
                    True),)
KABIR_BODY = ("Hi Kabir. I saw Tessera is hiring a forward deployed engineer in NYC and I would like to be considered. "
              "I build agent infra that real users run each day and I like sitting with customers to ship the fixes. "
              "Would you be open to a short call this week? My work is at devanshu.dev if you want a look.")


def his(body=KABIR_BODY, reason=KABIR_REASON, state="proposed", facts=KABIR_FACTS, banned=None, unconfirmed=None):
    return check_note(body, reason, state, facts, cfg=CFG, banned=BANNED if banned is None else banned,
                      unconfirmed=UNCONFIRMED if unconfirmed is None else unconfirmed, author="user")


class HisNoteTest(unittest.TestCase):
    """#9's authorship ruling (2026-10-26): a note he wrote or reworked is judged only on honesty and hard limits."""

    def test_the_kabir_note_passes_and_is_ready(self):
        self.assertEqual(len(KABIR_BODY), 297)
        self.assertNotIn(KABIR_REASON, KABIR_BODY)
        checks = his()
        self.assertEqual(codes(checks, "block"), [])
        self.assertEqual(codes(checks), [])
        self.assertTrue(is_ready(checks, "proposed", author="user"))

    def test_the_same_text_as_claudes_keeps_the_strict_checks(self):
        claude = check_note(KABIR_BODY, KABIR_REASON, "proposed", KABIR_FACTS, cfg=CFG, banned=BANNED,
                            unconfirmed=UNCONFIRMED)
        self.assertEqual(codes(claude), ["reason_not_in_note", "fact_not_in_note", "reason_unconfirmed"])
        self.assertEqual(codes(claude, "block"), ["reason_not_in_note"])
        self.assertFalse(is_ready(claude, "proposed"))
        explicit = check_note(KABIR_BODY, KABIR_REASON, "proposed", KABIR_FACTS, cfg=CFG, banned=BANNED,
                              unconfirmed=UNCONFIRMED, author="claude")
        self.assertEqual(explicit, claude)
        self.assertFalse(is_ready(his(), "proposed"))  # is_ready keeps Claude's rule unless told otherwise
        self.assertIn("reason_missing", codes(run(reason=""), "block"))

    def test_no_reason_line_and_no_fact_rules_for_his_note(self):
        self.assertEqual(codes(his(reason="")), [])
        self.assertEqual(codes(his(reason="", facts=())), [])
        self.assertEqual(codes(his(facts=(Fact("a fact with no page", "src", "", True),))), [])
        for state in ("proposed", "confirmed", "written"):
            self.assertEqual(codes(his(state=state)), [], state)
            self.assertTrue(is_ready(his(state=state), state, author="user"), state)

    def test_over_the_limit_still_blocks(self):
        long = KABIR_BODY + "abcd"
        self.assertEqual(len(long), 301)
        self.assertEqual(codes(his(body=long), "block"), ["note_too_long"])
        self.assertFalse(is_ready(his(body=long), "written", author="user"))
        self.assertEqual(codes(his(body=KABIR_BODY + "abc")), [])

    def test_honesty_checks_still_block(self):
        cases = {
            "banned_phrase": [" Real synergy.", " I am passionate about agents.", " Could you refer me?",
                              " We have a mutual connection.", " I have an MS from Northeastern."],
            "dash": [" Agents \u2014 infra.", " Agents \u2013 infra."],
            "placeholder": [" TODO", " ASK: x", " {{name}}"],
            "unconfirmed_fact": [" I need H-1B support.", " No STEM OPT needed."],
            "link_shortener": [" bit.ly/abc"],
        }
        short = KABIR_BODY[:200]
        for code, tails in cases.items():
            for tail in tails:
                checks = his(body=short + tail)
                self.assertIn(code, codes(checks, "block"), tail)
                self.assertFalse(is_ready(checks, "written", author="user"), tail)
        self.assertEqual(codes(his(body="   "), "block"), ["note_empty"])

    def test_unreadable_lists_still_block(self):
        self.assertEqual(codes(his(banned=[]), "block"), ["banned_list_unreadable"])
        checks = check_note(KABIR_BODY, "", "written", (), cfg=CFG, banned=BANNED, unconfirmed=None, author="user")
        self.assertEqual(codes(checks, "block"), ["unconfirmed_list_unreadable"])

    def test_link_count_and_numbers_still_warn(self):
        short = KABIR_BODY[:150]
        three = his(body=short + " https://a.example/1 https://b.example/2 https://c.example/3")
        self.assertEqual(codes(three), ["too_many_links"])
        self.assertEqual(codes(his(body=short + " We grew to 40 people.")), ["number_not_linked"])
        self.assertTrue(is_ready(three, "proposed", author="user"))

    def test_an_unknown_author_is_refused(self):
        for author in ("devanshu", "", "User"):
            with self.assertRaises(ValueError):
                check_note(KABIR_BODY, "", "written", (), cfg=CFG, banned=BANNED, unconfirmed=UNCONFIRMED,
                           author=author)
            with self.assertRaises(ValueError):
                is_ready([], "written", author=author)


class UnconfirmedLoaderTest(unittest.TestCase):
    def test_reads_backticks_on_ask_items_only(self):
        self.assertEqual(load_unconfirmed(FIX / "me.md"), ["STEM OPT", "H-1B", "installs"])

    def test_unreadable_file_is_none(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(load_unconfirmed(Path(folder) / "missing.md"))


if __name__ == "__main__":
    unittest.main()
