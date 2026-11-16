"""The Mail.app client (Phase B): the Northeastern account, sent and read through AppleScript.

No test starts osascript or touches Mail.app. `FakeMailApp` is the real `MailAppClient` with its runner replaced by
an in-memory Mail.app that answers each of the client's scripts the way the script does in the real one (records,
escaping, local dates), so these tests run the client's own code: what it asks Mail.app, how it reads the answer,
and when a send in doubt counts as sent, missing or neither.
"""
import re
import shutil
import subprocess
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


def _cursor(text: str) -> datetime:
    return datetime.fromisoformat(text)


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
        with self.assertRaises(GmailError) as caught:
            self.mail.send(m)
        self.assertEqual(caught.exception.kind, "server")
        self.assertEqual(len(self.mail.sent), 1)
        self.assertFalse(caught.exception.retryable)  # it went out: never sent again

    # F1 (final review): once Mail.app may have taken the email, a failure is settled by the Sent Items rule only.
    def send_error(self, m: Outgoing | None = None) -> GmailError:
        with self.assertRaises(GmailError) as caught:
            self.mail.send(m or outgoing())
        return caught.exception

    def test_a_send_mail_app_took_but_sent_items_never_shows_is_never_retryable(self):
        self.mail.unseen_next_send()
        err = self.send_error()
        self.assertEqual(err.kind, "timeout")
        self.assertTrue(err.maybe_sent)
        self.assertFalse(err.retryable)
        self.assertEqual(len(self.mail.sent), 1)

    def test_a_send_script_that_timed_out_is_never_retryable(self):
        self.mail.fail_next_send(GmailError("timeout", None, "osascript took longer than 60 seconds"))
        err = self.send_error()
        self.assertEqual(err.kind, "timeout")
        self.assertFalse(err.retryable)

    def test_a_send_script_that_failed_at_mail_apps_send_is_never_retryable(self):
        self.mail.fail_next_send(GmailError("server", None, "execution error: Mail got an error. (-10000)"))
        self.assertFalse(self.send_error().retryable)

    def test_a_follow_up_whose_reply_script_timed_out_is_never_retryable(self):
        first = self.mail.send(outgoing())
        self.mail.fail_next_send(GmailError("timeout", None, "osascript took longer than 60 seconds"))
        err = self.send_error(outgoing(2, subject="Re: Your team and the backend role", body="Following up.",
                                       thread_id=first.thread_id, in_reply_to=first.rfc_message_id))
        self.assertFalse(err.retryable)

    def test_mail_app_closing_during_the_sent_items_poll_is_never_retryable(self):
        original = self.mail._send_script

        def then_quit(args):
            out = original(args)
            self.mail.closed = True  # Mail.app took it, then quit before Sent Items showed it
            return out
        self.mail._send_script = then_quit
        self.mail.slow_sent(timedelta(minutes=3))
        err = self.send_error()
        self.assertEqual(err.kind, "app_closed")
        self.assertFalse(err.retryable)
        self.assertEqual(len(self.mail.sent), 1)

    def test_mail_app_not_running_before_the_send_stays_retryable_and_nothing_goes(self):
        self.mail.closed = True
        err = self.send_error()
        self.assertEqual(err.kind, "app_closed")
        self.assertTrue(err.retryable)
        self.assertEqual(self.mail.sent, [])

    def test_an_error_before_mail_apps_send_stays_retryable_and_nothing_goes(self):
        self.mail.refuse_next_send("Mail got an error: Can't make class outgoing message. (-2710)")
        err = self.send_error()
        self.assertEqual(err.kind, "server")
        self.assertTrue(err.retryable)
        self.assertFalse(err.maybe_sent)
        self.assertIn("-2710", err.detail)
        self.assertEqual(self.mail.sent, [])

    def test_automation_refused_before_mail_apps_send_is_not_allowed_and_nothing_goes(self):
        self.mail.refuse_next_send("Not authorized to send Apple events to Mail. (-1743)")
        err = self.send_error()
        self.assertEqual(err.kind, "not_allowed")
        self.assertFalse(err.maybe_sent)
        self.assertEqual(self.mail.sent, [])

    def test_a_follow_up_refused_before_mail_apps_send_stays_retryable(self):
        first = self.mail.send(outgoing())
        self.mail.refuse_next_send("Mail got an error: Can't set content. (-10006)")
        err = self.send_error(outgoing(2, subject="Re: Your team and the backend role", body="Following up.",
                                       thread_id=first.thread_id, in_reply_to=first.rfc_message_id))
        self.assertTrue(err.retryable)
        self.assertEqual(len(self.mail.sent), 1)


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

    def test_no_address_or_subject_stays_in_doubt_and_is_never_proof(self):
        self.clock.advance(hours=5)
        for to, subject in (("", "Hello"), ("ada@acme.example", ""), ("", "")):
            self.assertEqual(self.mail.find_sent("<claim1@knock.local>", ms(START), send_returned_ms=ms(START),
                                                 to=to, subject=subject), (None, False))


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

    def test_reads_from_24_hours_before_the_cursor(self):
        cursor = self.clock().strftime("%Y-%m-%dT%H:%M:%SZ")
        self.mail.deliver_unrelated("old@x.example", "Old", at=self.clock() - timedelta(hours=25))
        late = self.mail.deliver_reply(self.first, "ada@acme.example", at=self.clock() - timedelta(hours=3))
        near = self.mail.deliver_unrelated("near@x.example", "Near", at=self.clock() - timedelta(minutes=9))
        page = self.mail.history(cursor)
        self.assertEqual([a.gmail_message_id for a in page.added],
                         ["mailapp:" + late.rfc_id, "mailapp:" + near.rfc_id])
        self.assertEqual(page.history_id, cursor)  # the new cursor is when the read began

    def test_a_mailbox_that_fails_makes_the_read_fail_never_a_silent_skip(self):
        self.mail.deliver_reply(self.first, "ada@acme.example")
        self.mail.fail_mailbox("Tasks")
        with self.assertRaises(GmailError) as caught:
            self.mail.history(self.cursor)
        self.assertEqual(caught.exception.kind, "server")
        self.assertIn("Tasks", caught.exception.detail)

    def test_history_then_message_runs_two_scripts_however_many_messages(self):
        replies = [self.mail.deliver_reply(self.first, f"p{i}@acme.example") for i in range(6)]
        self.mail.scripts.clear()
        page = self.mail.history(self.cursor)
        metas = [self.mail.message(a.gmail_message_id) for a in page.added]
        self.assertEqual(self.mail.scripts, ["list", "fetch"])  # the ids first, then the full records
        self.assertEqual([m.gmail_message_id for m in metas], ["mailapp:" + r.rfc_id for r in replies])
        self.assertEqual({m.thread_id for m in metas}, {self.first.thread_id})
        self.assertEqual(metas[0].from_addr, "p0@acme.example")

    def test_a_message_from_the_page_matches_the_slow_read(self):
        ndr = self.mail.deliver_ndr(self.first)
        page = self.mail.history(self.cursor)
        cached = self.mail.message(page.added[0].gmail_message_id)
        self.clock.advance(days=2)
        self.mail.history(self.clock().strftime("%Y-%m-%dT%H:%M:%SZ"))  # a later page without it
        self.mail.scripts.clear()
        slow = self.mail.message("mailapp:" + ndr.rfc_id)
        self.assertEqual(self.mail.scripts, ["message"])
        self.assertEqual(cached, slow)

    def test_a_cursor_that_is_not_a_time_is_expired(self):
        for cursor in ("", "123456", "soon"):
            with self.assertRaises(HistoryExpired):
                self.mail.history(cursor)

    def test_a_message_in_two_mailboxes_is_added_once(self):
        reply = self.mail.deliver_reply(self.first, "ada@acme.example")
        self.mail.copy_to(reply, "Outreach")
        self.assertEqual(len(self.mail.history(self.cursor).added), 1)

    def test_a_page_holds_every_message_it_names(self):
        self.mail.deliver_unrelated("news@x.example", "A newsletter")
        page = self.mail.history(self.cursor)
        self.assertEqual((page.held, page.more), (True, False))

    def test_ids_already_handled_are_listed_but_never_fetched(self):
        done = self.mail.deliver_reply(self.first, "ada@acme.example")
        self.clock.advance(minutes=1)
        new = self.mail.deliver_reply(self.first, "bob@acme.example")
        page = self.mail.history(self.cursor, skip_ids={done.rfc_id})
        self.assertEqual([a.gmail_message_id for a in page.added], ["mailapp:" + new.rfc_id])
        self.assertEqual(self.mail.fetches, [[new.msgid]])
        page = self.mail.history(self.cursor, skip_ids={done.msgid, f" <{new.msgid}> "})  # bare or bracketed
        self.assertEqual((page.added, len(self.mail.fetches)), ((), 1))  # nothing left to fetch

    def test_at_most_40_full_records_a_read_oldest_first_and_the_cursor_waits_for_the_rest(self):
        stored = []
        for i in range(100):
            stored.append(self.mail.deliver_unrelated(f"p{i}@x.example", f"Note {i}"))
            self.clock.advance(seconds=1)
        page = self.mail.history(self.cursor)
        self.assertEqual(len(self.mail.fetches[-1]), mailapp.FETCH_LIMIT)
        self.assertEqual(mailapp.FETCH_LIMIT, 40)
        self.assertEqual([a.gmail_message_id for a in page.added], ["mailapp:" + s.rfc_id for s in stored[:40]])
        self.assertEqual((page.more, page.history_id), (True, self.cursor))  # left out: the cursor stays
        handled = {s.rfc_id for s in stored[:90]}
        last = self.mail.history(self.cursor, skip_ids=handled)
        self.assertEqual([a.gmail_message_id for a in last.added], ["mailapp:" + s.rfc_id for s in stored[90:]])
        self.assertEqual((last.more, last.history_id), (False, self.clock().strftime("%Y-%m-%dT%H:%M:%SZ")))

    def test_a_fetch_that_runs_out_of_time_keeps_what_it_read_and_the_cursor(self):
        stored = []
        for i in range(30):
            stored.append(self.mail.deliver_unrelated(f"p{i}@x.example", f"Note {i}"))
            self.clock.advance(seconds=1)
        self.mail.seconds_per_record = 5  # Mail.app is slow today: 80 s buys about 16 records
        page = self.mail.history(self.cursor)
        read = [a.gmail_message_id for a in page.added]
        self.assertTrue(0 < len(read) < 30, len(read))
        self.assertEqual(read, ["mailapp:" + s.rfc_id for s in stored[:len(read)]])
        self.assertEqual((page.more, page.history_id), (True, self.cursor))  # left out: the cursor stays
        self.mail.seconds_per_record = 0
        rest = self.mail.history(self.cursor, skip_ids={a[len("mailapp:"):] for a in read})
        self.assertEqual([a.gmail_message_id for a in rest.added], ["mailapp:" + s.rfc_id for s in stored[len(read):]])
        self.assertEqual((rest.more, rest.history_id), (False, self.clock().strftime("%Y-%m-%dT%H:%M:%SZ")))

    def test_the_fetch_is_given_the_time_budget_and_the_record_cap(self):
        self.mail.deliver_unrelated("news@x.example", "A newsletter")
        self.mail.history(self.cursor)
        self.assertEqual(self.mail.fetch_limits[-1], (mailapp.FETCH_BUDGET, mailapp.FETCH_LIMIT))
        self.assertEqual((mailapp.FETCH_BUDGET, mailapp.FETCH_LIMIT), (80, 40))

    def test_messages_listed_but_never_found_do_not_hold_the_read(self):
        new = self.mail.deliver_reply(self.first, "ada@acme.example")
        original = self.mail._list_script

        def with_ghosts(args):  # 45 ids Mail.app lists but never shows again (finished, not cut by the budget)
            when = mailapp.local_text(self.clock() - timedelta(hours=1))
            return original(args) + [f"id\t1\tInbox\tghost{i}@x.example\t{when}" for i in range(45)]
        self.mail._list_script = with_ghosts
        page = self.mail.history(self.cursor)
        self.assertEqual([a.gmail_message_id for a in page.added], ["mailapp:" + new.rfc_id])
        self.assertEqual((page.more, page.history_id), (False, self.clock().strftime("%Y-%m-%dT%H:%M:%SZ")))

    def test_two_mailboxes_with_one_name_are_read_separately(self):
        self.mail.mailboxes.append("Outreach")  # a second "Outreach" (a nested folder of that name)
        first = self.mail.deliver_reply(self.first, "ada@acme.example", mailbox="Outreach")
        second = self.mail.add_to_mailbox("Outreach", "bob@acme.example", NEU.address, "Re: hello",
                                           position=len(self.mail.mailboxes))
        page = self.mail.history(self.cursor)
        self.assertEqual(sorted(a.gmail_message_id for a in page.added),
                         sorted(["mailapp:" + first.rfc_id, "mailapp:" + second.rfc_id]))

    def test_a_cursor_10_days_old_catches_up_in_slices_of_at_most_3_days(self):
        old = self.clock() - timedelta(days=10)
        cursor = old.strftime("%Y-%m-%dT%H:%M:%SZ")
        daily = [self.mail.deliver_unrelated(f"d{i}@x.example", f"Day {i}", at=old + timedelta(days=i, hours=1))
                 for i in range(10)]
        handled: set[str] = set()
        for n in range(8):
            page = self.mail.history(cursor, skip_ids=handled)
            lo, hi = self.mail.windows[-1]
            self.assertLessEqual(hi - lo, timedelta(days=3))
            self.assertEqual(lo, _cursor(cursor) - mailapp.HISTORY_OVERLAP)
            handled |= {a.gmail_message_id[len("mailapp:"):] for a in page.added}
            if page.history_id == self.clock().strftime("%Y-%m-%dT%H:%M:%SZ"):
                self.assertFalse(page.more)  # the slice reached now: caught up, the cursor is the read start
                break
            self.assertTrue(page.more)  # behind: not fully read, the cursor moves to the slice end
            self.assertEqual(_cursor(page.history_id), hi)
            cursor = page.history_id
        self.assertLessEqual(n, 5)
        printed = [m for ids in self.mail.fetches for m in ids]
        self.assertEqual(sorted(printed), sorted(d.msgid for d in daily))  # every message once, none twice
        self.assertEqual(len(printed), len(set(printed)))

    def test_a_slice_ends_now_when_now_is_closer_than_3_days(self):
        self.mail.history(self.cursor)
        lo, hi = self.mail.windows[-1]
        self.assertEqual((lo, hi), (_cursor(self.cursor) - mailapp.HISTORY_OVERLAP, self.clock().replace(microsecond=0)))

    def test_nothing_new_fetches_nothing(self):
        done = self.mail.deliver_reply(self.first, "ada@acme.example")
        self.mail.scripts.clear()
        page = self.mail.history(self.cursor, skip_ids={done.rfc_id})
        self.assertEqual((page.added, page.more), ((), False))
        self.assertEqual(self.mail.scripts, ["list"])

    def test_a_message_gone_between_the_list_and_the_fetch_is_left_out(self):
        gone = self.mail.deliver_reply(self.first, "ada@acme.example")
        kept = self.mail.deliver_reply(self.first, "bob@acme.example")
        original = self.mail._list_script

        def then_delete(args):
            out = original(args)
            self.mail.store.remove(gone)
            return out
        self.mail._list_script = then_delete
        page = self.mail.history(self.cursor)
        self.assertEqual([a.gmail_message_id for a in page.added], ["mailapp:" + kept.rfc_id])
        self.assertFalse(page.more)

    def test_a_mailbox_that_fails_in_the_fetch_fails_the_read(self):
        self.mail.deliver_reply(self.first, "ada@acme.example", mailbox="Outreach")
        original = self.mail._list_script

        def then_fail(args):
            out = original(args)
            self.mail.fail_mailbox("Outreach")
            return out
        self.mail._list_script = then_fail
        with self.assertRaises(GmailError) as caught:
            self.mail.history(self.cursor)
        self.assertIn("Outreach", caught.exception.detail)


class MessageTest(Base):
    def test_meta_headers_and_a_2000_character_snippet(self):
        first = self.mail.send(outgoing())
        ndr = self.mail.deliver_ndr(first)
        meta = self.mail.message("mailapp:" + ndr.rfc_id)
        self.assertTrue(set(meta.headers) <= set(META_HEADERS))
        self.assertEqual(meta.headers["in-reply-to"], first.rfc_message_id)
        self.assertEqual(meta.headers["content-type"], "multipart/report; report-type=delivery-status; "
                                                       'boundary="b1"')
        self.assertNotIn("received", meta.headers)
        self.assertIn("Your message to ada@acme.example couldn't be delivered", meta.snippet)
        self.assertLessEqual(len(meta.snippet), 2000)
        self.assertEqual((meta.labels, meta.thread_id), (("INBOX",), first.thread_id))
        self.assertEqual(meta.subject, "Undeliverable: Your team and the backend role")
        self.assertEqual(meta.internal_ms, ms(self.clock()))

    def test_the_snippet_keeps_all_2000_characters_the_script_returns(self):
        first = self.mail.send(outgoing())
        long = self.mail.deliver_reply(first, "ada@acme.example", content="word " * 600)
        meta = self.mail.message("mailapp:" + long.rfc_id)
        self.assertEqual(len(meta.snippet), 1999)  # 2000 characters of body, the last space collapsed away

    def test_the_delivery_block_of_a_long_notice_is_appended_to_the_snippet(self):
        first = self.mail.send(outgoing())
        body = "quoted " * 2000 + "\nFinal-Recipient: rfc822;lee@example.com\nAction: failed\nStatus: 5.1.1\n" + "x" * 3000
        notice = self.mail.deliver_reply(first, "postmaster@acme.example", content=body)
        meta = self.mail.message("mailapp:" + notice.rfc_id)
        self.assertIn("Final-Recipient: rfc822;lee@example.com Action: failed Status: 5.1.1", meta.snippet)
        self.assertLessEqual(len(meta.snippet), 2000 + 1 + 1200)

    def test_reporting_mta_is_the_block_when_there_is_no_final_recipient(self):
        first = self.mail.send(outgoing())
        body = "quoted " * 2000 + "\nReporting-MTA: dns;mx.example.com\nStatus: 5.1.1\n"
        meta = self.mail.message("mailapp:" + self.mail.deliver_reply(first, "p@acme.example", content=body).rfc_id)
        self.assertIn("Reporting-MTA: dns;mx.example.com Status: 5.1.1", meta.snippet)

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
                   mailapp.LIST_SCRIPT, mailapp.FETCH_SCRIPT, mailapp.MESSAGE_SCRIPT}
        for script, args, _ in self.runs:
            self.assertIn(script, scripts)
            self.assertNotIn("rm -rf", script)
        self.assertIn(m.subject, self.runs[0][1])
        self.assertIn(m.body, self.runs[0][1])

    def test_both_bounds_of_the_slice_reach_the_scripts_as_arguments(self):
        self.mail.deliver_unrelated("news@x.example", "A newsletter")
        cursor = "2026-11-01T14:00:00Z"
        self.mail.history(cursor)
        lo = mailapp.local_parts(datetime(2026, 10, 31, 14, 0, tzinfo=UTC))
        hi = mailapp.local_parts(START)  # now: closer than 3 days after the slice start
        for script, args, _ in self.runs:
            if script in (mailapp.LIST_SCRIPT, mailapp.FETCH_SCRIPT):
                self.assertEqual(args[1:9], lo + hi)
        for script in (mailapp.LIST_SCRIPT, mailapp.FETCH_SCRIPT):
            self.assertIn("whose date received > lo and date received <= hi", script)

    def test_sends_wait_60_seconds_and_reads_120(self):
        self.mail.send(outgoing())
        self.mail.profile()
        self.mail.deliver_unrelated("news@x.example", "A newsletter")
        self.mail.history("2026-11-02T14:00:00Z")
        timeouts = {script: timeout for script, _, timeout in self.runs}
        self.assertEqual(timeouts[mailapp.SEND_SCRIPT], 60)
        self.assertLessEqual(timeouts[mailapp.FIND_SCRIPT], 60)  # the poll after a send is within the send's 60
        self.assertEqual(timeouts[mailapp.PROFILE_SCRIPT], 120)
        self.assertEqual(timeouts[mailapp.LIST_SCRIPT], 120)
        self.assertEqual(timeouts[mailapp.FETCH_SCRIPT], 120)

    def test_every_script_checks_mail_app_is_running_and_shares_one_escape(self):
        for script in (mailapp.PROFILE_SCRIPT, mailapp.SEND_SCRIPT, mailapp.REPLY_SCRIPT, mailapp.FIND_SCRIPT,
                       mailapp.LIST_SCRIPT, mailapp.FETCH_SCRIPT, mailapp.MESSAGE_SCRIPT):
            self.assertTrue(script.startswith(mailapp.COMMON_HANDLERS))
            self.assertEqual(script.count("on esc("), 1)
            self.assertIn("my requireMail()", script)
            self.assertNotIn("\u2014", script)

    def test_every_script_compiles(self):
        """osacompile only compiles (nothing runs, Mail.app is not asked anything); it needs macOS."""
        if shutil.which("osacompile") is None:
            self.skipTest("osacompile is macOS only")
        for script in (mailapp.PROFILE_SCRIPT, mailapp.SEND_SCRIPT, mailapp.REPLY_SCRIPT, mailapp.FIND_SCRIPT,
                       mailapp.LIST_SCRIPT, mailapp.FETCH_SCRIPT, mailapp.MESSAGE_SCRIPT):
            done = subprocess.run(["osacompile", "-o", "/dev/null", "-"], input=script, capture_output=True,
                                  text=True, encoding="utf-8", timeout=60, check=False)
            self.assertEqual(done.returncode, 0, f"{script.split('on run')[-1][:80]!r}: {done.stderr}")

    def test_every_loop_walks_a_list_already_read(self):
        # On the real Mail.app, `repeat with h in (messages of mb whose ...)` re-runs the whose filter for every
        # property read of `h` (history of 17 messages outlasted 120 s); reading the list into a variable first took
        # about 3 s. So no script walks an expression: every `repeat with x in` names a variable.
        for script in (mailapp.PROFILE_SCRIPT, mailapp.SEND_SCRIPT, mailapp.REPLY_SCRIPT, mailapp.FIND_SCRIPT,
                       mailapp.LIST_SCRIPT, mailapp.FETCH_SCRIPT, mailapp.MESSAGE_SCRIPT):
            loops = re.findall(r"repeat with \w+ in (.*)", script)
            for target in loops:
                self.assertRegex(target.strip(), r"^\w+$", f"a loop walks an expression: {target!r}")

    def test_reads_never_query_message_by_message_or_read_a_resolved_list_in_bulk(self):
        # On the real Mail.app a `whose message id is` query costs about 2 s a message, and a bulk property read on a
        # list already resolved (`message id of found`) fails (task-B4-live-check.md): each mailbox's window is
        # resolved once and walked item by item.
        for script in (mailapp.LIST_SCRIPT, mailapp.FETCH_SCRIPT):
            self.assertNotIn("whose message id", script)
            self.assertNotRegex(script, r"(message id|date received) of found")
            self.assertIn("repeat with h in found", script)
        self.assertIn('"more"', mailapp.FETCH_SCRIPT)

    def test_profile_reads_the_address_list_before_walking_it(self):
        # On the real Mail.app, `repeat with a in (email addresses of account ...)` fails (-1700): get it first.
        self.assertIn("set addrs to (get email addresses of account acctName)", mailapp.PROFILE_SCRIPT)
        self.assertIn("repeat with a in addrs", mailapp.PROFILE_SCRIPT)

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

    def snap(self, threads: dict[str, int], history_id: str = "",
             earliest: datetime | None = None) -> inbound.Snapshot:
        return inbound.Snapshot(account_id=3, threads=threads, subjects={}, known=frozenset(), history_id=history_id,
                                recent_threads=tuple(threads),
                                earliest_send=earliest.strftime("%Y-%m-%dT%H:%M:%SZ") if earliest else None)

    def test_no_cursor_reads_from_an_hour_before_the_first_send(self):
        first = self.mail.send(outgoing())
        self.mail.deliver_unrelated("early@x.example", "Before", at=START - timedelta(days=2))  # before the window
        self.clock.advance(days=1)
        reply = self.mail.deliver_reply(first, "ada@acme.example")
        self.clock.advance(seconds=1)
        news = self.mail.deliver_unrelated("news@x.example", "A newsletter")  # not in a tracked thread
        self.clock.advance(minutes=1)
        snap = self.snap({first.thread_id: 1}, earliest=START)
        read = inbound.read_mailbox(self.mail, snap, cfg=CFG, now=self.clock())
        self.assertTrue(read.complete)
        # Every message fetched is read, so applying it records it and it is never fetched again.
        self.assertEqual([m.gmail_message_id for m in read.metas],
                         ["mailapp:" + reply.rfc_id, "mailapp:" + news.rfc_id])
        self.assertEqual(read.new_history_id, self.clock().strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual(self.mail.windows[0][0], START - timedelta(hours=1) - mailapp.HISTORY_OVERLAP)
        self.assertEqual(self.mail.calls[1:], ["profile", "thread", "history", "message", "message"])

    def test_no_cursor_and_an_old_first_send_catches_up_and_keeps_its_place(self):
        first = self.mail.send(outgoing())
        self.clock.advance(days=5)
        snap = self.snap({first.thread_id: 1}, earliest=START)
        read = inbound.read_mailbox(self.mail, snap, cfg=CFG, now=self.clock())
        self.assertFalse(read.complete)  # behind: the account waits while it catches up
        self.assertEqual(_cursor(read.new_history_id), self.mail.windows[0][1])  # but the slice end is kept
        self.assertLessEqual(self.mail.windows[0][1] - self.mail.windows[0][0], timedelta(days=3))

    def test_a_failed_first_read_is_incomplete_and_keeps_no_cursor(self):
        first = self.mail.send(outgoing())

        def close_on_history(name):
            if name == "history":
                self.mail.closed = True
        self.mail.probe = close_on_history
        read = inbound.read_mailbox(self.mail, self.snap({first.thread_id: 1}, earliest=START), cfg=CFG,
                                    now=self.clock())
        self.assertEqual((read.complete, read.new_history_id), (False, None))


class InboundSkipTest(Base):
    """The inbound read gives the client every Message-ID it already handled, so Mail.app fetches only the rest, and a
    read the client left part of is incomplete with the cursor where it was."""

    def setUp(self):
        super().setUp()
        self.first = self.mail.send(outgoing())
        self.cursor = self.mail.profile().history_id
        self.clock.advance(minutes=30)

    def snap(self, known=frozenset()) -> inbound.Snapshot:
        return inbound.Snapshot(account_id=3, threads={self.first.thread_id: 1}, subjects={}, known=frozenset(known),
                                history_id=self.cursor, recent_threads=(self.first.thread_id,))

    def test_known_mail_is_skipped_by_its_message_id(self):
        done = self.mail.deliver_reply(self.first, "ada@acme.example")
        new = self.mail.deliver_unrelated("news@x.example", "A newsletter")
        read = inbound.read_mailbox(self.mail, self.snap({"mailapp:" + done.rfc_id, "gmail-1"}), cfg=CFG,
                                    now=self.clock())
        self.assertEqual(self.mail.fetches, [[new.msgid]])
        self.assertEqual([m.gmail_message_id for m in read.metas], ["mailapp:" + new.rfc_id])  # read, though untracked
        self.assertTrue(read.complete)

    def test_a_read_left_part_way_is_incomplete_and_keeps_the_cursor(self):
        for i in range(45):
            self.mail.deliver_unrelated(f"p{i}@x.example", f"Note {i}")
            self.clock.advance(seconds=1)
        read = inbound.read_mailbox(self.mail, self.snap(), cfg=CFG, now=self.clock())
        self.assertEqual(len(read.metas), 40)
        self.assertEqual((read.complete, read.new_history_id), (False, self.cursor))  # the cursor stays where it was


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
