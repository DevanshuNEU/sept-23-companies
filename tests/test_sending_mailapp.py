"""The Mail.app client (Phase B): the Northeastern account, sent and read through AppleScript.

No test starts osascript or touches Mail.app. `FakeMailApp` is the real `MailAppClient` with its runner replaced by
an in-memory Mail.app that answers each of the client's scripts the way the script does in the real one (records,
escaping, local dates), so these tests run the client's own code: what it asks Mail.app, how it reads the answer,
and when a send in doubt counts as sent, missing or neither.
"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from outreach.sending import applescript, inbound, mailapp
from outreach.sending.config import load_config
from outreach.sending.fakegmail import FakeClock
from outreach.sending.fakemailapp import FakeMailApp
from outreach.sending.mail import (META_HEADERS, GmailError, HistoryExpired, MailClient, Outgoing, Profile, Sent,
                                   SentAmbiguous, ThreadUnsupported)

UTC = timezone.utc
CFG = load_config()
NEU = next(a for a in CFG.accounts if a.kind == "mailapp")
START = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)  # a Monday, 10:00 in New York
SETTLE = CFG.reconcile_after_minutes


def ms(at: datetime) -> int:
    return int(at.timestamp() * 1000)


def outgoing(n: int = 1, *, to: str = "ada@acme.example", subject: str = "Your team and the backend role",
             body: str = "Hi Ada,\n\nA short note.\n\nDevanshu", thread_id: str | None = None,
             in_reply_to: str | None = None, from_addr: str = NEU.address) -> Outgoing:
    token = f"<claim{n}@knock.local>"
    return Outgoing(to=to, subject=subject, body=body, from_name=CFG.from_name, from_addr=from_addr,
                    message_id=token, thread_id=thread_id, in_reply_to=in_reply_to,
                    references=(in_reply_to,) if in_reply_to else (), marker=token)


class Base(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(START)
        self.mail = FakeMailApp(clock=self.clock, account=NEU, reconcile_after_minutes=SETTLE)

    def find(self, m: Outgoing, claimed: datetime, returned: datetime | None = None):
        return self.mail.find_sent(m.marker, ms(claimed) - 5 * 60_000, send_returned_ms=ms(returned or claimed),
                                   to=m.to, subject=m.subject)


class SendTest(Base):
    def test_is_a_mail_client(self):
        self.assertIsInstance(self.mail, MailClient)

    def test_send_returns_message_id_from_sent_items(self):
        m = outgoing()
        sent = self.mail.send(m)
        stored = self.mail.sent_items()[-1]
        rfc = f"<{stored.msgid}>"
        self.assertEqual(sent, Sent(gmail_message_id="mailapp:" + rfc, thread_id=rfc, rfc_message_id=rfc,
                                    marker=m.marker))
        self.assertEqual((stored.sender, stored.to, stored.subject, stored.content),
                         (f"Devanshu Chicholikar <{NEU.address}>", m.to, m.subject, m.body))
        self.assertEqual(len(self.mail.sent), 1)

    def test_follow_up_replies_to_the_first_email_in_sent_items(self):
        first = self.mail.send(outgoing())
        self.clock.advance(days=7)
        m = outgoing(2, subject="Re: Your team and the backend role", body="Following up.",
                     thread_id=first.thread_id, in_reply_to=first.rfc_message_id)
        sent = self.mail.send(m)
        reply = self.mail.sent_items()[-1]
        self.assertEqual(reply.replied_to, first.rfc_message_id[1:-1])
        self.assertEqual(reply.headers["In-Reply-To"], first.rfc_message_id)
        self.assertEqual((reply.subject, reply.content, reply.to), (m.subject, m.body, m.to))
        self.assertEqual(sent.thread_id, first.thread_id)
        self.assertEqual(sent.rfc_message_id, f"<{reply.msgid}>")
        self.assertNotEqual(sent.rfc_message_id, first.rfc_message_id)

    def test_follow_up_whose_first_email_is_not_in_sent_items_is_refused_and_nothing_goes(self):
        m = outgoing(2, subject="Re: Hello", thread_id="<gone@northeastern.edu>",
                     in_reply_to="<gone@northeastern.edu>")
        with self.assertRaises(GmailError) as caught:
            self.mail.send(m)
        self.assertEqual(caught.exception.kind, "bad_request")
        self.assertEqual(self.mail.sent, [])

    def test_mail_for_another_account_is_refused_before_mail_app(self):
        with self.assertRaises(GmailError) as caught:
            self.mail.send(outgoing(from_addr="devanshuchi9@gmail.com"))
        self.assertEqual(caught.exception.kind, "bad_request")
        self.assertEqual((self.mail.sent, self.mail.scripts), ([], []))

    def test_slow_sent_is_send_in_doubt_not_resend(self):
        m = outgoing()
        self.mail.slow_sent(timedelta(minutes=3))
        with self.assertRaises(GmailError) as caught:
            self.mail.send(m)
        self.assertEqual(caught.exception.kind, "timeout")
        self.assertEqual(len(self.mail.sent), 1)                   # it went out once
        self.assertGreaterEqual(self.clock() - START, timedelta(seconds=60))
        self.assertLess(self.clock() - START, timedelta(seconds=75))  # the poll ends at 60 seconds
        returned = self.clock()
        self.assertEqual(self.find(m, START, returned), (None, False))  # still in the Outbox: never proof
        self.clock.advance(minutes=SETTLE + 1)
        hit, complete = self.find(m, START, returned)
        self.assertTrue(complete)
        self.assertEqual(hit.marker, m.marker)
        self.assertEqual(hit.rfc_message_id, f"<{self.mail.sent_items()[-1].msgid}>")
        self.assertEqual(len(self.mail.sent), 1)

    def test_timeout_after_mail_app_took_it_is_found_in_sent_items(self):
        m = outgoing()
        self.mail.fail_next_send(GmailError("timeout", None, "osascript took longer than 60 seconds"), accepted=True)
        with self.assertRaises(GmailError):
            self.mail.send(m)
        hit, complete = self.find(m, START)
        self.assertTrue(complete)
        self.assertEqual(hit.gmail_message_id, "mailapp:" + hit.rfc_message_id)

    def test_two_in_sent_items_after_the_send_is_in_doubt(self):
        m = outgoing()
        self.mail.add_to_sent(m.to, m.subject, at=START + timedelta(seconds=1))
        with self.assertRaises(GmailError):
            self.mail.send(m)


class FindSentTest(Base):
    def test_in_outbox_is_incomplete(self):
        m = outgoing()
        self.mail.slow_sent(None)  # it never leaves the Outbox
        with self.assertRaises(GmailError):
            self.mail.send(m)
        self.clock.advance(hours=5)
        self.assertEqual(self.find(m, START, START + timedelta(minutes=1)), (None, False))
        self.assertEqual(len(self.mail.sent), 1)

    def test_two_hits_never_resend(self):
        m = outgoing()
        self.mail.add_to_sent(m.to, m.subject, at=START + timedelta(minutes=1))
        self.mail.add_to_sent(m.to, m.subject, at=START + timedelta(minutes=2))
        self.clock.advance(hours=1)
        with self.assertRaises(SentAmbiguous) as caught:
            self.find(m, START)
        self.assertIsInstance(caught.exception, GmailError)  # every caller that keeps it in doubt on an error does
        self.assertEqual(self.mail.sent, [])

    def test_a_miss_is_proof_only_after_the_settle_time(self):
        m = outgoing()
        self.assertEqual(self.find(m, START), (None, False))
        self.clock.advance(minutes=SETTLE)
        self.assertEqual(self.find(m, START), (None, True))

    def test_only_that_address_that_exact_subject_after_the_claim(self):
        m = outgoing()
        self.mail.add_to_sent(m.to, m.subject, at=START - timedelta(minutes=30))           # before the claim
        self.mail.add_to_sent("bob@acme.example", m.subject, at=START + timedelta(minutes=1))  # someone else
        self.mail.add_to_sent(m.to, m.subject.upper(), at=START + timedelta(minutes=1))    # another subject
        self.mail.add_to_sent(m.to, m.subject + " ", at=START + timedelta(minutes=1))
        self.clock.advance(minutes=SETTLE)
        self.assertEqual(self.find(m, START), (None, True))

    def test_the_address_matches_without_regard_to_case_or_display_name(self):
        m = outgoing(to="Ada Lovelace <Ada@Acme.example>")
        self.mail.add_to_sent("ada@acme.example", m.subject, at=START + timedelta(minutes=1))
        hit, complete = self.find(m, START)
        self.assertTrue(complete)
        self.assertEqual(hit.marker, m.marker)

    def test_a_follow_up_hit_carries_the_first_emails_thread(self):
        first = self.mail.send(outgoing())
        self.clock.advance(days=7)
        m = outgoing(2, subject="Re: Your team and the backend role", thread_id=first.thread_id,
                     in_reply_to=first.rfc_message_id)
        self.mail.fail_next_send(GmailError("timeout"), accepted=True)
        claimed = self.clock()
        with self.assertRaises(GmailError):
            self.mail.send(m)
        hit, _ = self.find(m, claimed)
        self.assertEqual(hit.thread_id, first.thread_id)

    def test_needs_the_address_and_subject(self):
        with self.assertRaises(ValueError):
            self.mail.find_sent("<claim1@knock.local>", ms(START), send_returned_ms=ms(START))


class HistoryTest(Base):
    def setUp(self):
        super().setUp()
        self.first = self.mail.send(outgoing())
        self.cursor = self.mail.profile().history_id
        self.clock.advance(hours=2)

    def test_history_skips_sent_and_drafts(self):
        reply = self.mail.deliver_reply(self.first, "ada@acme.example")
        self.mail.add_to_mailbox("Drafts", NEU.address, "ada@acme.example", "Re: a draft")
        self.mail.add_to_mailbox("Outbox", NEU.address, "ada@acme.example", "Re: waiting")
        self.mail.add_to_sent("bob@acme.example", "Another email")
        page = self.mail.history(self.cursor)
        self.assertEqual([a.gmail_message_id for a in page.added], ["mailapp:" + reply.rfc_id])
        self.assertEqual(page.added[0].thread_id, self.first.thread_id)
        self.assertEqual(page.added[0].labels, ("INBOX",))
        self.assertEqual(page.history_id, self.clock().strftime("%Y-%m-%dT%H:%M:%SZ"))

    def test_junk_labelled_spam(self):
        self.mail.deliver_reply(self.first, "ada@acme.example", mailbox="Junk Email")
        self.clock.advance(minutes=1)
        self.mail.deliver_ndr(self.first)
        page = self.mail.history(self.cursor)
        self.assertEqual([a.labels for a in page.added], [("SPAM",), ("INBOX",)])
        self.assertEqual({a.thread_id for a in page.added}, {self.first.thread_id})

    def test_his_own_folders_and_deleted_items_are_read(self):
        self.mail.deliver_reply(self.first, "ada@acme.example", mailbox="Outreach")
        self.mail.deliver_reply(self.first, "ada@acme.example", mailbox="Deleted Items")
        self.assertEqual(len(self.mail.history(self.cursor).added), 2)

    def test_reads_from_ten_minutes_before_the_cursor(self):
        cursor = self.clock().strftime("%Y-%m-%dT%H:%M:%SZ")
        self.mail.deliver_unrelated("old@x.example", "Old", at=self.clock() - timedelta(minutes=11))
        near = self.mail.deliver_unrelated("near@x.example", "Near", at=self.clock() - timedelta(minutes=9))
        self.assertEqual([a.gmail_message_id for a in self.mail.history(cursor).added], ["mailapp:" + near.rfc_id])

    def test_a_cursor_that_is_not_a_time_is_expired(self):
        for cursor in ("", "123456", "soon"):
            with self.assertRaises(HistoryExpired):
                self.mail.history(cursor)

    def test_a_message_in_two_mailboxes_is_added_once(self):
        reply = self.mail.deliver_reply(self.first, "ada@acme.example")
        self.mail.copy_to(reply, "Outreach")
        self.assertEqual(len(self.mail.history(self.cursor).added), 1)


class MessageTest(Base):
    def test_meta_headers_and_a_400_character_snippet(self):
        first = self.mail.send(outgoing())
        ndr = self.mail.deliver_ndr(first)
        meta = self.mail.message("mailapp:" + ndr.rfc_id)
        self.assertTrue(set(meta.headers) <= set(META_HEADERS))
        self.assertEqual(meta.headers["in-reply-to"], first.rfc_message_id)
        self.assertEqual(meta.headers["content-type"], "multipart/report; report-type=delivery-status; "
                                                       'boundary="b1"')
        self.assertNotIn("received", meta.headers)
        self.assertIn("Your message to ada@acme.example couldn't be delivered", meta.snippet)
        self.assertLessEqual(len(meta.snippet), 400)
        self.assertEqual((meta.labels, meta.thread_id), (("INBOX",), first.thread_id))
        self.assertEqual(meta.subject, "Undeliverable: Your team and the backend role")
        self.assertEqual(meta.internal_ms, ms(self.clock()))

    def test_a_sent_items_message_is_labelled_sent(self):
        first = self.mail.send(outgoing())
        meta = self.mail.message(first.gmail_message_id)
        self.assertEqual(meta.labels, ("SENT",))
        self.assertEqual(meta.headers["message-id"], first.rfc_message_id)
        self.assertEqual(meta.to_addr, "ada@acme.example")

    def test_a_message_not_in_mail_app_is_a_404(self):
        with self.assertRaises(GmailError) as caught:
            self.mail.message("mailapp:<nothing@x.example>")
        self.assertEqual(caught.exception.status, 404)

    def test_thread_is_unsupported(self):
        with self.assertRaises(ThreadUnsupported):
            self.mail.thread("<anything@x.example>")


class ProfileTest(Base):
    def test_the_account_holds_the_address(self):
        self.assertEqual(self.mail.profile(), Profile(email=NEU.address,
                                                     history_id=START.strftime("%Y-%m-%dT%H:%M:%SZ")))

    def test_another_address_is_reported_as_it_is(self):
        self.mail.addresses = ["someone.else@northeastern.edu"]
        self.assertEqual(self.mail.profile().email, "someone.else@northeastern.edu")

    def test_no_such_account_is_signed_out(self):
        self.mail.mailapp_account = "iCloud"
        with self.assertRaises(GmailError) as caught:
            self.mail.profile()
        self.assertEqual(caught.exception.kind, "auth")

    def test_mail_app_closed_fails_every_call_and_sends_nothing(self):
        self.mail.closed = True
        m = outgoing()
        calls = [self.mail.profile, lambda: self.mail.send(m), lambda: self.find(m, START),
                 lambda: self.mail.history("2026-11-02T14:00:00Z"), lambda: self.mail.message("mailapp:<a@b.c>")]
        for call in calls:
            with self.assertRaises(GmailError) as caught:
                call()
            self.assertEqual(caught.exception.kind, "app_closed")
        self.assertEqual(self.mail.sent, [])


class ScriptTest(Base):
    """What reaches osascript: script text fixed in the module, every value as an argument, and the timeouts."""

    def setUp(self):
        super().setUp()
        self.runs: list[tuple[str, list[str], float]] = []
        fake = self.mail._mailapp

        def recording(script, args, timeout):
            self.runs.append((script, list(args), timeout))
            return fake(script, args, timeout)
        self.mail._run = recording

    def test_values_go_as_arguments_never_into_the_script(self):
        m = outgoing(subject='Say "hi" end tell', body='do shell script "rm -rf ~"')
        self.mail.send(m)
        scripts = {mailapp.PROFILE_SCRIPT, mailapp.SEND_SCRIPT, mailapp.REPLY_SCRIPT, mailapp.FIND_SCRIPT,
                   mailapp.HISTORY_SCRIPT, mailapp.MESSAGE_SCRIPT}
        for script, args, _ in self.runs:
            self.assertIn(script, scripts)
            self.assertNotIn("rm -rf", script)
        self.assertIn(m.subject, self.runs[0][1])
        self.assertIn(m.body, self.runs[0][1])

    def test_sends_wait_60_seconds_and_reads_120(self):
        self.mail.send(outgoing())
        self.mail.profile()
        self.mail.history("2026-11-02T14:00:00Z")
        timeouts = {script: timeout for script, _, timeout in self.runs}
        self.assertEqual(timeouts[mailapp.SEND_SCRIPT], 60)
        self.assertLessEqual(timeouts[mailapp.FIND_SCRIPT], 60)  # the poll after a send is within the send's 60
        self.assertEqual(timeouts[mailapp.PROFILE_SCRIPT], 120)
        self.assertEqual(timeouts[mailapp.HISTORY_SCRIPT], 120)

    def test_every_script_checks_mail_app_is_running_and_shares_one_escape(self):
        for script in (mailapp.PROFILE_SCRIPT, mailapp.SEND_SCRIPT, mailapp.REPLY_SCRIPT, mailapp.FIND_SCRIPT,
                       mailapp.HISTORY_SCRIPT, mailapp.MESSAGE_SCRIPT):
            self.assertTrue(script.startswith(mailapp.COMMON_HANDLERS))
            self.assertEqual(script.count("on esc("), 1)
            self.assertIn("my requireMail()", script)
            self.assertNotIn("—", script)

    def test_fields_survive_the_escape(self):
        subject = "tab\there\\back\nline end"
        self.mail.add_to_sent("ada@acme.example", subject, at=START + timedelta(minutes=1))
        hit, _ = self.mail.find_sent("<claim1@knock.local>", ms(START), send_returned_ms=ms(START),
                                     to="ada@acme.example", subject=subject)
        self.assertIsNotNone(hit)

    def test_the_real_runner_is_refused_outside_the_live_checkout(self):
        with mock.patch.object(mailapp.live_checkout, "is_live", lambda *a, **k: False), \
                self.assertRaises(SystemExit):
            mailapp.MailAppClient(NEU, applescript.osascript_runner, self.clock)


class InboundFallbackTest(Base):
    """A client with no threads (Mail.app) is read with `history` over the last 45 days when there is no cursor."""

    def snap(self, threads: dict[str, int], history_id: str = "") -> inbound.Snapshot:
        return inbound.Snapshot(account_id=3, threads=threads, subjects={}, known=frozenset(), history_id=history_id,
                                recent_threads=tuple(threads))

    def test_no_cursor_reads_the_tracked_threads_of_the_last_45_days(self):
        first = self.mail.send(outgoing())
        self.clock.advance(days=3)
        reply = self.mail.deliver_reply(first, "ada@acme.example")
        self.mail.deliver_unrelated("news@x.example", "A newsletter")  # not in a tracked thread
        self.mail.deliver_reply(first, "ada@acme.example", at=self.clock() - timedelta(days=50))  # too old
        self.clock.advance(minutes=1)
        read = inbound.read_mailbox(self.mail, self.snap({first.thread_id: 1}), cfg=CFG, now=self.clock())
        self.assertTrue(read.complete)
        self.assertEqual([m.gmail_message_id for m in read.metas], ["mailapp:" + reply.rfc_id])
        self.assertEqual(read.new_history_id, self.clock().strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual(self.mail.calls[-4:-1], ["profile", "thread", "history"])  # then one message read

    def test_a_failed_45_day_read_is_incomplete_and_keeps_no_cursor(self):
        first = self.mail.send(outgoing())

        def close_on_history(name):
            if name == "history":
                self.mail.closed = True
        self.mail.probe = close_on_history
        read = inbound.read_mailbox(self.mail, self.snap({first.thread_id: 1}), cfg=CFG, now=self.clock())
        self.assertEqual((read.complete, read.new_history_id), (False, None))


class InboundFallbackWithCursorTest(Base):
    def test_a_time_cursor_is_read_with_history(self):
        first = self.mail.send(outgoing())
        cursor = self.mail.profile().history_id
        self.clock.advance(minutes=30)
        reply = self.mail.deliver_reply(first, "ada@acme.example")
        snap = inbound.Snapshot(account_id=3, threads={first.thread_id: 1}, subjects={}, known=frozenset(),
                                history_id=cursor, recent_threads=(first.thread_id,))
        read = inbound.read_mailbox(self.mail, snap, cfg=CFG, now=self.clock())
        self.assertEqual(([m.gmail_message_id for m in read.metas], read.complete),
                         (["mailapp:" + reply.rfc_id], True))
        self.assertNotIn("thread", self.mail.calls)


if __name__ == "__main__":
    unittest.main()
