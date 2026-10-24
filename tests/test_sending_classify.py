import unittest

from outreach.sending.classify import Inbound, classify, dsn_class, match_unthreaded
from outreach.sending.mail import MessageMeta

ACCOUNT = "devanshurajesh@gmail.com"


def meta(*, from_addr="Priya Rao <priya@acme.co>", subject="Re: Hello", snippet="", **headers) -> MessageMeta:
    return MessageMeta(
        gmail_message_id="g1", thread_id="t1", from_addr=from_addr, to_addr=ACCOUNT, subject=subject,
        internal_ms=1_700_000_000_000, labels=("INBOX",),
        headers={k.replace("_", "-"): v for k, v in headers.items()}, snippet=snippet)


def dsn(snippet="", subject="Delivery Status Notification (Failure)", **headers) -> MessageMeta:
    return meta(from_addr="Mail Delivery Subsystem <mailer-daemon@googlemail.com>", subject=subject,
                snippet=snippet, **headers)


class ClassifyTest(unittest.TestCase):
    def test_out_of_office_header_is_not_a_reply(self):
        for name, value in (("auto_submitted", "auto-replied"), ("x_autoreply", "yes"), ("x_autorespond", "yes"),
                            ("x_auto_response_suppress", "All"), ("precedence", "bulk"), ("precedence", "auto_reply")):
            with self.subTest(header=name, value=value):
                got = classify(meta(**{name: value}), ACCOUNT)
                self.assertEqual(got.kind, "auto_reply")
        self.assertEqual(classify(meta(auto_submitted="no"), ACCOUNT).kind, "human")

    def test_automatic_reply_subject_is_not_a_reply(self):
        for subject in ("Automatic reply: Hello", "Out of Office: Hello", "Autoreply - Hello", "OOO until Monday",
                        "Away from the office"):
            with self.subTest(subject=subject):
                self.assertEqual(classify(meta(subject=subject), ACCOUNT).kind, "auto_reply")

    def test_human_reply_is_human(self):
        got = classify(meta(), ACCOUNT)
        self.assertEqual(got, Inbound(kind="human", dsn_class=None, failed_recipient=None))

    def test_reply_from_another_address_is_a_human_reply(self):
        # A colleague answers from a different address than the one we wrote to: still a person, still a reply.
        got = classify(meta(from_addr="Sam Lee <sam.lee@acme.co>"), ACCOUNT)
        self.assertEqual(got.kind, "human")

    def test_his_own_message_in_the_thread_is_ignored(self):
        for sender in ("Devanshu C <devanshurajesh@gmail.com>", "DEVANSHURAJESH@GMAIL.COM",
                       "<devanshurajesh@gmail.com>"):
            with self.subTest(sender=sender):
                self.assertEqual(classify(meta(from_addr=sender), ACCOUNT).kind, "ours")

    def test_delay_notice_is_not_a_bounce(self):
        # The old BOUNCE_SUBJ regex matches "delivery status notification", so it would call this a bounce.
        for subject in ("Delivery Status Notification (Delay)", "Delayed Mail (still being retried)",
                        "Warning: message 1abc-0002 delayed 24 hours"):
            with self.subTest(subject=subject):
                got = classify(dsn(subject=subject, x_failed_recipients="priya@acme.co"), ACCOUNT)
                self.assertEqual(got.kind, "delay")
                self.assertIsNone(got.dsn_class)

    def test_gmail_bounce_is_hard(self):
        got = classify(dsn("Address not found. Your message wasn't delivered to priya@acme.co because the address "
                           "couldn't be found. 550 5.1.1", x_failed_recipients="Priya@Acme.co"), ACCOUNT)
        self.assertEqual(got, Inbound(kind="bounce", dsn_class="hard", failed_recipient="priya@acme.co"))

    def test_bounce_by_subject_from_an_odd_sender_needs_a_machine_header(self):
        odd = dict(from_addr="noreply@acme.co", subject="Undeliverable: Hello")
        self.assertEqual(classify(meta(**odd, auto_submitted="auto-replied"), ACCOUNT).kind, "bounce")
        self.assertEqual(classify(meta(**odd, x_failed_recipients="priya@acme.co"), ACCOUNT).kind, "bounce")
        self.assertEqual(classify(meta(**odd), ACCOUNT).kind, "human")

    def test_a_human_reply_about_a_delivery_failure_is_not_a_bounce(self):
        for subject in ("Re: Delivery failure question", "Delivery failure question", "Fwd: Undelivered mail"):
            with self.subTest(subject=subject):
                self.assertEqual(classify(meta(subject=subject), ACCOUNT).kind, "human")

    def test_a_bounce_whose_echoed_subject_says_delayed_is_still_a_bounce(self):
        got = classify(dsn("550 5.1.1 does not exist", subject="Undeliverable: Your delayed rollout",
                           x_failed_recipients="priya@acme.co"), ACCOUNT)
        self.assertEqual((got.kind, got.dsn_class), ("bounce", "hard"))
        self.assertEqual(classify(dsn(subject="Undeliverable: Your delayed rollout"), ACCOUNT).kind, "bounce")

    def test_a_delay_subject_with_a_hard_or_policy_reading_is_a_bounce(self):
        for snippet in ("550 5.1.1 does not exist", "550 5.7.511 banned sender"):
            with self.subTest(snippet=snippet):
                got = classify(dsn(snippet, subject="Delivery Status Notification (Delay)"), ACCOUNT)
                self.assertEqual(got.kind, "bounce")

    def test_dotted_and_plus_variants_of_his_own_gmail_address_are_ours(self):
        for sender in ("devanshu.rajesh@gmail.com", "Devanshu <devanshurajesh+jobs@gmail.com>",
                       "d.e.v.anshurajesh@googlemail.com"):
            with self.subTest(sender=sender):
                self.assertEqual(classify(meta(from_addr=sender), ACCOUNT).kind, "ours")
        self.assertEqual(classify(meta(from_addr="devanshurajesh@acme.co"), ACCOUNT).kind, "human")
        self.assertEqual(classify(meta(from_addr="devanshurajesh2@gmail.com"), ACCOUNT).kind, "human")

    def test_bounce_with_no_snippet_is_unknown_and_still_a_bounce(self):
        got = classify(dsn(""), ACCOUNT)
        self.assertEqual((got.kind, got.dsn_class), ("bounce", "unknown"))

    def test_his_own_address_wins_over_a_bounce_subject(self):
        self.assertEqual(classify(meta(from_addr=ACCOUNT, subject="Undeliverable: x"), ACCOUNT).kind, "ours")

    def test_a_bounce_is_never_called_auto_reply(self):
        got = classify(dsn("550 5.1.1 does not exist", auto_submitted="auto-replied", precedence="bulk"), ACCOUNT)
        self.assertEqual(got.kind, "bounce")


class DsnClassTest(unittest.TestCase):
    def test_policy_snippet_is_policy(self):
        cases = {
            "5.7.1 5.7.1 Our system has detected that this message is likely unsolicited mail": "policy",
            "550 5.7.1 rejected as spam": "policy",
            "550 5.7.1 blocked by policy": "policy",
            "550 5.7.511 Access denied, banned sender": "policy",
        }
        for snippet, expected in cases.items():
            with self.subTest(snippet=snippet):
                self.assertEqual(dsn_class(dsn(snippet)), expected)

    def test_a_temporary_4_7_code_is_a_delay_notice_never_a_bounce(self):
        # 4.x.x is "try again later": Gmail keeps trying, so nothing bounced (yet).
        for snippet in ("421 4.7.28 Gmail has detected an unusual rate of unsolicited mail",
                        "450 4.7.500 Server busy, try again later", "450 4.7.599 Try later",
                        "451 4.7.1 Greylisted, please try again", "421 4.7.0 Temporary System Problem"):
            for subject in ("Delivery Status Notification (Failure)", "Delivery Status Notification (Delay)",
                            "Undeliverable: Hello"):
                with self.subTest(snippet=snippet, subject=subject):
                    got = classify(dsn(snippet, subject=subject, x_failed_recipients="p@acme.co"), ACCOUNT)
                    self.assertEqual((got.kind, got.dsn_class), ("delay", None))
                    self.assertNotEqual(dsn_class(dsn(snippet)), "policy")

    def test_a_permanent_code_beside_a_temporary_one_is_still_a_bounce(self):
        got = classify(dsn("421 4.7.0 try later. Final: 550 5.1.1 The account does not exist"), ACCOUNT)
        self.assertEqual((got.kind, got.dsn_class), ("bounce", "hard"))

    def test_policy_code_without_its_words_is_unknown(self):
        # 5.7.1 alone also covers plain relay refusals, so it needs one of the policy words.
        self.assertEqual(dsn_class(dsn("550 5.7.1 relay access denied")), "unknown")
        self.assertEqual(dsn_class(dsn("550 5.7.10 spam")), "unknown")

    def test_hard_snippet_is_hard(self):
        cases = ("550 5.1.1 User unknown", "550 5.1.10 Recipient not found", "550 5.4.1 Recipient address rejected",
                 "Address not found", "The mailbox does not exist", "550 5.1.1.")
        for snippet in cases:
            with self.subTest(snippet=snippet):
                self.assertEqual(dsn_class(dsn(snippet)), "hard")

    def test_limit_snippet_is_limit(self):
        got = dsn_class(dsn("Your message couldn't be delivered. You have reached a limit for sending mail."))
        self.assertEqual(got, "limit")

    def test_code_inside_another_number_is_not_a_code(self):
        self.assertEqual(dsn_class(dsn("see 15.1.12 for details")), "unknown")


class MatchUnthreadedTest(unittest.TestCase):
    TRACKED = {"priya@acme.co": 7, "sam@beta.io": 8}
    SUBJECTS = {"Quick question about Acme": 7, "Hello from Devanshu": 8}

    def test_unthreaded_dsn_matches_by_failed_recipient(self):
        got = match_unthreaded(dsn(x_failed_recipients="Priya@Acme.co"), self.TRACKED, self.SUBJECTS)
        self.assertEqual(got, 7)

    def test_failed_recipient_list_matches_any_tracked_address(self):
        got = match_unthreaded(dsn(x_failed_recipients="nobody@x.co, sam@beta.io"), self.TRACKED, self.SUBJECTS)
        self.assertEqual(got, 8)

    def test_recipient_wins_over_subject(self):
        got = match_unthreaded(dsn(subject="Undeliverable: Hello from Devanshu", x_failed_recipients="priya@acme.co"),
                               self.TRACKED, self.SUBJECTS)
        self.assertEqual(got, 7)

    def test_unthreaded_dsn_matches_by_exact_subject_only(self):
        exact = dsn(subject="Undeliverable: Quick question about Acme")
        self.assertEqual(match_unthreaded(exact, self.TRACKED, self.SUBJECTS), 7)
        for near in ("Undeliverable: Quick question about Acm", "Undeliverable: quick question about acme",
                     "Undeliverable: Quick  question about Acme", "Undeliverable"):
            with self.subTest(subject=near):
                self.assertIsNone(match_unthreaded(dsn(subject=near), self.TRACKED, self.SUBJECTS))

    def test_the_longer_of_two_matching_subjects_wins(self):
        subjects = {"Quick question about it": 1, "Quick question about it at Acme": 2}
        got = match_unthreaded(dsn(subject="Undeliverable: Quick question about it at Acme"),
                               self.TRACKED, subjects)
        self.assertEqual(got, 2)

    def test_newsletter_is_other_only_when_untracked(self):
        news = meta(from_addr="Weekly <news@list.example>", subject="This week in Python")
        self.assertEqual(classify(news, ACCOUNT).kind, "human")
        self.assertIsNone(match_unthreaded(news, self.TRACKED, self.SUBJECTS))

    def test_a_short_sent_subject_matches_only_the_whole_subject(self):
        subjects = {"Hi there": 5}
        self.assertIsNone(match_unthreaded(dsn(subject="Undeliverable: Hi there, Priya"), {}, subjects))
        self.assertEqual(match_unthreaded(dsn(subject="Hi there"), {}, subjects), 5)

    def test_an_empty_sent_subject_never_matches(self):
        self.assertIsNone(match_unthreaded(dsn(subject="anything"), {}, {"": 5}))


if __name__ == "__main__":
    unittest.main()
