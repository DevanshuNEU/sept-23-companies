"""
Send/reply bugs in mail/sender.py, each reproduced against a fake Gmail.

Run from the project root:  python3 -m unittest discover -s tests -t .
"""
import datetime, fcntl, json, os, sys, tempfile, unittest

HOME = tempfile.mkdtemp(prefix="outreach-mail-test-")
os.environ["OUTREACH_MAIL_HOME"] = HOME
os.environ["OUTREACH_GMAIL_DIR"] = tempfile.mkdtemp(prefix="outreach-gmail-test-")   # never the real token
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mail"))
import store, gmail, sender, server  # noqa: E402

ME = "me@gmail.com"

def iso(minutes_ago=0):
    return (datetime.datetime.now().astimezone() - datetime.timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")

def contact(cid, status, **kw):
    c = {"id": cid, "name": "Jane Doe", "email": f"{cid}@acme.com", "company": "Acme", "tz": "America/New_York",
         "status": status, "draft": {"subject": "hello", "body": "hi"}, "followup": None, "history": []}
    c.update(kw)
    return c

def msg(frm, subject="re: hello", **headers):
    return {"from": frm, "subject": subject, "date": "", "internalDate": 0, "labels": [],
            "headers": {k.lower().replace("_", "-"): v for k, v in headers.items()}}


class FakeGmail:
    """Replaces the network calls in gmail.py; records what the sender asked for."""
    def __init__(self, threads=None, whoami_error=None, send_error=None, sent_box=None):
        self.threads, self.whoami_error, self.send_error = threads or {}, whoami_error, send_error
        self.sent_box = sent_box if sent_box is not None else []
        self.sends, self.lock_held_during = [], []

    def _lock_is_free(self):
        with open(store.LOCK, "w") as lk:
            try:
                fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lk, fcntl.LOCK_UN)
                return True
            except BlockingIOError:
                return False

    def whoami(self):
        if not self._lock_is_free(): self.lock_held_during.append("whoami")
        if self.whoami_error: raise self.whoami_error
        return ME

    def thread(self, thread_id):
        if not self._lock_is_free(): self.lock_held_during.append("thread")
        return self.threads.get(thread_id, [])

    def send(self, to, subject, body, from_name, from_addr, thread_id=None, in_reply_to=None, message_id=None):
        if not self._lock_is_free(): self.lock_held_during.append("send")
        db = store.load()
        claimed = next(c for c in db["contacts"] if c["email"] == to)
        self.sends.append({"to": to, "message_id": message_id, "claimed_status": claimed["status"],
                           "claimed_rfc": claimed.get("pendingRfcId")})
        if self.send_error: raise self.send_error
        return {"messageId": "m1", "threadId": "t-" + to, "rfcMessageId": message_id}

    def find_sent(self, rfc_id, to, subject):
        if not self._lock_is_free(): self.lock_held_during.append("find_sent")
        return next((m for m in self.sent_box if m["rfcMessageId"] == rfc_id), None)

    def install(self, case):
        for name in ("whoami", "thread", "send", "find_sent"):
            orig = getattr(gmail, name, None)
            setattr(gmail, name, getattr(self, name))
            case.addCleanup(setattr, gmail, name, orig) if orig else case.addCleanup(delattr, gmail, name)
        orig_auth = gmail.authorized
        gmail.authorized = lambda: True
        case.addCleanup(setattr, gmail, "authorized", orig_auth)


class SenderTest(unittest.TestCase):
    def setUp(self):
        for f in os.listdir(HOME): os.remove(os.path.join(HOME, f))
        # send window open all day, every day, so tests do not depend on the clock
        store.save_config({"window": ["00:00", "23:59"], "weekdays": list(range(7)), "gapMinutes": [0, 0]})

    def seed(self, *contacts):
        store.save({"contacts": list(contacts), "firstSendDate": None})

    def status(self, cid):
        return next(c for c in store.load()["contacts"] if c["id"] == cid)


class AutoReplyIsNotAReply(SenderTest):
    def test_out_of_office_header_does_not_count_as_reply(self):
        fake = FakeGmail(threads={"t1": [msg(ME, "hello"),
                                          msg("Jane <a@acme.com>", "Out of Office", auto_submitted="auto-replied")]})
        fake.install(self)
        self.seed(contact("a", "sent", threadId="t1", sentAt=iso()))
        sender.main()
        self.assertEqual(self.status("a")["status"], "sent")

    def test_automatic_reply_subject_does_not_count_as_reply(self):
        fake = FakeGmail(threads={"t1": [msg(ME, "hello"), msg("Jane <a@acme.com>", "Automatic reply: hello")]})
        fake.install(self)
        self.seed(contact("a", "sent", threadId="t1", sentAt=iso()))
        sender.main()
        self.assertEqual(self.status("a")["status"], "sent")

    def test_human_reply_still_counts(self):
        fake = FakeGmail(threads={"t1": [msg(ME, "hello"), msg("Jane <a@acme.com>", "Re: hello")]})
        fake.install(self)
        self.seed(contact("a", "sent", threadId="t1", sentAt=iso()))
        sender.main()
        self.assertEqual(self.status("a")["status"], "replied")


class SignedInMeansTheTokenWorks(SenderTest):
    def test_dead_token_does_not_crash_and_sends_nothing(self):
        fake = FakeGmail(whoami_error=RuntimeError("Gmail API 400: invalid_grant"))
        fake.install(self)
        self.seed(contact("a", "approved", approvedAt=iso()))
        sender.main()                                            # must not raise
        self.assertEqual(fake.sends, [])
        self.assertIn("sign-in", open(sender.LOGF).read().lower())


class LockIsNotHeldAcrossGmailCalls(SenderTest):
    def test_gmail_calls_run_outside_the_store_lock(self):
        fake = FakeGmail(threads={"t1": [msg(ME, "hello")]})
        fake.install(self)
        self.seed(contact("a", "sent", threadId="t1", sentAt=iso(120)), contact("b", "approved", approvedAt=iso()))
        sender.main()
        self.assertEqual(len(fake.sends), 1)
        self.assertEqual(fake.lock_held_during, [])


class NoDoubleSend(SenderTest):
    def test_send_is_claimed_with_its_message_id_before_the_call(self):
        fake = FakeGmail()
        fake.install(self)
        self.seed(contact("a", "approved", approvedAt=iso()))
        sender.main()
        s = fake.sends[0]
        self.assertEqual(s["claimed_status"], "sending")
        self.assertTrue(s["message_id"])
        self.assertEqual(s["claimed_rfc"], s["message_id"])
        self.assertEqual(self.status("a")["status"], "sent")
        self.assertEqual(self.status("a")["rfcMessageId"], s["message_id"])

    def test_timed_out_send_cannot_be_approved_again(self):
        fake = FakeGmail(send_error=TimeoutError("timed out"))
        fake.install(self)
        self.seed(contact("a", "approved", approvedAt=iso()))
        sender.main()
        self.assertEqual(self.status("a")["status"], "send_unknown")
        res = server.act("/api/approve", {"id": "a"})
        self.assertIn("error", res)

    def test_unclear_send_cannot_be_skipped_or_edited(self):
        self.seed(contact("a", "send_unknown", pendingRfcId="<x@gmail.com>", sendingAt=iso(1)),
                  contact("b", "sending", pendingRfcId="<y@gmail.com>", sendingAt=iso(1)))
        for cid in ("a", "b"):
            self.assertIn("error", server.act("/api/skip", {"id": cid}))
            self.assertIn("error", server.act("/api/save", {"id": cid, "which": "draft", "subject": "s", "body": "b"}))
            self.assertIn("error", server.act("/api/unapprove", {"id": cid}))

    def test_unknown_send_found_in_sent_folder_becomes_sent_without_resending(self):
        fake = FakeGmail(sent_box=[{"messageId": "m9", "threadId": "t9", "rfcMessageId": "<x@gmail.com>"}])
        fake.install(self)
        self.seed(contact("a", "send_unknown", approvedAt=iso(30), pendingRfcId="<x@gmail.com>", sendingAt=iso(20)))
        sender.main()
        c = self.status("a")
        self.assertEqual(c["status"], "sent")
        self.assertEqual(c["threadId"], "t9")
        self.assertEqual(fake.sends, [])

    def test_unknown_send_missing_from_sent_folder_becomes_failed_and_approvable(self):
        fake = FakeGmail(sent_box=[])
        fake.install(self)
        self.seed(contact("a", "send_unknown", approvedAt=iso(30), pendingRfcId="<x@gmail.com>", sendingAt=iso(20)))
        sender.main()
        self.assertEqual(self.status("a")["status"], "failed")
        self.assertEqual(fake.sends, [])
        self.assertEqual(server.act("/api/approve", {"id": "a"}), {"ok": True, "status": "approved"})

    def test_recent_unknown_send_waits_before_being_called_failed(self):
        fake = FakeGmail(sent_box=[])
        fake.install(self)
        self.seed(contact("a", "send_unknown", approvedAt=iso(3), pendingRfcId="<x@gmail.com>", sendingAt=iso(2)))
        sender.main()
        self.assertEqual(self.status("a")["status"], "send_unknown")

    def test_claim_left_behind_by_a_crash_is_reconciled_not_resent(self):
        fake = FakeGmail(sent_box=[{"messageId": "m9", "threadId": "t9", "rfcMessageId": "<x@gmail.com>"}])
        fake.install(self)
        self.seed(contact("a", "sending", approvedAt=iso(30), pendingRfcId="<x@gmail.com>", sendingAt=iso(20)))
        sender.main()
        self.assertEqual(self.status("a")["status"], "sent")
        self.assertEqual(fake.sends, [])

    def test_unknown_followup_found_in_sent_folder_becomes_followup_sent(self):
        fake = FakeGmail(sent_box=[{"messageId": "m9", "threadId": "t1", "rfcMessageId": "<y@gmail.com>"}])
        fake.install(self)
        self.seed(contact("a", "send_unknown", threadId="t1", sentAt=iso(9000), rfcMessageId="<x@gmail.com>",
                          followup={"subject": "Re: hello", "body": "bump"}, pendingFollowup=True,
                          pendingRfcId="<y@gmail.com>", sendingAt=iso(20)))
        sender.main()
        c = self.status("a")
        self.assertEqual(c["status"], "followup_sent")
        self.assertEqual(c["rfcMessageId"], "<x@gmail.com>")


class FindSent(unittest.TestCase):
    def test_matches_on_message_id_header(self):
        calls = []
        def fake_api(method, path, body=None, query=None):
            calls.append(path)
            if path == "/messages":
                return {"messages": [{"id": "m1", "threadId": "t1"}, {"id": "m2", "threadId": "t2"}]}
            mid = path.rsplit("/", 1)[1]
            hdr = {"m1": "<other@gmail.com>", "m2": "<x@gmail.com>"}[mid]
            return {"id": mid, "threadId": {"m1": "t1", "m2": "t2"}[mid],
                    "payload": {"headers": [{"name": "Message-ID", "value": hdr},
                                            {"name": "To", "value": "a@acme.com"}, {"name": "Subject", "value": "hello"}]}}
        orig = gmail.api
        gmail.api = fake_api
        self.addCleanup(setattr, gmail, "api", orig)
        found = gmail.find_sent("<x@gmail.com>", "a@acme.com", "hello")
        self.assertEqual(found, {"messageId": "m2", "threadId": "t2", "rfcMessageId": "<x@gmail.com>"})

    def test_returns_none_when_absent(self):
        orig = gmail.api
        gmail.api = lambda method, path, body=None, query=None: {} if path == "/messages" else None
        self.addCleanup(setattr, gmail, "api", orig)
        self.assertIsNone(gmail.find_sent("<x@gmail.com>", "a@acme.com", "hello"))


if __name__ == "__main__":
    unittest.main()
