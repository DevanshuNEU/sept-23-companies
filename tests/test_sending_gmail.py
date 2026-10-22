"""The real Gmail client, tested without Gmail: a fake transport answers every request with canned JSON.

No test here makes a network request, opens a browser, reads a real token or constructs the client against the
real secrets folder outside a refusal. setUp replaces urlopen and webbrowser.open with functions that fail the
test, so a broken guard shows up as a failure, never as a real call.
"""
import base64
import builtins
import email
import email.policy
import io
import json
import os
import socket
import stat
import tempfile
import unittest
import urllib.error
from email.message import Message
from unittest import mock

from outreach.sending import gmail
from outreach.sending.mail import GmailError, HistoryExpired, MessageMeta, Outgoing, Sent

ACCOUNT = "devanshurajesh@gmail.com"
SECRET = "ya29.tok-secret-abc"


def outgoing(to="founder@sendwell.example", subject="Backend role at Sendwell", *, message_id="<a1@gmail.com>",
             thread_id=None, in_reply_to=None, references=(), body="Hi Sam,\n\nShort note.\n\nDevanshu"):
    return Outgoing(to=to, subject=subject, body=body, from_name="Devanshu C", from_addr=ACCOUNT,
                    message_id=message_id, thread_id=thread_id, in_reply_to=in_reply_to,
                    references=tuple(references))


def http_error(code: int, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://gmail.googleapis.com/x", code, "error", Message(),
                                  io.BytesIO(body.encode()))


class Transport:
    """Records every request; `handler(method, path, body, query)` answers or raises."""

    def __init__(self, handler=None):
        self.requests = []
        self.handler = handler or (lambda *a: {})

    def __call__(self, method, path, body, query):
        self.requests.append((method, path, body, query))
        return self.handler(method, path, body, query)


class SentFolder:
    """Answers the Sent listing and metadata reads. `entries` is newest first: (gmail id, Message-ID, ms)."""

    def __init__(self, entries, page=50):
        self.entries = entries
        self.page = page
        self.list_calls = 0
        self.reads = []

    def __call__(self, method, path, body, query):
        if path == "/messages":
            assert query["labelIds"] == "SENT", query
            self.list_calls += 1
            start = int(query.get("pageToken") or 0)
            size = int(query["maxResults"])
            chunk = self.entries[start:start + size]
            res = {"messages": [{"id": gid, "threadId": "th-" + gid} for gid, _, _ in chunk]}
            if start + size < len(self.entries):
                res["nextPageToken"] = str(start + size)
            return res
        gid = path.rsplit("/", 1)[1]
        assert query["format"] == "metadata" and query["metadataHeaders"] == ["Message-ID"], query
        self.reads.append(gid)
        _, rfc, at = next(e for e in self.entries if e[0] == gid)
        res = {"id": gid, "threadId": "th-" + gid,
               "payload": {"headers": [{"name": "Message-Id", "value": rfc},
                                        {"name": "To", "value": "founder@sendwell.example"},
                                        {"name": "Subject", "value": "Backend role at Sendwell"}]}}
        if at is not None:
            res["internalDate"] = str(at)
        return res


class NoNetwork(unittest.TestCase):
    def setUp(self):
        def refuse(*a, **k):
            raise AssertionError("a test tried to reach the network or open a browser")
        for target in ("urllib.request.urlopen", "webbrowser.open"):
            patcher = mock.patch(target, side_effect=refuse)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.dir = tempfile.mkdtemp(prefix="outreach-gmail-test-")

    def client(self, handler=None, token=SECRET):
        transport = Transport(handler)
        return gmail.GmailClient(self.dir, transport=transport, token_provider=lambda: token), transport

    def live(self, value: bool):
        patcher = mock.patch.object(gmail.live_checkout, "is_live", lambda *a, **k: value)
        patcher.start()
        self.addCleanup(patcher.stop)


class LiveGuardTest(NoNetwork):
    def write_secrets(self):
        with open(os.path.join(self.dir, "token.json"), "w") as f:
            json.dump({"access_token": SECRET, "refresh_token": "1//refresh", "expires_at": 9999999999}, f)
        with open(os.path.join(self.dir, "client_secret.json"), "w") as f:
            json.dump({"installed": {"client_id": "cid", "client_secret": "csecret"}}, f)

    def test_real_client_refuses_outside_the_live_checkout(self):
        self.write_secrets()
        self.live(False)
        opened = []
        real_open, real_os_open = builtins.open, os.open

        def spy_open(file, *a, **k):
            opened.append(str(file))
            return real_open(file, *a, **k)

        def spy_os_open(path, *a, **k):
            opened.append(str(path))
            return real_os_open(path, *a, **k)

        transport = Transport(lambda *a: self.fail("the transport ran before the live check"))
        calls = {
            "profile": lambda c: c.profile(),
            "send": lambda c: c.send(outgoing()),
            "history": lambda c: c.history("100"),
            "message": lambda c: c.message("m1"),
            "thread": lambda c: c.thread("t1"),
            "find_sent": lambda c: c.find_sent("<a1@gmail.com>", 0),
        }
        with mock.patch("builtins.open", spy_open), mock.patch("os.open", spy_os_open):
            for which, client in enumerate((gmail.GmailClient(self.dir), gmail.GmailClient(self.dir, transport=transport))):
                for name, call in calls.items():
                    with self.subTest(name=name, client=which), self.assertRaises(SystemExit) as cm:
                        call(client)
                    self.assertIn("live checkout", str(cm.exception.code))
                    self.assertNotIn(SECRET, str(cm.exception.code))
        real_dir = os.path.realpath(self.dir)
        self.assertEqual([p for p in opened if os.path.realpath(p).startswith(real_dir)], [])
        self.assertEqual(transport.requests, [])

    def test_the_token_function_itself_refuses_before_opening(self):
        self.write_secrets()
        self.live(False)
        with mock.patch("builtins.open", side_effect=AssertionError("opened")):
            with self.assertRaises(SystemExit) as cm:
                gmail._token(self.dir)
        self.assertIn("live checkout", str(cm.exception.code))

    def test_default_folder_refuses_at_construction_outside_live(self):
        self.live(False)
        spellings = [gmail.DEFAULT_DIR, gmail.DEFAULT_DIR + "/", gmail.DEFAULT_DIR.upper(),
                     "~/Library/Application Support/outreach-mail"]
        for spelling in spellings:
            with self.subTest(spelling=spelling), self.assertRaises(SystemExit) as cm:
                gmail.GmailClient(spelling, transport=Transport(), token_provider=lambda: "x")
            self.assertIn("live checkout", str(cm.exception.code))
        with mock.patch.dict(os.environ, {"OUTREACH_GMAIL_DIR": ""}), self.assertRaises(SystemExit):
            gmail.GmailClient(transport=Transport(), token_provider=lambda: "x")

    def test_gmail_dir_follows_the_environment(self):
        self.assertEqual(gmail.gmail_dir(), os.environ["OUTREACH_GMAIL_DIR"])
        self.assertFalse(gmail.is_default_dir(gmail.gmail_dir()))
        with mock.patch.dict(os.environ, {"OUTREACH_GMAIL_DIR": ""}):
            self.assertEqual(gmail.gmail_dir(), gmail.DEFAULT_DIR)

    def test_scopes_are_the_old_mailers_so_the_token_keeps_working(self):
        self.assertEqual(gmail.SCOPES, "https://www.googleapis.com/auth/gmail.send "
                                       "https://www.googleapis.com/auth/gmail.metadata")

    def test_auth_into_the_real_folder_refuses_outside_live(self):
        self.live(False)
        with self.assertRaises(SystemExit) as cm:
            gmail.check_auth_location(gmail.DEFAULT_DIR)
        self.assertIn("live checkout", str(cm.exception.code))
        with mock.patch.object(gmail, "authorize") as authorize, \
                mock.patch.dict(os.environ, {"OUTREACH_GMAIL_DIR": gmail.DEFAULT_DIR}):
            with self.assertRaises(SystemExit) as cm:
                gmail.main(["auth"])
            self.assertIn("live checkout", str(cm.exception.code))
        authorize.assert_not_called()

    def test_auth_into_a_scratch_folder_is_allowed(self):
        self.live(False)
        gmail.check_auth_location(self.dir)
        with open(os.path.join(self.dir, "client_secret.json"), "w") as f:
            json.dump({"installed": {"client_id": "cid"}}, f)
        with mock.patch.object(gmail, "authorize") as authorize, \
                mock.patch.dict(os.environ, {"OUTREACH_GMAIL_DIR": self.dir}):
            self.assertEqual(gmail.main(["auth"]), 0)
        authorize.assert_called_once_with(self.dir)

    def test_no_bypass_flag_exists(self):
        with open(gmail.__file__, encoding="utf-8") as f:
            src = f.read()
        for word in ("allow-non-live", "--force", "OUTREACH_LIVE"):
            self.assertNotIn(word, src)


class TokenTest(NoNetwork):
    """`_token` in the live checkout, on a scratch folder, with the token endpoint replaced."""

    def setUp(self):
        super().setUp()
        self.live(True)
        self.token_path = os.path.join(self.dir, "token.json")
        with open(os.path.join(self.dir, "client_secret.json"), "w") as f:
            json.dump({"installed": {"client_id": "cid", "client_secret": "csecret"}}, f)

    def write_token(self, **tok):
        with open(self.token_path, "w") as f:
            json.dump(tok, f)

    def test_fresh_token_is_used_as_is(self):
        self.write_token(access_token=SECRET, refresh_token="1//r", expires_at=9999999999)
        with mock.patch.object(gmail, "_post", side_effect=AssertionError("refreshed")):
            self.assertEqual(gmail._token(self.dir), SECRET)

    def test_expired_token_is_refreshed_and_rewritten_private(self):
        self.write_token(access_token="old", refresh_token="1//r", expires_at=0, scope=gmail.SCOPES)
        os.chmod(self.token_path, 0o644)
        with mock.patch.object(gmail, "_post", return_value={"access_token": "new", "expires_in": 3600}) as post:
            self.assertEqual(gmail._token(self.dir), "new")
        form = post.call_args[0][1]
        self.assertEqual((form["grant_type"], form["refresh_token"]), ("refresh_token", "1//r"))
        with open(self.token_path) as f:
            saved = json.load(f)
        self.assertEqual((saved["access_token"], saved["refresh_token"], saved["scope"]), ("new", "1//r", gmail.SCOPES))
        self.assertGreater(saved["expires_at"], 0)
        self.assertEqual(stat.S_IMODE(os.stat(self.token_path).st_mode), 0o600)
        self.assertEqual(sorted(os.listdir(self.dir)), ["client_secret.json", "token.json"])

    def test_revoked_refresh_token_is_a_sign_in_error_without_secrets(self):
        self.write_token(access_token="old", refresh_token="1//revoked", expires_at=0)
        body = '{"error": "invalid_grant", "error_description": "Token has been expired or revoked."}'
        with mock.patch.object(gmail, "_post", side_effect=http_error(400, body)):
            with self.assertRaises(GmailError) as cm:
                gmail._token(self.dir)
        self.assertEqual(cm.exception.kind, "auth")
        for secret in ("1//revoked", "csecret"):
            self.assertNotIn(secret, str(cm.exception))

    def test_missing_token_is_signed_out(self):
        with self.assertRaises(GmailError) as cm:
            gmail._token(self.dir)
        self.assertEqual(cm.exception.kind, "auth")

    def test_unreadable_token_is_signed_out_and_not_echoed(self):
        with open(self.token_path, "w") as f:
            f.write('{"access_token": "' + SECRET + '"')
        with self.assertRaises(GmailError) as cm:
            gmail._token(self.dir)
        self.assertEqual(cm.exception.kind, "auth")
        self.assertNotIn(SECRET, str(cm.exception))


class MimeTest(unittest.TestCase):
    def parse(self, m):
        return email.message_from_bytes(gmail.build_mime(m), policy=email.policy.default)

    def test_mime_is_plain_text_one_recipient_no_cc(self):
        msg = self.parse(outgoing(body="Hi Sam,\n\nCafé at 5?\n\nDevanshu"))
        self.assertEqual(msg["To"], "founder@sendwell.example")
        self.assertEqual(msg["From"], "Devanshu C <devanshurajesh@gmail.com>")
        self.assertEqual(msg["Subject"], "Backend role at Sendwell")
        self.assertEqual(msg["Message-ID"], "<a1@gmail.com>")
        self.assertFalse(msg.is_multipart())
        self.assertEqual(msg.get_content_type(), "text/plain")
        self.assertEqual(msg.get_content_charset(), "utf-8")
        self.assertEqual(msg.get_content().rstrip("\n"), "Hi Sam,\n\nCafé at 5?\n\nDevanshu")
        names = {k.lower() for k in msg.keys()}
        self.assertEqual(names, {"to", "from", "subject", "message-id", "mime-version", "content-type",
                                 "content-transfer-encoding"})

    def test_follow_up_mime_has_in_reply_to_and_references(self):
        msg = self.parse(outgoing(subject="Re: Backend role at Sendwell", message_id="<a3@gmail.com>",
                                  thread_id="th1", in_reply_to="<a2@gmail.com>",
                                  references=("<a1@gmail.com>", "<a2@gmail.com>")))
        self.assertEqual(msg["In-Reply-To"], "<a2@gmail.com>")
        self.assertEqual(msg["References"], "<a1@gmail.com> <a2@gmail.com>")
        self.assertIsNone(self.parse(outgoing())["In-Reply-To"])
        self.assertIsNone(self.parse(outgoing())["References"])

    def test_two_recipients_are_refused(self):
        for to in ("a@x.example, b@y.example", "a@x.example; b@y.example", "Sam <a@x.example>, b@y.example",
                   "", "not-an-address"):
            with self.subTest(to=to), self.assertRaises(ValueError):
                gmail.build_mime(outgoing(to=to))

    def test_empty_message_id_and_header_injection_are_refused(self):
        with self.assertRaises(ValueError):
            gmail.build_mime(outgoing(message_id=""))
        with self.assertRaises(ValueError):
            gmail.build_mime(outgoing(subject="Hi\nBcc: x@y.example"))

    def test_new_message_id_uses_the_sender_domain(self):
        mid = gmail.new_message_id(ACCOUNT)
        self.assertTrue(mid.startswith("<") and mid.endswith("@gmail.com>"), mid)
        self.assertNotEqual(mid, gmail.new_message_id(ACCOUNT))


class ClientCallsTest(NoNetwork):
    def test_profile(self):
        client, t = self.client(lambda *a: {"emailAddress": ACCOUNT, "historyId": 4521})
        profile = client.profile()
        self.assertEqual((profile.email, profile.history_id), (ACCOUNT, "4521"))
        self.assertEqual(t.requests[0][:2], ("GET", "/profile"))

    def test_send_posts_raw_and_thread_id(self):
        client, t = self.client(lambda *a: {"id": "g1", "threadId": "th1"})
        sent = client.send(outgoing())
        self.assertEqual(sent, Sent(gmail_message_id="g1", thread_id="th1", rfc_message_id="<a1@gmail.com>"))
        method, path, body, _ = t.requests[0]
        self.assertEqual((method, path, set(body)), ("POST", "/messages/send", {"raw"}))
        raw = email.message_from_bytes(base64.urlsafe_b64decode(body["raw"]), policy=email.policy.default)
        self.assertEqual(raw["Message-ID"], "<a1@gmail.com>")
        client.send(outgoing(subject="Re: Backend role at Sendwell", message_id="<a2@gmail.com>", thread_id="th1",
                             in_reply_to="<a1@gmail.com>", references=("<a1@gmail.com>",)))
        self.assertEqual(set(t.requests[1][2]), {"raw", "threadId"})
        self.assertEqual(t.requests[1][2]["threadId"], "th1")

    def test_a_refused_outgoing_never_reaches_the_transport(self):
        client, t = self.client(lambda *a: {"id": "g1", "threadId": "th1"})
        with self.assertRaises(GmailError) as cm:
            client.send(outgoing(to="a@x.example, b@y.example"))
        self.assertEqual(cm.exception.kind, "bad_request")
        self.assertEqual(t.requests, [])

    def test_http_errors_map_to_kinds(self):
        rows = [
            (401, '{"error": {"code": 401, "message": "Invalid Credentials"}}', "auth"),
            (403, '{"error": {"code": 403, "errors": [{"reason": "dailyLimitExceeded"}], "message": "Daily"}}',
             "daily_limit"),
            (429, '{"error": {"code": 429, "message": "Too many concurrent requests for user"}}', "rate_limit"),
            (400, '{"error": {"code": 400, "message": "Invalid To header"}}', "bad_request"),
            (503, "Service Unavailable", "server"),
        ]
        for status, body, kind in rows:
            def fail(*a, status=status, body=body):
                raise http_error(status, body)
            client, _ = self.client(fail)
            with self.subTest(status=status), self.assertRaises(GmailError) as cm:
                client.send(outgoing())
            self.assertEqual((cm.exception.kind, cm.exception.status), (kind, status))
            self.assertLessEqual(len(cm.exception.detail), 300)

    def test_transport_failures_map_to_kinds(self):
        rows = [
            (TimeoutError("timed out"), "timeout"),
            (socket.timeout("timed out"), "timeout"),
            (urllib.error.URLError(TimeoutError("timed out")), "timeout"),
            (urllib.error.URLError("nodename nor servname provided"), "network"),
            (ConnectionResetError("reset by peer"), "network"),
        ]
        for error, kind in rows:
            def fail(*a, error=error):
                raise error
            client, _ = self.client(fail)
            with self.subTest(error=error), self.assertRaises(GmailError) as cm:
                client.profile()
            self.assertEqual(cm.exception.kind, kind)

    def test_logs_and_errors_never_hold_the_token(self):
        echoes = [
            '{"error": {"code": 401, "message": "bad header Authorization: Bearer ' + SECRET + '"}}',
            "upstream said: " + SECRET,
            "raw echo s3cr3t-plain-value here",
        ]
        for token in (SECRET, "s3cr3t-plain-value"):
            for body in echoes:
                def fail(*a, body=body):
                    raise http_error(401, body)
                client, _ = self.client(fail, token=token)
                with self.subTest(token=token, body=body), self.assertRaises(GmailError) as cm:
                    client.profile()
                for text in (cm.exception.detail, str(cm.exception), repr(cm.exception)):
                    self.assertNotIn(token, text)
                    self.assertNotIn("tok-secret", text)

    def test_message_reads_metadata_and_keeps_only_the_listed_headers(self):
        def answer(method, path, body, query):
            self.assertEqual(query["format"], "metadata")
            for name in ("From", "To", "Subject", "Auto-Submitted", "X-Failed-Recipients", "Message-ID"):
                self.assertIn(name, query["metadataHeaders"])
            return {"id": "g9", "threadId": "th9", "internalDate": "1762786800000", "labelIds": ["INBOX"],
                    "snippet": "Happy to chat",
                    "payload": {"headers": [{"name": "From", "value": "Sam <founder@sendwell.example>"},
                                            {"name": "To", "value": ACCOUNT},
                                            {"name": "Subject", "value": "Re: Backend role"},
                                            {"name": "Auto-Submitted", "value": "no"},
                                            {"name": "Received", "value": "from mx"},
                                            {"name": "Message-ID", "value": "<r1@sendwell.example>"}]}}
        client, t = self.client(answer)
        meta = client.message("g9")
        self.assertEqual(t.requests[0][1], "/messages/g9")
        self.assertEqual(meta, MessageMeta(gmail_message_id="g9", thread_id="th9",
                                           from_addr="Sam <founder@sendwell.example>", to_addr=ACCOUNT,
                                           subject="Re: Backend role", internal_ms=1762786800000,
                                           labels=("INBOX",), headers={"auto-submitted": "no",
                                                                       "message-id": "<r1@sendwell.example>"},
                                           snippet="Happy to chat"))

    def test_thread_lists_every_message(self):
        def answer(method, path, body, query):
            self.assertEqual(path, "/threads/th1")
            return {"id": "th1", "messages": [
                {"id": "g1", "threadId": "th1", "internalDate": "1", "labelIds": ["SENT"], "payload": {"headers": []}},
                {"id": "g2", "threadId": "th1", "internalDate": "2", "labelIds": ["INBOX"],
                 "payload": {"headers": [{"name": "Subject", "value": "Re: x"}]}}]}
        client, _ = self.client(answer)
        self.assertEqual([m.gmail_message_id for m in client.thread("th1")], ["g1", "g2"])

    def test_ids_are_quoted_into_the_path(self):
        client, t = self.client(lambda *a: {"id": "x", "threadId": "y", "payload": {}})
        client.message("../profile")
        self.assertEqual(t.requests[0][1], "/messages/..%2Fprofile")


class FindSentTest(NoNetwork):
    CLAIM = 1_762_786_800_000

    def test_find_sent_returns_the_exact_message_id(self):
        folder = SentFolder([("g3", "<n2@gmail.com>", self.CLAIM + 3), ("g2", "<a1@gmail.com>", self.CLAIM + 1),
                             ("g1", "<old@gmail.com>", self.CLAIM - 5)])
        client, _ = self.client(folder)
        self.assertEqual(client.find_sent("<a1@gmail.com>", self.CLAIM),
                         (Sent("g2", "th-g2", "<a1@gmail.com>"), True))

    def test_find_sent_matches_by_message_id_only(self):
        # Same To and Subject, another Message-ID: not a hit (follow-ups share a subject).
        folder = SentFolder([("g2", "<other@gmail.com>", self.CLAIM + 1), ("g1", "<old@gmail.com>", self.CLAIM - 5)])
        client, _ = self.client(folder)
        self.assertEqual(client.find_sent("<a1@gmail.com>", self.CLAIM), (None, True))

    def test_find_sent_stops_at_the_claim_time_and_says_complete(self):
        entries = [(f"n{i}", f"<n{i}@gmail.com>", self.CLAIM + 1000 - i) for i in range(30)]
        entries += [(f"o{i}", f"<o{i}@gmail.com>", self.CLAIM - 1 - i) for i in range(90)]
        folder = SentFolder(entries)
        client, _ = self.client(folder)
        self.assertEqual(client.find_sent("<a1@gmail.com>", self.CLAIM), (None, True))
        self.assertEqual(folder.list_calls, 1)          # the first page already reached past the claim
        self.assertEqual(len(folder.reads), 50)

    def test_find_sent_reaching_the_end_of_sent_is_complete(self):
        folder = SentFolder([("g1", "<n1@gmail.com>", self.CLAIM + 5)])
        client, _ = self.client(folder)
        self.assertEqual(client.find_sent("<a1@gmail.com>", self.CLAIM), (None, True))

    def test_find_sent_after_ten_pages_says_incomplete(self):
        folder = SentFolder([(f"n{i}", f"<n{i}@gmail.com>", self.CLAIM + 10_000 - i) for i in range(700)])
        client, _ = self.client(folder)
        self.assertEqual(client.find_sent("<a1@gmail.com>", self.CLAIM), (None, False))
        self.assertEqual(folder.list_calls, 10)

    def test_a_message_without_a_date_proves_nothing(self):
        folder = SentFolder([(f"n{i}", f"<n{i}@gmail.com>", None) for i in range(600)])
        client, _ = self.client(folder)
        self.assertEqual(client.find_sent("<a1@gmail.com>", self.CLAIM), (None, False))


class HistoryTest(NoNetwork):
    def test_history_404_is_history_expired(self):
        def gone(*a):
            raise http_error(404, '{"error": {"code": 404, "message": "Requested entity was not found."}}')
        client, _ = self.client(gone)
        with self.assertRaises(HistoryExpired):
            client.history("100")

    def test_empty_cursor_is_history_expired_without_a_call(self):
        client, t = self.client()
        with self.assertRaises(HistoryExpired):
            client.history("")
        self.assertEqual(t.requests, [])

    def test_other_history_errors_stay_gmail_errors(self):
        def busy(*a):
            raise http_error(503, "Backend Error")
        client, _ = self.client(busy)
        with self.assertRaises(GmailError):
            client.history("100")

    def test_history_reads_every_page(self):
        pages = {
            None: {"history": [{"id": "101", "messagesAdded": [{"message": {"id": "g1", "threadId": "th1",
                                                                            "labelIds": ["INBOX"]}}]}],
                   "nextPageToken": "p2", "historyId": "105"},
            "p2": {"history": [{"id": "104", "messagesAdded": [
                       {"message": {"id": "g2", "threadId": "th2", "labelIds": ["SENT"]}},
                       {"message": {"id": "g1", "threadId": "th1", "labelIds": ["INBOX"]}}]}],
                   "historyId": "106"},
        }

        def answer(method, path, body, query):
            self.assertEqual((method, path), ("GET", "/history"))
            self.assertEqual((query["startHistoryId"], query["historyTypes"]), ("100", "messageAdded"))
            return pages[query.get("pageToken")]
        client, t = self.client(answer)
        page = client.history("100")
        self.assertEqual([(a.gmail_message_id, a.thread_id, a.labels) for a in page.added],
                         [("g1", "th1", ("INBOX",)), ("g2", "th2", ("SENT",))])
        self.assertEqual(page.history_id, "106")
        self.assertEqual(len(t.requests), 2)

    def test_endless_history_falls_back_to_reading_threads(self):
        def endless(method, path, body, query):
            return {"history": [], "nextPageToken": "more", "historyId": "200"}
        client, _ = self.client(endless)
        with self.assertRaises(HistoryExpired):
            client.history("100")


if __name__ == "__main__":
    unittest.main()
