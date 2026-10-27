"""The mail client contract (`outreach.sending.mail`) and the scriptable fake Gmail every sending test uses.

Nothing here reaches Gmail: FakeGmail and FakeClock are in-memory only.
"""
import unittest
from datetime import datetime, timedelta, timezone

from outreach.sending.fakegmail import FakeClock, FakeGmail
from outreach.sending.mail import (RETRYABLE, GmailError, HistoryExpired, MailClient, MessageMeta, Outgoing, Sent,
                                   classify_http, normalise_message_id)

ACCOUNT = "devanshurajesh@gmail.com"
START = datetime(2026, 11, 10, 14, 0, tzinfo=timezone.utc)
# Malformed Message-IDs: no brackets, two ids, two @, a space, an empty local part.
BAD_IDS = ("a1@gmail.com", "<a1@gmail.com> <a2@gmail.com>", "<a1@b@gmail.com>", "<a 1@gmail.com>", "<@gmail.com>",
           # Outside RFC 5322 dot-atom ASCII: a non-ASCII letter, a quote, parentheses, a NUL, a bare dot.
           "<\u00e9t\u00e9@gmail.com>", '<"a1"@gmail.com>', "<a(1)@gmail.com>", "<a1\x00@gmail.com>", "<a1.@gmail.com>",
           "<a1@gmail..com>", "<a1@gmail.com>\n")
GOOD_IDS = ("<177.1.2@gmail.com>", "<a!#$%&'*+/=?^_`{|}~-z@x-y.example>", "<CAgmail123@mail.gmail.com>")


def outgoing(to="founder@sendwell.example", subject="Backend role at Sendwell", *, message_id="<a1@gmail.com>",
             thread_id=None, in_reply_to=None, references=(), marker=None):
    """An email as `claim.outgoing_for` builds it: the marker is the claimed token, the Message-ID's value."""
    return Outgoing(to=to, subject=subject, body="Hi Sam,\n\nShort note.\n\nDevanshu", from_name="Devanshu C",
                    from_addr=ACCOUNT, message_id=message_id, thread_id=thread_id, in_reply_to=in_reply_to,
                    references=tuple(references), marker=message_id if marker is None else marker)


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


class FakeClockTest(unittest.TestCase):
    def test_clock_advances_and_sets(self):
        clock = FakeClock(START)
        self.assertEqual(clock(), START)
        clock.advance(minutes=5)
        self.assertEqual(clock(), START + timedelta(minutes=5))
        clock.set(START + timedelta(days=2))
        self.assertEqual(clock(), START + timedelta(days=2))

    def test_clock_refuses_a_naive_time(self):
        with self.assertRaises(ValueError):
            FakeClock(datetime(2026, 11, 10, 9, 0))
        with self.assertRaises(ValueError):
            FakeClock(START).set(datetime(2026, 11, 10, 9, 0))


class FakeGmailTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(START)
        self.gmail = FakeGmail(clock=self.clock)

    def test_fake_is_a_mail_client(self):
        self.assertIsInstance(self.gmail, MailClient)

    def test_send_assigns_ids_and_lands_in_sent(self):
        sent = self.gmail.send(outgoing())
        # Gmail stores it under a Message-ID of its own, as the real one does (`rewrite_message_ids`, on by default).
        self.assertEqual(sent, Sent(gmail_message_id="m1", thread_id="t1", rfc_message_id="<rewritten1@mail.gmail.com>",
                                    marker="<a1@gmail.com>"))
        self.assertEqual(len(self.gmail.sent), 1)
        out, got, at = self.gmail.sent[0]
        self.assertEqual((out.to, got, at), ("founder@sendwell.example", sent, START))
        self.assertEqual([m.headers["message-id"] for m in self.gmail.sent_box], ["<rewritten1@mail.gmail.com>"])
        self.assertEqual([m.headers["x-outreach-id"] for m in self.gmail.sent_box], ["<a1@gmail.com>"])
        meta = self.gmail.message("m1")
        self.assertEqual(meta.labels, ("SENT",))
        self.assertEqual(meta.to_addr, "founder@sendwell.example")
        self.assertIn(ACCOUNT, meta.from_addr)
        self.assertEqual(meta.internal_ms, ms(START))
        second = self.gmail.send(outgoing(to="cto@other.example", subject="Hello", message_id="<a2@gmail.com>"))
        self.assertEqual((second.gmail_message_id, second.thread_id), ("m2", "t2"))

    def test_follow_up_with_thread_id_stays_in_the_thread(self):
        first = self.gmail.send(outgoing())
        self.clock.advance(days=9)
        follow = self.gmail.send(outgoing(subject="Re: Backend role at Sendwell", message_id="<a2@gmail.com>",
                                          thread_id=first.thread_id, in_reply_to=first.rfc_message_id,
                                          references=(first.rfc_message_id,)))
        self.assertEqual(follow.thread_id, first.thread_id)
        thread = self.gmail.thread(first.thread_id)
        self.assertEqual([m.gmail_message_id for m in thread], [first.gmail_message_id, follow.gmail_message_id])
        self.assertEqual(thread[1].headers["in-reply-to"], first.rfc_message_id)
        self.assertEqual(thread[1].headers["references"], first.rfc_message_id)

    def test_follow_up_with_another_subject_starts_a_new_thread_as_gmail_does(self):
        first = self.gmail.send(outgoing())
        other = self.gmail.send(outgoing(subject="Something else", message_id="<a2@gmail.com>",
                                         thread_id=first.thread_id, in_reply_to=first.rfc_message_id))
        self.assertNotEqual(other.thread_id, first.thread_id)

    def test_unknown_thread_id_is_a_bad_request_and_sends_nothing(self):
        with self.assertRaises(GmailError) as cm:
            self.gmail.send(outgoing(thread_id="t99"))
        self.assertEqual((cm.exception.kind, cm.exception.status), ("bad_request", 404))
        self.assertEqual(self.gmail.sent, [])

    def test_malformed_message_ids_are_refused_in_every_header(self):
        for bad in BAD_IDS:
            for m in (outgoing(message_id=bad), outgoing(in_reply_to=bad, references=("<a0@gmail.com>",)),
                      outgoing(in_reply_to="<a0@gmail.com>", references=("<a0@gmail.com>", bad)),
                      outgoing(marker=bad)):
                with self.subTest(bad=bad, m=m), self.assertRaises(GmailError) as cm:
                    self.gmail.send(m)
                self.assertEqual(cm.exception.kind, "bad_request")
        self.assertEqual(self.gmail.sent, [])

    def test_dot_atom_message_ids_are_accepted(self):
        for i, good in enumerate(GOOD_IDS):
            with self.subTest(good=good):
                self.gmail.send(outgoing(to=f"p{i}@co.example", message_id=good))
        self.assertEqual(len(self.gmail.sent), len(GOOD_IDS))

    def test_a_rewritten_message_id_is_a_normal_send_that_keeps_the_marker(self):
        # What Gmail did on the first live send: it stored the email under a Message-ID of its own.
        self.gmail.rewrite_message_ids = True
        sent = self.gmail.send(outgoing())
        self.assertEqual(len(self.gmail.sent), 1)
        self.assertEqual((sent.gmail_message_id, sent.thread_id, sent.marker), ("m1", "t1", "<a1@gmail.com>"))
        self.assertNotEqual(sent.rfc_message_id, "<a1@gmail.com>")
        stored = self.gmail.message("m1").headers
        self.assertEqual((stored["message-id"], stored["x-outreach-id"]), (sent.rfc_message_id, "<a1@gmail.com>"))

    def test_gmail_can_keep_the_id_it_replaced_and_a_scan_takes_it_as_a_hit(self):
        self.gmail.keep_original_id, self.gmail.drop_markers = True, True
        sent = self.gmail.send(outgoing())
        self.assertEqual(self.gmail.message("m1").headers["x-google-original-message-id"], "<a1@gmail.com>")
        found, complete = self.gmail.find_sent("<a1@gmail.com>", ms(START), send_returned_ms=ms(START))
        self.assertEqual((found.gmail_message_id, found.marker, complete), (sent.gmail_message_id, "<a1@gmail.com>",
                                                                           True))

    def test_a_dropped_marker_and_a_failed_read_back_can_be_scripted(self):
        self.gmail.drop_markers = True
        sent = self.gmail.send(outgoing())
        self.assertEqual(sent.marker, "")
        self.assertNotIn("x-outreach-id", self.gmail.message("m1").headers)
        self.gmail.drop_markers, self.gmail.rewrite_message_ids, self.gmail.readback_fails = False, True, True
        blind = self.gmail.send(outgoing(to="cto@other.example", subject="Hello", message_id="<a2@gmail.com>"))
        self.assertEqual((blind.rfc_message_id, blind.marker), ("<a2@gmail.com>", None))
        self.assertNotEqual(self.gmail.message("m2").headers["message-id"], "<a2@gmail.com>")

    def test_bad_outgoing_is_refused_before_anything_goes_out(self):
        for bad in (outgoing(to="a@x.example, b@y.example"), outgoing(message_id=""), outgoing(marker=""),
                    outgoing(subject="Hi\r\nBcc: x@y.example")):
            with self.subTest(bad=bad), self.assertRaises(GmailError) as cm:
                self.gmail.send(bad)
            self.assertEqual(cm.exception.kind, "bad_request")
        self.assertEqual((self.gmail.sent, self.gmail.sent_box), ([], []))

    def test_failed_send_sends_nothing(self):
        self.gmail.fail_next_send(GmailError("rate_limit", 429, "slow down"))
        with self.assertRaises(GmailError):
            self.gmail.send(outgoing())
        self.assertEqual((self.gmail.sent, self.gmail.sent_box), ([], []))
        self.clock.advance(minutes=10)
        self.assertEqual(self.gmail.find_sent("<a1@gmail.com>", ms(START), send_returned_ms=ms(START)), (None, True))
        # The script is used once: the next send goes through.
        self.assertEqual(self.gmail.send(outgoing()).gmail_message_id, "m1")

    def test_accepted_then_failed_counts_as_sent_and_is_in_sent(self):
        self.gmail.fail_next_send(TimeoutError("read timed out"), accepted=True)
        with self.assertRaises(TimeoutError):
            self.gmail.send(outgoing())
        self.assertEqual(len(self.gmail.sent), 1)
        found, complete = self.gmail.find_sent("<a1@gmail.com>", ms(START), send_returned_ms=ms(START))
        self.assertTrue(complete)
        self.assertEqual(found, self.gmail.sent[0][1])

    def test_accepted_but_not_yet_in_sent_can_be_scripted(self):
        self.gmail.fail_next_send(TimeoutError(), accepted=True, in_sent=False)
        with self.assertRaises(TimeoutError):
            self.gmail.send(outgoing())
        self.assertEqual((len(self.gmail.sent), self.gmail.sent_box), (1, []))

    def test_history_lists_only_new_messages_and_expires_on_request(self):
        first = self.gmail.send(outgoing())
        cursor = self.gmail.profile().history_id
        self.assertEqual(self.gmail.history(cursor).added, ())
        reply = self.gmail.deliver_reply(first, "Sam Lee <founder@sendwell.example>", snippet="Happy to chat")
        noise = self.gmail.deliver_unrelated("news@letters.example", "Weekly digest")
        page = self.gmail.history(cursor)
        self.assertEqual([a.gmail_message_id for a in page.added], [reply.gmail_message_id, noise.gmail_message_id])
        self.assertEqual(page.added[0].thread_id, first.thread_id)
        self.assertIn("INBOX", page.added[0].labels)
        self.assertEqual(page.history_id, self.gmail.profile().history_id)
        self.assertGreater(int(page.history_id), int(cursor))
        self.assertEqual(self.gmail.history(page.history_id).added, ())
        self.gmail.expire_history()
        with self.assertRaises(HistoryExpired):
            self.gmail.history(page.history_id)
        self.assertEqual(self.gmail.history(page.history_id).added, ())   # expiry is one call
        with self.assertRaises(HistoryExpired):
            self.gmail.history("")                                        # no cursor is no cursor

    def test_reply_shape(self):
        first = self.gmail.send(outgoing())
        self.clock.advance(hours=3)
        reply = self.gmail.deliver_reply(first, "founder@sendwell.example", headers={"Precedence": "bulk"},
                                         snippet="Sure")
        self.assertEqual(reply.thread_id, first.thread_id)
        self.assertEqual(reply.subject, "Re: Backend role at Sendwell")
        self.assertEqual(reply.to_addr, ACCOUNT)
        self.assertEqual(reply.headers["in-reply-to"], first.rfc_message_id)
        self.assertEqual(reply.headers["precedence"], "bulk")
        self.assertEqual(reply.internal_ms, ms(START + timedelta(hours=3)))
        self.assertEqual(self.gmail.message(reply.gmail_message_id), reply)
        with self.assertRaises(ValueError):
            self.gmail.deliver_reply(first, "x@y.example", headers={"X-Not-Read": "1"})

    def test_auto_reply_shape(self):
        first = self.gmail.send(outgoing())
        auto = self.gmail.deliver_auto_reply(first, "founder@sendwell.example")
        self.assertEqual((auto.subject, auto.thread_id), ("Automatic reply", first.thread_id))
        self.assertEqual(auto.headers["auto-submitted"], "auto-replied")

    def test_bounce_shapes(self):
        first = self.gmail.send(outgoing())
        hard = self.gmail.deliver_bounce(first)
        self.assertEqual(hard.from_addr, "Mail Delivery Subsystem <mailer-daemon@googlemail.com>")
        self.assertEqual(hard.subject, "Delivery Status Notification (Failure)")
        self.assertEqual(hard.thread_id, first.thread_id)
        self.assertEqual(hard.headers["x-failed-recipients"], "founder@sendwell.example")
        self.assertIn("550 5.1.1", hard.snippet)
        policy = self.gmail.deliver_bounce(first, dsn="policy")
        self.assertIn("550 5.7.1", policy.snippet)
        self.assertIn("unsolicited", policy.snippet)
        limit = self.gmail.deliver_bounce(first, dsn="limit")
        self.assertIn("You have reached a limit for sending mail", limit.snippet)
        delay = self.gmail.deliver_bounce(first, dsn="delay")
        self.assertIn("(Delay)", delay.subject)
        self.assertNotIn("x-failed-recipients", delay.headers)
        loose = self.gmail.deliver_bounce(first, threaded=False)
        self.assertNotEqual(loose.thread_id, first.thread_id)
        self.assertNotIn(loose.thread_id, {m.thread_id for m in self.gmail.sent_box})
        self.assertEqual(loose.headers["x-failed-recipients"], "founder@sendwell.example")
        self.assertNotIn("in-reply-to", loose.headers)

    def test_find_sent_by_marker_reports_a_complete_scan(self):
        claim = START
        target = self.gmail.send(outgoing())
        for i in range(120):                       # many newer messages: more than two fake pages
            self.clock.advance(minutes=1)
            self.gmail.send(outgoing(to=f"p{i}@co.example", subject=f"s{i}", message_id=f"<n{i}@gmail.com>"))
        done = ms(claim)
        self.assertEqual(self.gmail.find_sent("<a1@gmail.com>", ms(claim), send_returned_ms=done), (target, True))
        # A miss that reached past the claim time is proof.
        self.assertEqual(self.gmail.find_sent("<never@gmail.com>", ms(claim) + 1, send_returned_ms=done), (None, True))
        # A miss that reached the end of Sent is proof too.
        self.assertEqual(self.gmail.find_sent("<never@gmail.com>", 0, send_returned_ms=done), (None, True))

    def test_find_sent_matches_by_marker_only(self):
        # Same To and Subject, and even the very Message-ID looked for, but no marker: not a hit.
        self.gmail.add_to_sent("founder@sendwell.example", "Backend role at Sendwell", message_id="<a1@gmail.com>")
        self.gmail.add_to_sent("founder@sendwell.example", "Backend role at Sendwell", message_id="<b1@gmail.com>",
                               marker="<other@gmail.com>")
        self.clock.advance(minutes=10)
        self.assertEqual(self.gmail.find_sent("<a1@gmail.com>", 0, send_returned_ms=ms(START)), (None, True))

    def test_find_sent_finds_a_rewritten_send_by_its_marker(self):
        self.gmail.rewrite_message_ids = True
        sent = self.gmail.send(outgoing())
        found, complete = self.gmail.find_sent("<a1@gmail.com>", ms(START), send_returned_ms=ms(START))
        self.assertTrue(complete)
        self.assertEqual(found, sent)  # with the Message-ID Gmail stored, not the one set
        self.assertNotEqual(found.rfc_message_id, "<a1@gmail.com>")

    def test_find_sent_reads_each_message_and_fails_like_the_real_client(self):
        self.gmail.send(outgoing())
        self.gmail.fail_reads = GmailError("server", 500, "backend error")
        for call in (lambda: self.gmail.find_sent("<a1@gmail.com>", 0, send_returned_ms=ms(START)),
                     lambda: self.gmail.message("m1")):
            with self.assertRaises(GmailError) as cm:
                call()
            self.assertEqual(cm.exception.status, 500)
        self.gmail.fail_reads = None
        self.assertIsNotNone(self.gmail.find_sent("<a1@gmail.com>", 0, send_returned_ms=ms(START))[0])

    def test_find_sent_with_a_page_cap_reports_incomplete(self):
        self.gmail.send(outgoing())
        for i in range(60):
            self.clock.advance(minutes=1)
            self.gmail.send(outgoing(to=f"p{i}@co.example", subject=f"s{i}", message_id=f"<n{i}@gmail.com>"))
        self.assertEqual(self.gmail.find_sent_page_cap, 10)     # like the real client
        done = ms(START)
        self.gmail.find_sent_page_cap = 1           # one page of 50: the target is on page 2
        self.assertEqual(self.gmail.find_sent("<a1@gmail.com>", ms(START), send_returned_ms=done), (None, False))
        self.gmail.find_sent_page_cap = 0
        self.assertEqual(self.gmail.find_sent("<n59@gmail.com>", 0, send_returned_ms=done), (None, False))
        self.gmail.find_sent_page_cap = 10
        self.assertEqual(self.gmail.find_sent("<a1@gmail.com>", ms(START), send_returned_ms=done)[1], True)

    def test_a_miss_too_soon_after_the_failed_send_is_not_proof(self):
        self.gmail.fail_next_send(GmailError("server", 503, "backend"))
        with self.assertRaises(GmailError):
            self.gmail.send(outgoing())
        done = ms(START)
        self.clock.advance(minutes=9, seconds=59)
        self.assertEqual(self.gmail.find_sent("<a1@gmail.com>", ms(START), send_returned_ms=done), (None, False))
        self.clock.advance(seconds=1)                                    # ten minutes after the send returned
        self.assertEqual(self.gmail.find_sent("<a1@gmail.com>", ms(START), send_returned_ms=done), (None, True))

    def test_a_send_that_lands_in_sent_late_is_not_called_missing(self):
        self.gmail.fail_next_send(TimeoutError(), accepted=True, lands_after=timedelta(minutes=4))
        with self.assertRaises(TimeoutError):
            self.gmail.send(outgoing())
        done = ms(START)
        self.clock.advance(minutes=3)
        self.assertEqual(self.gmail.find_sent("<a1@gmail.com>", ms(START), send_returned_ms=done), (None, False))
        self.clock.advance(minutes=2)
        found, complete = self.gmail.find_sent("<a1@gmail.com>", ms(START), send_returned_ms=done)
        self.assertEqual((found, complete), (self.gmail.sent[0][1], True))

    def test_the_scan_allows_for_the_mac_clock_running_ahead(self):
        # Gmail took the email at START; the Mac, two minutes fast, claimed it at START + 2 min. Fifty later
        # messages, all older than the claim by the Mac's clock, fill the first page.
        target = self.gmail.send(outgoing())
        for i in range(50):
            self.clock.advance(seconds=1)
            self.gmail.send(outgoing(to=f"p{i}@co.example", subject=f"s{i}", message_id=f"<n{i}@gmail.com>"))
        claim = START + timedelta(minutes=2)
        self.clock.set(START + timedelta(minutes=30))
        self.assertEqual(self.gmail.find_sent("<a1@gmail.com>", ms(claim), send_returned_ms=ms(claim)),
                         (target, True))

    def test_probe_sees_every_call(self):
        seen = []
        gmail = FakeGmail(clock=self.clock, probe=seen.append)
        first = gmail.send(outgoing())
        gmail.profile()
        gmail.history("1")
        gmail.message(first.gmail_message_id)
        gmail.thread(first.thread_id)
        gmail.find_sent(first.rfc_message_id, 0, send_returned_ms=0)
        names = ["send", "profile", "history", "message", "thread", "find_sent"]
        self.assertEqual(seen, names)
        self.assertEqual(gmail.calls, names)
        gmail.deliver_reply(first, "x@y.example")       # scripting is not a client call
        self.assertEqual(seen, names)

    def test_a_probe_that_raises_stops_the_call_before_it_does_anything(self):
        def crash(name):
            raise SystemExit("power cut")
        gmail = FakeGmail(clock=self.clock, probe=crash)
        with self.assertRaises(SystemExit):
            gmail.send(outgoing())
        self.assertEqual((gmail.sent, gmail.sent_box), ([], []))

    def test_profile_names_the_account_and_can_fail(self):
        self.assertEqual(self.gmail.profile().email, ACCOUNT)
        other = FakeGmail(clock=self.clock, account="someone@else.com")
        self.assertEqual(other.profile().email, "someone@else.com")
        self.gmail.fail_profile(GmailError("auth", 401, "invalid credentials"))
        for _ in range(2):                             # a dead token stays dead
            with self.assertRaises(GmailError):
                self.gmail.profile()
        self.gmail.fail_profile(None)
        self.assertEqual(self.gmail.profile().email, ACCOUNT)

    def test_unknown_message_or_thread_is_a_404(self):
        for call in (lambda: self.gmail.message("m404"), lambda: self.gmail.thread("t404")):
            with self.assertRaises(GmailError) as cm:
                call()
            self.assertEqual(cm.exception.status, 404)

    def test_message_meta_headers_are_only_the_listed_names(self):
        first = self.gmail.send(outgoing())
        for meta in (self.gmail.message(first.gmail_message_id), self.gmail.deliver_bounce(first)):
            self.assertIsInstance(meta, MessageMeta)
            self.assertLessEqual(set(meta.headers), {
                "auto-submitted", "x-autoreply", "x-autorespond", "precedence", "x-auto-response-suppress",
                "message-id", "in-reply-to", "references", "x-failed-recipients", "content-type", "x-outreach-id"})


class ClassifyHttpTest(unittest.TestCase):
    def test_classify_http_table(self):
        rows = [
            (401, '{"error": {"message": "Invalid Credentials"}}', "auth"),
            (403, '{"error": {"errors": [{"reason": "dailyLimitExceeded"}]}}', "daily_limit"),
            (403, "You have reached a limit for sending mail", "daily_limit"),
            (403, '{"error": {"errors": [{"reason": "userRateLimitExceeded"}]}}', "rate_limit"),
            (403, '{"error": {"errors": [{"reason": "rateLimitExceeded"}]}}', "rate_limit"),
            (403, '{"error": {"errors": [{"reason": "insufficientPermissions"}]}}', "auth"),
            (403, '{"error": "invalid_grant"}', "auth"),
            (403, '{"error": {"message": "Delegation denied"}}', "account_blocked"),
            (403, "", "account_blocked"),
            (429, "Too many concurrent requests for user", "rate_limit"),
            (400, '{"error": {"message": "Invalid To header"}}', "bad_request"),
            (404, "Requested entity was not found.", "bad_request"),
            (500, "", "server"),
            (503, "Backend Error", "server"),
        ]
        for status, body, kind in rows:
            with self.subTest(status=status, body=body):
                self.assertEqual(classify_http(status, body), kind)

    def test_only_a_bad_request_is_the_message_own_fault(self):
        kinds = {"auth", "rate_limit", "daily_limit", "account_blocked", "bad_request", "server", "network",
                 "timeout"}
        self.assertEqual(kinds - RETRYABLE, {"bad_request"})
        self.assertLessEqual(RETRYABLE, kinds)


class GmailErrorTest(unittest.TestCase):
    def test_detail_is_short_and_never_holds_a_bearer_token(self):
        err = GmailError("auth", 401, "Authorization: Bearer ya29.secretvalue " + "x" * 400)
        self.assertLessEqual(len(err.detail), 300)
        self.assertNotIn("ya29", err.detail)
        self.assertNotIn("ya29", str(err))
        self.assertEqual((err.kind, err.status), ("auth", 401))

    def test_detail_drops_token_fields(self):
        err = GmailError("server", 500, '{"access_token": "abc123", "refresh_token": "1//zzz"}')
        self.assertNotIn("abc123", err.detail)
        self.assertNotIn("1//zzz", err.detail)

    def test_message_ids_compare_without_folding_whitespace(self):
        self.assertEqual(normalise_message_id(" <a1@gmail.com>\r\n "), "<a1@gmail.com>")
        self.assertEqual(normalise_message_id("<a1@\r\n gmail.com>"), "<a1@gmail.com>")

    def test_unknown_kind_is_refused(self):
        # message_id_rewritten is gone: Gmail storing its own Message-ID is how a send normally ends.
        for kind in ("whatever", "message_id_rewritten"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                GmailError(kind, None, "")


if __name__ == "__main__":
    unittest.main()
