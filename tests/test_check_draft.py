"""
mail/check_draft.py: the checks the cold-email skill runs on every draft before it is queued (#9 Drafts, #10).
Blocking problems exit 1; style warnings are printed and exit 0. Uses fixture files, never the DevVault list.
"""
import copy, json, os, subprocess, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "mail"))
import check_draft as cd

FIX = os.path.join(ROOT, "tests", "fixtures")
BANNED_FILE, ME_FILE = os.path.join(FIX, "banned_phrases.txt"), os.path.join(FIX, "me.md")
SCRIPT = os.path.join(ROOT, "mail", "check_draft.py")

BODY = """Hi Jane,

I'm Devanshu. I saw your post on how your team tests voice agents before they ship, and I want in on that work.

I built Overhear, a QA analyst for voice agents. Code checks what the call log proves, and an LLM judge checks what the agent said. On a 39-call golden set it caught 23 of 23 planted failures.

You're hiring a Software Engineer, New Grad. Would you refer me, or point me to the person who owns that req?

https://www.devanshuchicholikar.com
https://github.com/DevanshuNEU/overhear

Thanks,
Devanshu Chicholikar"""

CONTACT = {"company": "Acme Voice", "name": "Jane Doe", "email": "jane@acmevoice.com", "kind": "engineer",
           "draft": {"subject": "your voice agent testing post", "body": BODY}}

RECRUITER_BODY = """Hi Sam,

I'm on F-1 OPT and can start now. I'm writing about the Software Engineer, New Grad req (R-1042).

I built OpenCodeIntel, code search for AI coding agents with a 12-tool MCP server. It searches by what code does, not what it is named. Before my MS I spent two years building a Java and Spring Boot quotation platform for B2B customers.

Could we do a short screen on Tuesday at 10 AM or Thursday at 2 PM Eastern?

https://www.devanshuchicholikar.com
https://opencodeintel.com

Thanks,
Devanshu Chicholikar"""


def contact(**draft):
    c = copy.deepcopy(CONTACT)
    c["draft"].update(draft)
    return c


class CheckDraftTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.banned = cd.load_banned(BANNED_FILE)
        cls.unconfirmed = cd.load_unconfirmed(ME_FILE)

    def run_check(self, c, others=(), followup=False):
        return cd.check(c, self.banned, self.unconfirmed, list(others), followup=followup)

    def assertBlocks(self, c, needle, **kw):
        blocks, _ = self.run_check(c, **kw)
        self.assertTrue(any(needle in b for b in blocks), f"no block mentioning {needle!r} in {blocks}")

    def assertWarns(self, c, needle, **kw):
        blocks, warns = self.run_check(c, **kw)
        self.assertEqual(blocks, [])
        self.assertTrue(any(needle in w for w in warns), f"no warning mentioning {needle!r} in {warns}")

    # --- the clean case ---
    def test_clean_engineer_draft_passes(self):
        self.assertEqual(self.run_check(CONTACT), ([], []))

    def test_clean_recruiter_draft_passes(self):
        c = copy.deepcopy(CONTACT); c["kind"] = "recruiter"; c["name"] = "Sam Lee"
        c["draft"] = {"subject": "Software Engineer, New Grad (R-1042), on OPT", "body": RECRUITER_BODY}
        self.assertEqual(self.run_check(c), ([], []))

    # --- loading the lists ---
    def test_load_banned_keeps_email_scopes_only(self):
        sources = [s for s, _ in self.banned]
        self.assertIn("leveraged", sources)
        self.assertIn("MS from Northeastern", sources)
        self.assertNotIn("let.s connect", sources)
        self.assertNotIn("^great post", sources)

    def test_load_banned_translates_posix_classes(self):
        self.assertBlocks(contact(body=BODY.replace("I want in", "it is not just tooling, but I want in")),
                          "banned phrase")

    def test_missing_banned_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            cd.load_banned(os.path.join(FIX, "nope.txt"))

    def test_load_unconfirmed_reads_backticks_on_ask_lines(self):
        self.assertEqual(self.unconfirmed, ["STEM OPT", "H-1B", "no sponsorship needed", "installs"])

    # --- blocking checks ---
    def test_banned_phrase_in_body_blocks_case_insensitive(self):
        self.assertBlocks(contact(body=BODY.replace("I want in", "I am Passionate About it")), "banned phrase")

    def test_banned_phrase_in_subject_blocks(self):
        self.assertBlocks(contact(subject="I'd love to join"), "banned phrase")

    def test_bumping_blocks_even_without_the_list(self):
        blocks, _ = cd.check(contact(body=BODY.replace("I want in", "bumping this up, I want in")), [], [], [])
        self.assertTrue(any("bumping" in b for b in blocks), blocks)

    def test_em_and_en_dashes_block(self):
        for dash in ("\u2014", "\u2013"):
            self.assertBlocks(contact(body=BODY.replace(", and I want", f" {dash} I want")), "dash")
            self.assertBlocks(contact(subject=f"voice agents {dash} testing"), "dash")

    def test_first_email_needs_exactly_two_links(self):
        one = BODY.replace("https://github.com/DevanshuNEU/overhear\n", "")
        self.assertBlocks(contact(body=one), "2 links")
        three = BODY.replace("Thanks,", "https://getsaar.com\n\nThanks,")
        self.assertBlocks(contact(body=three), "2 links")

    def test_portfolio_must_be_first(self):
        swapped = BODY.replace("https://www.devanshuchicholikar.com\nhttps://github.com/DevanshuNEU/overhear",
                               "https://github.com/DevanshuNEU/overhear\nhttps://www.devanshuchicholikar.com")
        self.assertBlocks(contact(body=swapped), "portfolio")

    def test_portfolio_without_www_or_with_slash_is_accepted(self):
        for url in ("https://devanshuchicholikar.com/", "https://www.devanshuchicholikar.com/"):
            self.assertEqual(self.run_check(contact(body=BODY.replace("https://www.devanshuchicholikar.com", url))),
                             ([], []))

    def test_bare_domains_count_as_links(self):
        self.assertEqual(cd.links("see opencodeintel.com and Claude.ai, or https://x.dev/a."),
                         ["opencodeintel.com", "Claude.ai", "https://x.dev/a"])
        self.assertBlocks(contact(body=BODY.replace("I built Overhear", "I built Overhear (on Claude.ai)")), "2 links")

    def test_email_address_is_not_a_link(self):
        self.assertEqual(cd.links("write to chicholikar.d@northeastern.edu"), [])

    def test_shortener_blocks(self):
        self.assertBlocks(contact(body=BODY.replace("https://github.com/DevanshuNEU/overhear", "https://bit.ly/3xYz")),
                          "shortener")

    def test_re_subject_blocks_on_first_email(self):
        self.assertBlocks(contact(subject="Re: your voice agent testing post"), "Re:")

    def test_empty_subject_or_body_blocks(self):
        self.assertBlocks(contact(subject=""), "subject")
        self.assertBlocks(contact(body="  "), "body")

    def test_subject_used_by_another_contact_blocks(self):
        other = {"company": "Other Co", "name": "Ann", "email": "ann@other.co", "draft": {"subject": "Your Voice Agent Testing Post ", "body": "x"}}
        self.assertBlocks(CONTACT, "subject", others=[other])

    def test_near_identical_to_colleague_at_same_company_blocks(self):
        colleague = {"company": "acme voice", "name": "Raj", "email": "raj@acmevoice.com",
                     "draft": {"subject": "something else", "body": BODY.replace("Jane", "Raj")}}
        self.assertBlocks(CONTACT, "near-identical", others=[colleague])

    def test_a_lightly_reworded_colleague_draft_blocks_both_ways(self):
        # A new name, a reworded hook and one changed sentence: 92 percent alike. difflib's autojunk scored it 79
        # percent on emails this long, under the 0.8 line, so both emails would have gone out.
        reworded = BODY.replace("Jane", "Raj").replace(
            "I saw your post on how your team tests voice agents before they ship, and I want in on that work.",
            "I read your talk on how your team catches voice agent regressions before release, and I want to help with that.",
        ).replace("Would you refer me, or point me to the person who owns that req?",
                  "Could you refer me, or tell me who owns that req?")
        raj = {"company": "Acme Voice", "name": "Raj", "email": "raj@acmevoice.com",
               "draft": {"subject": "something else", "body": reworded}}
        self.assertBlocks(CONTACT, "near-identical", others=[raj])
        jane = {"company": "Acme Voice", "name": "Jane Doe", "email": "jane@acmevoice.com",
                "draft": {"subject": "your voice agent testing post", "body": BODY}}
        self.assertBlocks({**raj, "kind": "engineer"}, "near-identical", others=[jane])

    def test_same_body_at_another_company_is_fine(self):
        stranger = {"company": "Other Co", "name": "Raj", "email": "raj@other.co",
                    "draft": {"subject": "something else", "body": BODY}}
        self.assertEqual(self.run_check(CONTACT, others=[stranger]), ([], []))

    def test_placeholders_block(self):
        self.assertBlocks(contact(body=BODY.replace("I'm Devanshu.", "I'm Devanshu. TODO add result.")), "placeholder")
        self.assertBlocks(contact(body=BODY.replace("I'm Devanshu.", "I'm Devanshu. ASK: phone")), "placeholder")

    def test_unconfirmed_me_md_value_blocks(self):
        self.assertBlocks(contact(body=BODY.replace("I'm Devanshu.", "I'm Devanshu, on STEM OPT.")), "unconfirmed")
        self.assertBlocks(contact(body=BODY.replace("I'm Devanshu.", "I'm Devanshu. No H-1B cost.")), "unconfirmed")

    def test_unconfirmed_value_matches_whole_words_only(self):
        blocks, _ = cd.check(contact(body=BODY.replace("voice agents.", "voice agent systems.")), [], ["STEM"], [])
        self.assertEqual(blocks, [])

    def test_unknown_kind_blocks(self):
        c = copy.deepcopy(CONTACT); c["kind"] = "investor"
        self.assertBlocks(c, "kind")

    def test_alum_kind_is_accepted(self):
        c = copy.deepcopy(CONTACT); c["kind"] = "alum"
        self.assertEqual(self.run_check(c), ([], []))

    # --- warnings ---
    def test_short_body_warns(self):
        self.assertWarns(contact(body=BODY.replace(
            "I'm Devanshu. I saw your post on how your team tests voice agents before they ship, and I want in on that work.\n\n", "")),
            "words")

    def test_long_body_warns(self):
        self.assertWarns(contact(body=BODY.replace("Thanks,", "I also built a lot of other things. " * 6 + "\n\nThanks,")),
                         "words")

    def test_recruiter_range_is_tighter(self):
        c = copy.deepcopy(CONTACT); c["kind"] = "recruiter"
        c["draft"]["body"] = BODY.replace("Hi Jane,", "Hi Jane, I'm on F-1 OPT.").replace(
            "Thanks,", "I can also talk through the eval design and the gold set in detail if that helps.\n\nThanks,")
        self.assertWarns(c, "words")

    def test_recruiter_without_work_authorization_warns(self):
        c = copy.deepcopy(CONTACT); c["kind"] = "recruiter"
        c["draft"]["body"] = RECRUITER_BODY.replace("I'm on F-1 OPT and can start now. ", "")
        self.assertWarns(c, "work authorization")

    def test_greeting_without_first_name_warns(self):
        self.assertWarns(contact(body=BODY.replace("Hi Jane,", "Hi there,")), "first name")

    def test_weak_subject_wording_warns(self):
        self.assertWarns(contact(subject="quick question about voice agents"), "subject")

    # --- follow-ups ---
    def test_followup_allows_re_and_fewer_links(self):
        fu = {"company": "Acme Voice", "name": "Jane Doe", "kind": "engineer",
              "subject": "Re: your voice agent testing post",
              "body": "Hi Jane,\n\nOne new thing since my note: Overhear now scores calls against the slots as they were "
                      "during the call. Happy to walk you through it in ten minutes.\n\nThanks,\nDevanshu"}
        self.assertEqual(self.run_check(fu, others=[CONTACT], followup=True), ([], []))

    def test_followup_still_caps_links_and_bans_bumping(self):
        fu = {"company": "Acme Voice", "name": "Jane Doe", "kind": "engineer", "subject": "Re: x",
              "body": "Hi Jane,\n\nJust bumping this up. a.com b.com c.com\n\nDevanshu"}
        blocks, _ = self.run_check(fu, followup=True)
        self.assertTrue(any("bumping" in b for b in blocks), blocks)
        self.assertTrue(any("links" in b for b in blocks), blocks)


class CommandLineTest(unittest.TestCase):
    def run_cli(self, payload, *args):
        return subprocess.run([sys.executable, SCRIPT, "--banned", BANNED_FILE, "--me", ME_FILE, *args],
                              input=payload if isinstance(payload, str) else json.dumps(payload),
                              capture_output=True, text=True, env=os.environ.copy())

    def test_clean_draft_exits_zero_and_reports_word_count(self):
        r = self.run_cli(CONTACT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("words", r.stdout)

    def test_blocking_problem_exits_one(self):
        r = self.run_cli(contact(subject="Re: hello"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("BLOCK:", r.stdout)

    def test_warning_only_exits_zero(self):
        r = self.run_cli(contact(subject="quick question"))
        self.assertEqual(r.returncode, 0)
        self.assertIn("WARN:", r.stdout)

    def test_missing_banned_list_exits_two(self):
        r = subprocess.run([sys.executable, SCRIPT, "--banned", os.path.join(FIX, "nope.txt"), "--me", ME_FILE],
                           input=json.dumps(CONTACT), capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("nope.txt", r.stderr)

    def test_bad_json_exits_two(self):
        self.assertEqual(self.run_cli("{not json").returncode, 2)


if __name__ == "__main__":
    unittest.main()
