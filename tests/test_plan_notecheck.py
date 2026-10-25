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
        self.assertEqual(len(padded(200)), 200)
        self.assertNotIn("note_too_long", codes(run(body=padded(200))))
        too_long = [c for c in run(body=padded(201)) if c.code == "note_too_long"]
        self.assertEqual([c.severity for c in too_long], ["block"])
        # each emoji and curly quote is one character
        tail = "\U0001F600“”"
        body = padded(197) + tail
        self.assertEqual(len(body), 200)
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


class UnconfirmedLoaderTest(unittest.TestCase):
    def test_reads_backticks_on_ask_items_only(self):
        self.assertEqual(load_unconfirmed(FIX / "me.md"), ["STEM OPT", "H-1B", "installs"])

    def test_unreadable_file_is_none(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(load_unconfirmed(Path(folder) / "missing.md"))


if __name__ == "__main__":
    unittest.main()
