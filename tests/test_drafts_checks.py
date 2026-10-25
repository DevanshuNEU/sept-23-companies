"""The approve checks (#11 Task 17): the #9 safety blocks, the style warns and the passes, read on current data.

Every test runs on a scratch store and the fixture banned list, never the DevVault file.
"""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from outreach.drafts import checks
from outreach.drafts.checks import DraftInput, cached_mx, draft_input, load_banned, run_checks
from outreach.outcomes.context import build_context
from tests import apiseed as seed
from tests.storetest import fresh_store

NOW = "2026-11-10T15:14:00Z"
FIXTURE_BANNED = Path(__file__).resolve().parent / "fixtures" / "banned_phrases.txt"
PORTFOLIO = "https://www.devanshuchicholikar.com"
PROJECT = "https://github.com/devanshu/runner"
SUBJECT = "Your queue rewrite and the new grad backend role"
HOOK = "His post on the queue rewrite (2026-11-02)"
BODY = (
    "Hi Pat,\n\n"
    "I saw Acme just opened a new grad backend role, and your post on the queue rewrite is why I'm writing. "
    "I build the same kind of thing. Last spring I wrote a job runner in Go that moved forty thousand tasks an hour "
    "with retries and clean shutdowns, and I want to bring that to your team.\n\n"
    f"My work is at {PORTFOLIO} and the runner is at {PROJECT}.\n\n"
    "Would you be open to referring me for the role, or pointing me to the right person? "
    "I'm happy to send anything that helps, and I can keep it short.\n\n"
    "Thanks,\nDevanshu"
)
CLEAN_CODES = [
    "no_mail_server", "placeholder", "too_many_links", "link_shortener", "near_identical", "no_go_person", "said_no",
    "closed_no_sponsorship", "banned_phrase", "dash", "length", "stale_hook", "subject_wording", "subject_reused",
    "link_count", "recruiter",
]
NO_GO_REASONS = ("no_sponsorship", "citizenship", "over_300", "defence_contractor", "agency", "said_no",
                 "stop_reply", "not_a_fit", "other")


def send_first(conn, touch_id: int, *, body: str, subject: str, at: str = "2026-11-05T15:00:00Z") -> int:
    """The touch's first email, sent: the thread columns the CHECKs want, then the sent seq 0 message."""
    conn.execute("UPDATE touch SET first_sent_at = ?, first_rfc_id = ?, gmail_thread_id = ? WHERE touch_id = ?",
                 (at, f"<rfc{touch_id}@x>", f"thread{touch_id}", touch_id))
    return seed.message(conn, touch_id, 0, "sent", subject=subject, body=body, sent_at=at)


def words(n: int, *, links: tuple[str, ...] = (PORTFOLIO, PROJECT)) -> str:
    """A body of exactly `n` counted words (links are not counted) with the given links at the end."""
    return "Hi Pat, " + " ".join(["plain"] * (n - 2)) + "\n" + " ".join(links)


class ChecksCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.banned, ok = load_banned(FIXTURE_BANNED)
        self.assertTrue(ok)
        self.acme = seed.company(self.conn, "Acme", "working")
        self.pat = seed.person(self.conn, self.acme, "Pat Lee", email="pat@acme.example")
        self.touch = seed.touch(self.conn, self.pat)
        self.mid = seed.message(self.conn, self.touch, 0, subject=SUBJECT, body=BODY)
        self.conn.execute("UPDATE message SET hook = ? WHERE message_id = ?", (HOOK, self.mid))

    # helpers

    def draft(self, mid: int | None = None, **changes) -> DraftInput:
        return replace(draft_input(self.conn, mid or self.mid, now=NOW), **{"mx": "ok", **changes})

    def run_on(self, d: DraftInput | None = None, *, banned=None, **changes) -> list[dict]:
        d = d or self.draft(**changes)
        return run_checks(self.conn, d, banned=self.banned if banned is None else banned, now=NOW)

    @staticmethod
    def codes(found: list[dict], severity: str) -> list[str]:
        return [c["code"] for c in found if c["severity"] == severity]

    def one(self, found: list[dict], code: str) -> dict:
        matches = [c for c in found if c["code"] == code]
        self.assertEqual(len(matches), 1, f"expected one {code} check, got {found}")
        return matches[0]

    def assert_blocks(self, found: list[dict], code: str) -> dict:
        check = self.one(found, code)
        self.assertEqual(check["severity"], "block", check)
        self.assertTrue(check["title"] and check["detail"], check)
        return check

    def assert_passes(self, found: list[dict], code: str) -> None:
        self.assertEqual(self.one(found, code)["severity"], "pass")

    def colleague(self, company_id: int, name: str = "Sam Wu", *, body: str = BODY, subject: str = "Another subject",
                  state: str = "drafted") -> tuple[int, int]:
        person = seed.person(self.conn, company_id, name)
        mid = seed.message(self.conn, seed.touch(self.conn, person), 0, state, subject=subject, body=body)
        return person, mid

    def no_go(self, *, person_id: int | None = None, company_id: int | None = None, reason: str = "other",
              source: str = "devanshu", lifted: bool = False) -> int:
        cur = self.conn.execute(
            "INSERT INTO no_go(person_id, company_id, reason, source, created_at, lifted_at, lift_reason) "
            "VALUES (?,?,?,?,?,?,?)",
            (person_id, company_id, reason, source, seed.T0, seed.T0 if lifted else None,
             "Mistake" if lifted else None))
        return cur.lastrowid

    def lift(self, no_go_id: int) -> None:
        self.conn.execute("UPDATE no_go SET lifted_at = ?, lift_reason = 'Mistake' WHERE no_go_id = ?",
                          (seed.T0, no_go_id))


class Blocks(ChecksCase):
    def test_no_mail_server_blocks(self):
        self.assert_blocks(self.run_on(mx="none"), "no_mail_server")
        self.assert_passes(self.run_on(mx="ok"), "no_mail_server")

    def test_no_address_or_a_malformed_domain_is_no_mail_server(self):
        for email in (None, "", "pat", "pat@", "pat@-acme-.com", "pat@acme..com"):
            with self.subTest(email=email):
                for mx in ("ok", "unknown"):
                    found = self.run_on(person_email=email, mx=mx)
                    self.assert_blocks(found, "no_mail_server")
                    self.assertNotIn("mail_server_unknown", [c["code"] for c in found])

    def test_placeholder_text_blocks(self):
        for field, text in (("body", "TODO"), ("body", "ASK: which team"), ("subject", "{{role}}"),
                            ("body", "end }}")):
            with self.subTest(field=field, text=text):
                if field == "body":
                    body, subject = BODY.replace("Thanks,", f"{text} Thanks,"), SUBJECT
                else:
                    body, subject = BODY, f"{SUBJECT} {text}"
                check = self.assert_blocks(self.run_on(body=body, subject=subject), "placeholder")
                source = body if field == "body" else subject
                self.assertTrue(check["spans"] and all(s["field"] == field for s in check["spans"]), check)
                self.assertTrue(all(source[s["start"]:s["end"]] in ("TODO", "ASK:", "{{", "}}")
                                    for s in check["spans"]), check)
        with self.subTest("words that only look like one pass"):
            self.assert_passes(self.run_on(body=BODY.replace("Thanks,", "I'll ask: todo lists. Thanks,")),
                               "placeholder")

    def test_three_links_block(self):
        body = BODY.replace(f"at {PROJECT}.", f"at {PROJECT} and {PROJECT}/docs.")
        check = self.assert_blocks(self.run_on(body=body), "too_many_links")
        self.assertEqual(len(check["spans"]), 3)
        self.assertTrue(all(s["field"] == "body" for s in check["spans"]))
        self.assertEqual(body[check["spans"][0]["start"]:check["spans"][0]["end"]], PORTFOLIO)
        self.assert_passes(self.run_on(), "too_many_links")

    def test_bare_domain_counts_as_a_link(self):
        self.assert_blocks(self.run_on(body=BODY + "\nThe team page is acme.com/team."), "too_many_links")
        # an email address is not a link
        self.assert_passes(self.run_on(body=BODY + "\nOr write to me at dev@gmail.com."), "too_many_links")

    def test_shortener_blocks(self):
        for link in ("https://bit.ly/abc123", "lnkd.in/xyz", "https://www.tinyurl.com/q"):
            with self.subTest(link=link):
                body = BODY.replace(PROJECT, link)
                check = self.assert_blocks(self.run_on(body=body), "link_shortener")
                span = check["spans"][0]
                self.assertEqual(body[span["start"]:span["end"]], link)
        self.assert_passes(self.run_on(), "link_shortener")

    def test_near_identical_colleague_draft_blocks_with_a_compare_fix(self):
        _, sam_mid = self.colleague(self.acme)
        check = self.assert_blocks(self.run_on(), "near_identical")
        self.assertEqual(check["fix"], {"kind": "compare", "otherMessageId": sam_mid})

    def test_near_identical_survives_a_few_changed_words(self):
        self.colleague(self.acme, body=BODY.replace("forty thousand", "fifty thousand"))
        self.assert_blocks(self.run_on(), "near_identical")

    def test_a_long_near_copy_blocks_in_both_directions(self):
        # The final review's pair: about 94% alike, but difflib's autojunk scored it 66% one way and 89% the other.
        variant = (BODY.replace("Hi Pat,", "Hi Sam,")
                   .replace("your post on the queue rewrite is why I'm writing",
                            "your talk on the billing service is why I'm reaching out")
                   .replace("with retries and clean shutdowns", "with retries, backoff and clean restarts"))
        self.assertGreater(len(BODY), 200)
        _, sam_mid = self.colleague(self.acme, body=variant)
        with self.subTest("his draft is the test body"):
            self.assert_blocks(self.run_on(), "near_identical")
        with self.subTest("his draft is the variant"):
            self.conn.execute("UPDATE message SET body = ? WHERE message_id = ?", (BODY, sam_mid))
            self.assert_blocks(self.run_on(body=variant), "near_identical")

    def test_a_skipped_colleague_draft_does_not_count(self):
        _, mid = self.colleague(self.acme)
        self.conn.execute("UPDATE message SET state = 'skipped' WHERE message_id = ?", (mid,))
        self.assert_passes(self.run_on(), "near_identical")

    def test_a_sent_colleague_email_counts(self):
        _, mid = self.colleague(self.acme, state="sent")
        self.assertEqual(self.assert_blocks(self.run_on(), "near_identical")["fix"]["otherMessageId"], mid)

    def test_the_same_body_at_another_company_is_fine(self):
        self.colleague(seed.company(self.conn, "Other", "working"), "Lee Ray")
        self.assert_passes(self.run_on(), "near_identical")

    def test_a_colleague_at_a_merged_duplicate_counts(self):
        dup = seed.company(self.conn, "Acme Inc", "removed", merged_into=self.acme)
        _, mid = self.colleague(dup, "Jo Park")
        self.assertEqual(self.assert_blocks(self.run_on(), "near_identical")["fix"]["otherMessageId"], mid)

    def test_his_own_earlier_email_to_the_same_person_is_not_a_colleague(self):
        other = seed.company(self.conn, "Other", "working")
        kim = seed.person(self.conn, other, "Kim Day")
        touch = seed.touch(self.conn, kim)
        send_first(self.conn, touch, body=BODY, subject="First")
        mid = seed.message(self.conn, touch, 1, subject="Re: First", body=BODY)
        self.assert_passes(self.run_on(self.draft(mid)), "near_identical")

    def test_a_different_colleague_body_passes(self):
        self.colleague(self.acme, "Ana Cruz", body=words(95))
        self.assert_passes(self.run_on(), "near_identical")

    def test_company_no_go_blocks(self):
        with self.subTest("on the company"):
            ng = self.no_go(company_id=self.acme, reason="no_sponsorship")
            check = self.assert_blocks(self.run_on(), "no_go_person")
            self.assertEqual(check["fix"], {"kind": "no_go", "noGoId": ng})
            self.lift(ng)
        dup = seed.company(self.conn, "Acme Inc", "removed", merged_into=self.acme)
        with self.subTest("on a merged duplicate of the company"):
            ng = self.no_go(company_id=dup, reason="over_300")
            self.assertEqual(self.assert_blocks(self.run_on(), "no_go_person")["fix"]["noGoId"], ng)
            self.lift(ng)
        with self.subTest("a person at the duplicate, the no-go on the survivor"):
            jo = seed.person(self.conn, dup, "Jo Park")
            mid = seed.message(self.conn, seed.touch(self.conn, jo), 0, subject="A fresh one", body=words(95))
            ng = self.no_go(company_id=self.acme)
            self.assertEqual(self.assert_blocks(self.run_on(self.draft(mid)), "no_go_person")["fix"]["noGoId"], ng)
            self.lift(ng)
        with self.subTest("a no-go on an unrelated company blocks nothing"):
            self.no_go(company_id=seed.company(self.conn, "Other"))
            self.assert_passes(self.run_on(), "no_go_person")

    def test_person_no_go_blocks_with_its_fix(self):
        cases = [(reason, "devanshu") for reason in NO_GO_REASONS] + [
            ("said_no", "reply"), ("stop_reply", "reply"), ("not_a_fit", "claude"), ("other", "import"),
            ("agency", "rules")]
        for reason, source in cases:
            with self.subTest(reason=reason, source=source):
                ng = self.no_go(person_id=self.pat, reason=reason, source=source)
                check = self.assert_blocks(self.run_on(), "no_go_person")
                self.assertEqual(check["fix"], {"kind": "no_go", "noGoId": ng})
                self.lift(ng)
                self.assert_passes(self.run_on(), "no_go_person")

    def test_a_lifted_no_go_blocks_nothing(self):
        dup = seed.company(self.conn, "Acme Inc", "removed", merged_into=self.acme)
        self.no_go(person_id=self.pat, reason="said_no", source="reply", lifted=True)
        self.no_go(company_id=self.acme, lifted=True)
        self.no_go(company_id=dup, lifted=True)
        found = self.run_on()
        self.assertEqual(self.codes(found, "block"), [])
        self.assert_passes(found, "no_go_person")

    def test_another_persons_no_go_does_not_block(self):
        sam, _ = self.colleague(self.acme, body=words(95))
        self.no_go(person_id=sam, reason="said_no", source="reply")
        self.assert_passes(self.run_on(), "no_go_person")

    def test_said_no_reply_blocks(self):
        for kind in ("said_no", "asked_to_stop"):
            with self.subTest(channel="email", kind=kind):
                self.conn.execute("UPDATE touch SET state = 'replied', replied_at = ?, reply_kind = ? "
                                  "WHERE touch_id = ?", (seed.T0, kind, self.touch))
                self.assert_blocks(self.run_on(), "said_no")
        for kind in ("interested", "not_now", "unsorted"):
            with self.subTest(channel="email", kind=kind):
                self.conn.execute("UPDATE touch SET reply_kind = ? WHERE touch_id = ?", (kind, self.touch))
                self.assert_passes(self.run_on(), "said_no")

    def test_said_no_on_linkedin_blocks_the_email_too(self):
        context = json.dumps(build_context(source="own", lane="own", role_kind="engineer", role_age_days=None,
                                           ranker_version=None, signals={}, role_named=False))
        cur = self.conn.execute(
            "INSERT INTO outreach_touch(person_id, company_id, channel, sent_at, replied_at, reply_kind, context, "
            "created_at) VALUES (?,?,?,?,?,?,?,?)",
            (self.pat, self.acme, "linkedin_request", seed.T0, seed.T0, "asked_to_stop", context, seed.T0))
        self.assert_blocks(self.run_on(), "said_no")
        with self.subTest("another reply kind on LinkedIn does not block"):
            self.conn.execute("UPDATE outreach_touch SET reply_kind = 'not_now' WHERE touch_id = ?", (cur.lastrowid,))
            self.assert_passes(self.run_on(), "said_no")

    def test_closed_for_no_sponsorship_blocks(self):
        self.conn.execute("UPDATE company SET status = 'closed', close_reason = 'no_sponsorship' "
                          "WHERE company_id = ?", (self.acme,))
        self.assert_blocks(self.run_on(), "closed_no_sponsorship")
        with self.subTest("a person at a merged duplicate of the closed company"):
            dup = seed.company(self.conn, "Acme Inc", "removed", merged_into=self.acme)
            jo = seed.person(self.conn, dup, "Jo Park")
            mid = seed.message(self.conn, seed.touch(self.conn, jo), 0, subject="A fresh one", body=words(95))
            self.assert_blocks(self.run_on(self.draft(mid)), "closed_no_sponsorship")
        with self.subTest("closed for another reason does not block"):
            self.conn.execute("UPDATE company SET close_reason = 'not_now' WHERE company_id = ?", (self.acme,))
            self.assert_passes(self.run_on(), "closed_no_sponsorship")

    def test_follow_up_subject_must_thread_on_the_first_email(self):
        kim = seed.person(self.conn, self.acme, "Kim Day")
        touch = seed.touch(self.conn, kim)
        seed.send_first(self.conn, touch)  # seq 0, subject "Hello"
        mid = seed.message(self.conn, touch, 1, subject="Re: Hello", body=words(60))
        found = self.run_on(self.draft(mid))
        self.assert_passes(found, "followup_subject")
        self.assertNotIn("subject_wording", self.codes(found, "warn"))  # Re: is right on a follow-up
        for subject in ("Hello again", "re: Hello", "Re: Hello!", "Hello"):
            with self.subTest(subject=subject):
                self.assert_blocks(self.run_on(self.draft(mid, subject=subject)), "followup_subject")
        with self.subTest("a first email has no follow-up subject check"):
            self.assertNotIn("followup_subject", [c["code"] for c in self.run_on()])

    def test_follow_up_with_no_first_email_on_record_blocks(self):
        # Seed a real follow-up, then check it as though its first email were not on record (an imported touch).
        kim = seed.person(self.conn, self.acme, "Kim Day")
        touch = seed.touch(self.conn, kim)
        send_first(self.conn, touch, body=words(95), subject="Hello")
        mid = seed.message(self.conn, touch, 1, subject="Re: Hello", body=words(60))
        stranger = seed.person(self.conn, self.acme, "Lee Ray")  # has no messages at all
        self.assert_blocks(self.run_on(self.draft(mid, person_id=stranger)), "followup_subject")

    def test_missing_banned_list_blocks_and_does_not_crash(self):
        with tempfile.TemporaryDirectory(prefix="outreach-banned-") as folder:
            banned, ok = load_banned(Path(folder) / "missing.txt")
        self.assertEqual((banned, ok), ([], False))
        found = self.run_on(banned=banned)
        check = self.assert_blocks(found, "banned_list_missing")
        self.assertIn("banned phrases", check["detail"])
        self.assertNotIn("banned_phrase", self.codes(found, "pass"))  # nothing was checked, so nothing passes

    def test_an_unusable_banned_list_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="outreach-banned-") as folder:
            for name, text in (("dm-only.txt", "dm\tlet.s connect\n"), ("bad-regex.txt", "both\t(unclosed\n"),
                               ("empty.txt", ""), ("not-utf8.txt", None)):
                with self.subTest(name=name):
                    path = Path(folder) / name
                    if text is None:
                        path.write_bytes(b"both\t\xff\xfe\n")
                    else:
                        path.write_text(text, encoding="utf-8")
                    self.assertEqual(load_banned(path), ([], False))
            with self.subTest("a folder is not a list"):
                self.assertEqual(load_banned(Path(folder)), ([], False))

    def test_each_block_is_found_on_current_data_not_stored_checks(self):
        saved = self.run_on()
        self.assertEqual(self.codes(saved, "block"), [])
        self.conn.execute("UPDATE message SET checks = ? WHERE message_id = ?", (json.dumps(saved), self.mid))
        steps = [
            ("no_go_person", lambda: self.no_go(person_id=self.pat)),
            ("near_identical", lambda: self.colleague(self.acme)),
            ("closed_no_sponsorship", lambda: self.conn.execute(
                "UPDATE company SET status = 'closed', close_reason = 'no_sponsorship' WHERE company_id = ?",
                (self.acme,))),
            ("said_no", lambda: self.conn.execute(
                "UPDATE touch SET state = 'replied', replied_at = ?, reply_kind = 'said_no' WHERE touch_id = ?",
                (seed.T0, self.touch))),
        ]
        for code, change in steps:
            with self.subTest(code=code):
                change()
                self.assertIn(code, self.codes(self.run_on(), "block"))
        stored = self.conn.execute("SELECT checks FROM message WHERE message_id = ?", (self.mid,)).fetchone()[0]
        self.assertEqual(json.loads(stored), saved)  # run_checks never wrote them


class Warns(ChecksCase):
    def test_banned_phrase_and_dash_are_warns(self):
        subject = "Passionate about queues"
        body = BODY.replace("I want to bring", "I'd love to bring").replace(", and your post", " \u2014 your post")
        found = self.run_on(subject=subject, body=body)
        self.assertEqual(self.codes(found, "block"), [])
        banned = self.one(found, "banned_phrase")
        self.assertEqual(banned["severity"], "warn")
        texts = sorted((s["field"], (subject if s["field"] == "subject" else body)[s["start"]:s["end"]])
                       for s in banned["spans"])
        self.assertEqual(texts, [("body", "I'd love to"), ("subject", "Passionate about")])
        dash = self.one(found, "dash")
        self.assertEqual(dash["severity"], "warn")
        self.assertEqual([body[s["start"]:s["end"]] for s in dash["spans"]], ["\u2014"])

    def test_banned_scope_rules_and_follow_up_phrases(self):
        with self.subTest("dm and comment scopes are not email rules"):
            self.assert_passes(self.run_on(body=BODY.replace("Thanks,", "Let's connect. Thanks,")), "banned_phrase")
        with self.subTest("[[:space:]] in a pattern matches any space"):
            body = BODY.replace("I build the same", "Not just  queues, but I build the same")
            self.assertEqual(self.one(self.run_on(body=body), "banned_phrase")["severity"], "warn")
        with self.subTest("a follow-up never bumps"):
            body = BODY.replace("Thanks,", "Just bumping this. Thanks,")
            self.assertEqual(self.one(self.run_on(body=body), "banned_phrase")["severity"], "warn")
        with self.subTest("every dash from U+2012 to U+2015 warns, a hyphen does not"):
            for ch in "\u2012\u2013\u2014\u2015":
                self.assertEqual(self.one(self.run_on(body=BODY + ch), "dash")["severity"], "warn")
            self.assert_passes(self.run_on(body=BODY + " well-tested"), "dash")

    def test_length_bands_per_role_kind(self):
        cases = [
            ("engineer", 0, 79, "warn"), ("engineer", 0, 80, "pass"), ("engineer", 0, 120, "pass"),
            ("engineer", 0, 121, "warn"), ("manager", 0, 120, "pass"), ("founder", 0, 121, "warn"),
            ("alum", 0, 80, "pass"), ("other", 0, 119, "pass"),
            ("recruiter", 0, 90, "pass"), ("recruiter", 0, 100, "pass"), ("recruiter", 0, 101, "warn"),
            ("recruiter", 0, 79, "warn"),
            ("engineer", 1, 80, "pass"), ("engineer", 1, 81, "warn"), ("engineer", 1, 20, "pass"),
            ("recruiter", 1, 81, "warn"),
        ]
        for kind, seq, n, severity in cases:
            with self.subTest(kind=kind, seq=seq, words=n):
                found = self.run_on(role_kind=kind, seq=seq, body=words(n))
                check = self.one(found, "length")
                self.assertEqual(check["severity"], severity, check)
                self.assertIn(str(n), check["detail"])
                self.assertNotIn("length", self.codes(found, "block"))

    def test_stale_hook_needs_a_date_in_the_hook_and_only_warns(self):
        cases = [
            ("His talk at the meetup (2026-10-01)", "warn"),   # 40 days
            ("His talk at the meetup (2026-10-11)", "pass"),   # exactly 30 days
            ("His talk at the meetup (2026-11-09)", "pass"),
            ("His talk at the meetup (2026-10-01) ", "warn"),  # trailing space
            ("His talk at the meetup (2026-02-30)", "warn"),   # not a real date
        ]
        for hook, severity in cases:
            with self.subTest(hook=hook):
                found = self.run_on(hook=hook)
                self.assertEqual(self.one(found, "stale_hook")["severity"], severity)
                self.assertEqual(self.codes(found, "block"), [])
        for hook in ("His talk at the meetup", "(2026-01-01) his talk", ""):
            with self.subTest(hook=hook):
                self.assertNotIn("stale_hook", [c["code"] for c in self.run_on(hook=hook)])

    def test_weak_subject_and_re_prefix_warn(self):
        for subject in ("Quick question about Acme", "Following up on the role", "Excited about Acme",
                        "Re: your queue post", "RE : your queue post"):
            with self.subTest(subject=subject):
                found = self.run_on(subject=subject)
                self.assertEqual(self.one(found, "subject_wording")["severity"], "warn")
                self.assertEqual(self.codes(found, "block"), [])
        self.assert_passes(self.run_on(), "subject_wording")

    def test_subject_reused_warns(self):
        self.colleague(seed.company(self.conn, "Other"), "Lee Ray", subject=SUBJECT.upper() + "  ", body=words(95))
        found = self.run_on()
        self.assertEqual(self.one(found, "subject_reused")["severity"], "warn")
        self.assertEqual(self.codes(found, "block"), [])

    def test_subject_reused_ignores_his_own_thread_and_skipped_drafts(self):
        kim = seed.person(self.conn, seed.company(self.conn, "Other"), "Kim Day")
        touch = seed.touch(self.conn, kim)
        send_first(self.conn, touch, body=words(95), subject="First")
        mid = seed.message(self.conn, touch, 1, subject="Re: First", body=words(40))
        self.assert_passes(self.run_on(self.draft(mid)), "subject_reused")
        seed.message(self.conn, seed.touch(self.conn, seed.person(self.conn, self.acme, "Ana Cruz")), 0,
                     subject="Re: First", body=words(95))
        self.assertEqual(self.one(self.run_on(self.draft(mid)), "subject_reused")["severity"], "warn")
        _, mid = self.colleague(seed.company(self.conn, "Other"), "Lee Ray", subject=SUBJECT, body=words(95))
        self.conn.execute("UPDATE message SET state = 'skipped' WHERE message_id = ?", (mid,))
        self.assert_passes(self.run_on(), "subject_reused")

    def test_link_count_on_a_first_email_warns(self):
        cases = [
            (words(95, links=(PORTFOLIO,)), "warn"),
            (words(95, links=(PROJECT, PORTFOLIO)), "warn"),
            (words(95, links=(PORTFOLIO + "/projects", PROJECT)), "warn"),
            (words(95, links=("devanshuchicholikar.com", PROJECT)), "pass"),
            (words(95, links=(PORTFOLIO + "/", PROJECT)), "pass"),
            (words(95, links=()), "warn"),
        ]
        for body, severity in cases:
            with self.subTest(body=body[-80:]):
                found = self.run_on(body=body)
                self.assertEqual(self.one(found, "link_count")["severity"], severity)
                self.assertEqual(self.codes(found, "block"), [])
        with self.subTest("a follow-up has no link count rule"):
            self.assertNotIn("link_count", [c["code"] for c in self.run_on(seq=1, body=words(40, links=()))])

    def test_recruiter_warns_with_the_role_kind_fix(self):
        found = self.run_on(role_kind="recruiter", body=words(90))
        check = self.one(found, "recruiter")
        self.assertEqual(check["severity"], "warn")
        self.assertEqual(check["fix"], {"kind": "role_kind"})
        self.assertEqual(self.codes(found, "block"), [])
        self.assert_passes(self.run_on(), "recruiter")

    def test_mail_server_unknown_warns(self):
        found = self.run_on(mx="unknown")
        self.assertEqual(self.one(found, "mail_server_unknown")["severity"], "warn")
        self.assertEqual(self.codes(found, "block"), [])
        self.assertNotIn("no_mail_server", [c["code"] for c in found])


class Shape(ChecksCase):
    def test_clean_draft_has_only_pass_checks_in_a_stable_order(self):
        found = self.run_on()
        self.assertEqual([c["code"] for c in found], CLEAN_CODES)
        self.assertEqual({c["severity"] for c in found}, {"pass"})
        self.assertEqual(self.run_on(), found)

    def test_order_is_blocks_then_warns_then_passes(self):
        body = BODY.replace(", and your post", " \u2014 your post") + "\nacme.com"
        found = self.run_on(mx="none", body=body, subject="Quick question", role_kind="recruiter")
        rank = {"block": 0, "warn": 1, "pass": 2}
        self.assertEqual([rank[c["severity"]] for c in found], sorted(rank[c["severity"]] for c in found))
        self.assertEqual(self.codes(found, "block"), ["no_mail_server", "too_many_links"])
        self.assertEqual(self.codes(found, "warn")[:3], ["dash", "length", "subject_wording"])

    def test_no_attachment_column_exists(self):
        names = [row["name"] for row in self.conn.execute("PRAGMA table_info(message)")]
        self.assertIn("body", names)  # the pragma read the right table
        for name in names:
            self.assertFalse(any(word in name for word in ("attach", "file", "mime", "blob", "upload")), name)

    def test_checks_never_change_the_store(self):
        self.colleague(self.acme)
        self.no_go(person_id=self.pat)
        before = self.conn.total_changes
        row = dict(self.conn.execute("SELECT * FROM message WHERE message_id = ?", (self.mid,)).fetchone())
        self.run_on(mx="none")
        self.assertEqual(self.conn.total_changes, before)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(dict(self.conn.execute("SELECT * FROM message WHERE message_id = ?", (self.mid,))
                              .fetchone()), row)

    def test_every_check_is_the_api_check_shape(self):
        from outreach.api.models.checks import Check
        self.colleague(self.acme)
        self.no_go(person_id=self.pat)
        body = BODY.replace(", and your post", " \u2014 your post")
        found = self.run_on(mx="none", body=body, subject="Re: hi", role_kind="recruiter", hook="x (2026-01-01)")
        self.assertGreater(len(found), 10)
        for check in found:
            with self.subTest(code=check["code"]):
                self.assertEqual(set(check), {"code", "severity", "title", "detail", "fix", "spans"})
                self.assertEqual(Check.model_validate(check).model_dump(by_alias=True), check)
                json.dumps(check)  # stored as message.checks

    def test_checks_run_inside_a_callers_transaction_without_ending_it(self):
        self.conn.execute("BEGIN IMMEDIATE")
        self.run_on()
        self.assertTrue(self.conn.in_transaction)
        self.conn.execute("ROLLBACK")


class Inputs(ChecksCase):
    def put_mx(self, domain: str, status: str, at: str) -> None:
        self.conn.execute("INSERT INTO mx_check(domain, status, checked_at) VALUES (?,?,?) ON CONFLICT(domain) "
                          "DO UPDATE SET status = excluded.status, checked_at = excluded.checked_at",
                          (domain, status, at))

    def test_draft_input_reads_the_stored_message_and_the_cached_mail_server(self):
        d = draft_input(self.conn, self.mid, now=NOW)
        self.assertEqual(d, DraftInput(
            message_id=self.mid, seq=0, subject=SUBJECT, body=BODY, hook=HOOK, role_kind="engineer",
            person_name="Pat Lee", person_email="pat@acme.example", person_id=self.pat, company_id=self.acme,
            mx="unknown"))
        self.put_mx("acme.example", "ok", "2026-11-10T14:14:00Z")
        self.assertEqual(draft_input(self.conn, self.mid, now=NOW).mx, "ok")
        self.put_mx("acme.example", "none", "2026-11-09T15:14:01Z")
        self.assertEqual(draft_input(self.conn, self.mid, now=NOW).mx, "none")

    def test_cached_mx_is_unknown_when_missing_or_older_than_24_hours(self):
        self.assertEqual(cached_mx(self.conn, "acme.example", NOW), "unknown")
        self.put_mx("acme.example", "none", "2026-11-09T15:14:00Z")  # exactly 24 hours: stale
        self.assertEqual(cached_mx(self.conn, "acme.example", NOW), "unknown")
        self.put_mx("acme.example", "none", "2026-11-09T15:14:01Z")
        self.assertEqual(cached_mx(self.conn, "ACME.example.", NOW), "none")
        self.assertEqual(cached_mx(self.conn, "", NOW), "unknown")

    def test_draft_input_for_a_person_with_no_address_has_no_mail_server(self):
        self.conn.execute("UPDATE person SET email = NULL, added_by = 'import' WHERE person_id = ?", (self.pat,))
        self.assertEqual(draft_input(self.conn, self.mid, now=NOW).mx, "none")

    def test_draft_input_for_an_unknown_message_raises(self):
        with self.assertRaises(LookupError):
            draft_input(self.conn, 999_999, now=NOW)

    def test_load_banned_reads_the_email_and_both_scopes(self):
        banned, ok = load_banned(FIXTURE_BANNED)
        self.assertTrue(ok)
        self.assertEqual([text for text, _ in banned], [
            "leveraged", "passionate about", "I.d love to", "MS from Northeastern", "hope this finds you well",
            "not[[:space:]]+just[[:space:]]+[a-z]+,[[:space:]]*but"])
        self.assertTrue(banned[-1][1].search("NOT just queues, but"))

    def test_load_banned_scopes(self):
        """Ported from the brief's tests/test_check_draft.py::test_load_banned_scopes: the ledger ruling puts the
        `scopes` parameter on this module, not on the retired mail/check_draft.py."""
        default, ok = load_banned(FIXTURE_BANNED)
        self.assertTrue(ok)
        self.assertEqual(default, load_banned(FIXTURE_BANNED, ("both", "email"))[0])
        self.assertNotIn("let.s connect", [text for text, _ in default])
        dm, ok = load_banned(FIXTURE_BANNED, scopes=("both", "dm"))
        self.assertTrue(ok)
        self.assertEqual([text for text, _ in dm], [
            "leveraged", "passionate about", "I.d love to", "not[[:space:]]+just[[:space:]]+[a-z]+,[[:space:]]*but",
            "let.s connect"])
        comment, ok = load_banned(FIXTURE_BANNED, scopes=("comment",))
        self.assertEqual(([text for text, _ in comment], ok), (["^great post"], True))

    def test_load_banned_with_no_pattern_in_the_scopes_is_unusable(self):
        self.assertEqual(load_banned(FIXTURE_BANNED, scopes=("nonsense",)), ([], False))

    def test_text_helpers_are_public_for_the_note_checker(self):
        self.assertEqual(checks.find_links("see www.example.com/x and https://a.example/y."),
                         [(4, 21, "www.example.com/x"), (26, 45, "https://a.example/y")])
        self.assertEqual(checks.link_host("https://www.Bit.ly/abc"), "bit.ly")

    def test_default_banned_path_is_the_api_default(self):
        from outreach.api.settings import DEFAULT_BANNED_PHRASES
        self.assertEqual(checks.DEFAULT_BANNED_PHRASES, DEFAULT_BANNED_PHRASES)


if __name__ == "__main__":
    unittest.main()
