"""The real Gmail client for the new sender, standard library only. A port of `mail/gmail.py`; it does not import
`mail/`, and that file is not changed.

Secrets live outside the project, in ~/Library/Application Support/outreach-mail/ (OUTREACH_GMAIL_DIR overrides it):
  client_secret.json   the OAuth "Desktop app" client downloaded from Google Cloud Console
  token.json           written by sign-in (mode 600); holds the refresh token

The token is the old mailer's: the same folder, file and scopes, so the switch needs no new sign-in.
Scopes: gmail.send (send approved emails) and gmail.metadata (read headers, to notice replies and bounces).
Neither can read a message body or delete anything. users.history.list, users.messages.list (without `q`),
messages.get and threads.get with format=metadata all accept gmail.metadata (Gmail API reference).

Live checkout only: the token is read by `_token`, whose first statement refuses outside the live checkout, before
any file is opened, and every client call goes through it. Building a client on the real secrets folder refuses
too. Tests never build a client on the real folder except to see it refuse; they pass a fake transport.

  python -m outreach.sending.gmail auth     sign in once in the browser (you type your password into Google)
  python -m outreach.sending.gmail whoami   print the signed-in address
"""
import base64
import hashlib
import http.client
import http.server
import json
import os
import secrets
import socket
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from datetime import datetime, timezone
from email import message_from_bytes
from email import policy as email_policy
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import live_checkout
from outreach.sending.mail import (META_HEADERS, Added, GmailError, HistoryExpired, HistoryPage, MailClient,
                                   MessageMeta, Outgoing, Profile, Sent, check_outgoing, classify_http, miss_is_proof,
                                   normalise_message_id, page_reaches, scrub, settle_minutes)

__all__ = ["DEFAULT_DIR", "SCOPES", "gmail_dir", "is_default_dir", "build_mime", "new_message_id", "GmailClient",
           "check_auth_location", "authorize", "main"]

DEFAULT_DIR = os.path.expanduser("~/Library/Application Support/outreach-mail")
CLIENT_FILE, TOKEN_FILE = "client_secret.json", "token.json"
SCOPES = "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.metadata"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TIMEOUT = 30
SIGN_IN = "python -m outreach.sending.gmail auth"

FIND_SENT_PAGES, FIND_SENT_PAGE_SIZE = 10, 50
HISTORY_PAGES, HISTORY_PAGE_SIZE = 50, 500
# Header names asked for on every metadata read: who, what, and the META_HEADERS the classifier needs.
READ_HEADERS = ["From", "To", "Subject", "Auto-Submitted", "X-Autoreply", "X-Autorespond", "Precedence",
                "X-Auto-Response-Suppress", "Message-ID", "In-Reply-To", "References", "X-Failed-Recipients",
                "Content-Type"]
assert {h.lower() for h in READ_HEADERS} == set(META_HEADERS) | {"from", "to", "subject"}

Transport = Callable[[str, str, dict | None, dict | None], dict]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an HTTP error, never followed, so the Authorization header never goes to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Every request this module makes goes through this opener.
_OPENER = urllib.request.build_opener(_NoRedirect)


def gmail_dir() -> str:
    """`OUTREACH_GMAIL_DIR` when set (tests point it at a scratch folder), else the real secrets folder."""
    return os.environ.get("OUTREACH_GMAIL_DIR") or DEFAULT_DIR


def _normalise(path: str) -> str:
    return os.path.realpath(os.path.abspath(os.path.expanduser(str(path))))


def _fold(path: str) -> str:
    # APFS ignores letter case and Unicode normalisation form, so spellings that differ only that way are one folder.
    return unicodedata.normalize("NFD", path).casefold()


def is_default_dir(directory: str) -> bool:
    """True when `directory` is the real secrets folder under any spelling (a link, other case, a trailing slash)."""
    candidate, real = _normalise(directory), _normalise(DEFAULT_DIR)
    if _fold(candidate) == _fold(real):
        return True
    return os.path.exists(candidate) and os.path.exists(real) and os.path.samefile(candidate, real)


def check_auth_location(directory: str | None = None) -> None:
    """Signing in writes the real token, so it happens only from the live checkout (a scratch folder is fine)."""
    if is_default_dir(directory or gmail_dir()):
        live_checkout.require_live("Gmail sign-in into the real secrets folder")


# -- the message ------------------------------------------------------------------------------------------------

def build_mime(m: Outgoing) -> bytes:
    """One plain-text UTF-8 email: To, From, Subject, Message-ID, and In-Reply-To and References only for a reply.
    No Cc, Bcc, attachment or HTML part. ValueError for anything but one address or for a missing Message-ID."""
    check_outgoing(m)
    msg = EmailMessage()
    msg["To"] = m.to.strip()
    msg["From"] = formataddr((m.from_name, m.from_addr))
    msg["Subject"] = m.subject
    msg["Message-ID"] = m.message_id
    if m.in_reply_to:
        msg["In-Reply-To"] = m.in_reply_to
    refs = m.references or ((m.in_reply_to,) if m.in_reply_to else ())
    if refs:
        msg["References"] = " ".join(refs)
    msg.set_content(m.body, charset="utf-8")
    raw = msg.as_bytes()
    # The id in the bytes is the id the claim saved: a Sent scan looks for exactly this one.
    written = message_from_bytes(raw, policy=email_policy.default)["Message-ID"]
    if normalise_message_id(str(written or "")) != normalise_message_id(m.message_id):
        raise ValueError("the Message-ID written is not the one claimed")
    return raw


def new_message_id(from_addr: str) -> str:
    return make_msgid(domain=from_addr.rpartition("@")[2])


# -- the token --------------------------------------------------------------------------------------------------

def _write_private(path: str, data: dict) -> None:
    """Write JSON readable only by him (mode 600 from the first byte), replacing the old file in one step."""
    tmp = f"{path}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _client(directory: str) -> dict:
    with open(os.path.join(directory, CLIENT_FILE)) as f:
        c = json.load(f)
    return c.get("installed") or c.get("web")


def _post(url: str, form: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST")
    with _OPENER.open(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def _google_error(err: urllib.error.HTTPError) -> str:
    """Only the `error` code of a Google OAuth error body (`invalid_grant`), never the rest of it."""
    try:
        body = json.loads(err.read(4096) or b"{}")
        code = body.get("error") if isinstance(body, dict) else None
        return code if isinstance(code, str) and code.replace("_", "").isalnum() else f"HTTP {err.code}"
    except (ValueError, OSError):
        return f"HTTP {err.code}"
    finally:
        err.close()


def _token(directory: str) -> str:
    """Every Gmail call reads the token here, so this is the one place the live-checkout rule is enforced."""
    if not live_checkout.is_live():
        raise SystemExit(f"the real Gmail token is only used from the live checkout ({live_checkout.LIVE_ROOT})")
    path = os.path.join(directory, TOKEN_FILE)
    try:
        with open(path) as f:
            tok = json.load(f)
    except FileNotFoundError:
        raise GmailError("auth", None, f"not signed in to Gmail; run: {SIGN_IN}") from None
    except (OSError, ValueError):
        raise GmailError("auth", None, f"the Gmail token file is unreadable; run: {SIGN_IN}") from None
    if not isinstance(tok, dict) or not (tok.get("access_token") or tok.get("refresh_token")):
        raise GmailError("auth", None, f"the Gmail token file holds no token; run: {SIGN_IN}")
    try:
        expires_at = float(tok.get("expires_at", 0))
    except (TypeError, ValueError):
        expires_at = 0.0
    if time.time() >= expires_at or not tok.get("access_token"):
        if not tok.get("refresh_token"):
            raise GmailError("auth", None, f"the Gmail token has no refresh token; run: {SIGN_IN}")
        try:
            c = _client(directory)
        except (OSError, ValueError, AttributeError):
            raise GmailError("auth", None, "the OAuth client file is missing or unreadable") from None
        if not c or not c.get("client_id"):
            raise GmailError("auth", None, "the OAuth client file has no client_id")
        try:
            new = _post(TOKEN_URL, {"client_id": c["client_id"], "client_secret": c.get("client_secret", ""),
                                    "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
        except urllib.error.HTTPError as err:
            # The token endpoint's errors are a sign-in problem (400 invalid_grant: revoked or expired refresh
            # token) or Google's trouble, never a sending limit: auth for 400, 401 and 403, server for the rest.
            kind = "auth" if err.code in (400, 401, 403) else "server"
            raise GmailError(kind, err.code, f"token refresh failed: {_google_error(err)}") from None
        except (urllib.error.URLError, http.client.HTTPException, OSError) as err:
            raise GmailError("network", None, f"token refresh failed: {type(err).__name__}") from None
        except ValueError:
            raise GmailError("server", None, "token refresh answer was unreadable") from None
        if not isinstance(new, dict) or not new.get("access_token"):
            raise GmailError("auth", None, "token refresh gave no access token")
        tok.update(new)
        tok["expires_at"] = time.time() + float(new.get("expires_in", 3600)) - 60
        _write_private(path, tok)
    return tok["access_token"]


def _urlopen(method: str, path: str, body: dict | None, query: dict | None, token: str) -> dict:
    """The one real Gmail API call. Refuses outside the live checkout whatever token it was handed."""
    if not live_checkout.is_live():
        raise SystemExit(f"the real Gmail API is only called from the live checkout ({live_checkout.LIVE_ROOT})")
    url = API + path + ("?" + urllib.parse.urlencode(query, doseq=True) if query else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with _OPENER.open(req, timeout=TIMEOUT) as r:
        return json.loads(r.read() or b"{}")


def _http_detail(body: str) -> str:
    """Gmail's own reason and message from an error body, else the start of the body (scrubbed later)."""
    try:
        err = json.loads(body).get("error")
    except (ValueError, AttributeError):
        return body
    if isinstance(err, dict):
        reasons = [e.get("reason", "") for e in err.get("errors", []) if isinstance(e, dict)]
        return " ".join(x for x in [",".join(r for r in reasons if r), str(err.get("message", ""))] if x)
    return str(err) if err else body


def _headers(msg: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for h in (msg or {}).get("payload", {}).get("headers", []) or []:
        name = str(h.get("name", "")).lower()
        if name and name not in out:
            out[name] = str(h.get("value", ""))
    return out


def _date(value) -> int:
    """Gmail's internalDate as an int, or GmailError: a bad date is Gmail's fault, never a crash in the tick."""
    try:
        return int(value)
    except (TypeError, ValueError):
        raise GmailError("server", None, "Gmail gave a message without a usable date") from None


def _id(obj: dict, key: str = "id") -> str:
    value = obj.get(key) if isinstance(obj, dict) else None
    if not value:
        raise GmailError("server", None, f"Gmail gave a message without its {key}")
    return str(value)


def _meta(msg: dict) -> MessageMeta:
    h = _headers(msg)
    return MessageMeta(
        gmail_message_id=_id(msg), thread_id=str(msg.get("threadId", "")),
        from_addr=h.get("from", ""), to_addr=h.get("to", ""), subject=h.get("subject", ""),
        internal_ms=_date(msg.get("internalDate")), labels=tuple(msg.get("labelIds", []) or ()),
        headers={k: h[k] for k in META_HEADERS if k in h}, snippet=str(msg.get("snippet", "")))


def _quote(gmail_id: str) -> str:
    return urllib.parse.quote(str(gmail_id), safe="")


# -- the client -------------------------------------------------------------------------------------------------

class GmailClient(MailClient):
    """Gmail through its REST API. `transport(method, path, body, query) -> dict` replaces the HTTP call in tests;
    `token_provider` runs before every call either way (by default `_token`, the live-checkout gate), and is only
    accepted with a transport. `clock` (aware now) is for tests of find_sent's timing."""

    def __init__(self, directory: str | None = None, *, transport: Transport | None = None,
                 token_provider: Callable[[], str] | None = None, clock: Callable[[], datetime] | None = None,
                 reconcile_after_minutes: int | None = None):
        if token_provider is not None and transport is None:
            raise ValueError("a token_provider replaces the real token only with a fake transport")
        self.directory = _normalise(directory or gmail_dir())
        if is_default_dir(self.directory):
            live_checkout.require_live("the real Gmail client")
        self._transport = transport
        self._token_provider = token_provider or (lambda: _token(self.directory))
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.settle_minutes = settle_minutes(reconcile_after_minutes)

    def _call(self, method: str, path: str, body: dict | None = None, query: dict | None = None) -> dict:
        token = self._token_provider()          # SystemExit outside the live checkout, before anything else
        try:
            if self._transport is None:
                return _urlopen(method, path, body, query, token)
            return self._transport(method, path, body, query)
        except urllib.error.HTTPError as err:
            try:
                text = err.read(8192).decode("utf-8", "replace")
            except OSError:
                text = ""
            finally:
                err.close()
            kind = classify_http(err.code, text)
            raise GmailError(kind, err.code, scrub(_http_detail(text), token)) from None
        except (TimeoutError, socket.timeout):
            raise GmailError("timeout", None, f"{method} {path.split('/')[1]} timed out") from None
        except urllib.error.URLError as err:
            if isinstance(err.reason, (TimeoutError, socket.timeout)):
                raise GmailError("timeout", None, f"{method} {path.split('/')[1]} timed out") from None
            raise GmailError("network", None, scrub(f"network: {err.reason}", token)) from None
        except (http.client.HTTPException, OSError) as err:
            raise GmailError("network", None, f"network: {type(err).__name__}") from None
        except ValueError:
            raise GmailError("server", None, "unreadable Gmail answer") from None

    def profile(self) -> Profile:
        res = self._call("GET", "/profile")
        return Profile(email=str(res.get("emailAddress", "")), history_id=str(res.get("historyId", "")))

    def send(self, m: Outgoing) -> Sent:
        try:
            raw = base64.urlsafe_b64encode(build_mime(m)).decode()
        except ValueError as err:
            raise GmailError("bad_request", None, f"refused before sending: {err}") from None
        body = {"raw": raw}
        if m.thread_id:
            body["threadId"] = m.thread_id
        res = self._call("POST", "/messages/send", body)
        if not res.get("id") or not res.get("threadId"):
            raise GmailError("server", None, "Gmail answered the send without message and thread ids")
        sent = Sent(gmail_message_id=str(res["id"]), thread_id=str(res["threadId"]), rfc_message_id=m.message_id)
        # Settling an unclear send looks for this Message-ID in Sent, so check Gmail kept it. If the check itself
        # fails, the send still succeeded: report it as sent (raising would make a sent email look unclear).
        try:
            stored = _headers(self._call("GET", f"/messages/{_quote(sent.gmail_message_id)}",
                                         query={"format": "metadata", "metadataHeaders": ["Message-ID"]}))
        except GmailError:
            return sent
        actual = normalise_message_id(stored.get("message-id"))
        if not actual:
            return sent                     # no header is no evidence of a rewrite: the same as a failed check
        if actual != normalise_message_id(m.message_id):
            raise GmailError("message_id_rewritten", None, "Gmail stored the email under another Message-ID",
                             sent=Sent(sent.gmail_message_id, sent.thread_id, actual))
        return sent

    def history(self, start_history_id: str) -> HistoryPage:
        if not start_history_id:
            raise HistoryExpired("no history cursor")
        added: list[Added] = []
        seen: set[str] = set()
        last = start_history_id
        page_token = None
        for _ in range(HISTORY_PAGES):
            query = {"startHistoryId": start_history_id, "historyTypes": "messageAdded",
                     "maxResults": HISTORY_PAGE_SIZE}
            if page_token:
                query["pageToken"] = page_token
            try:
                res = self._call("GET", "/history", query=query)
            except GmailError as err:
                if err.status == 404:
                    raise HistoryExpired("history cursor is too old") from None
                raise
            for record in res.get("history", []) or []:
                for item in record.get("messagesAdded", []) or []:
                    msg = item.get("message", {}) or {}
                    gid = str(msg.get("id", ""))
                    if gid and gid not in seen:
                        seen.add(gid)
                        added.append(Added(gmail_message_id=gid, thread_id=str(msg.get("threadId", "")),
                                           labels=tuple(msg.get("labelIds", []) or ())))
            last = str(res.get("historyId") or last)
            page_token = res.get("nextPageToken")
            if not page_token:
                return HistoryPage(added=tuple(added), history_id=last)
        # Too much to page through: read the tracked threads instead and take a fresh cursor.
        raise HistoryExpired(f"history is longer than {HISTORY_PAGES} pages")

    def message(self, gmail_message_id: str) -> MessageMeta:
        return _meta(self._call("GET", f"/messages/{_quote(gmail_message_id)}",
                                query={"format": "metadata", "metadataHeaders": READ_HEADERS}))

    def thread(self, thread_id: str) -> list[MessageMeta]:
        res = self._call("GET", f"/threads/{_quote(thread_id)}",
                         query={"format": "metadata", "metadataHeaders": READ_HEADERS})
        return [_meta(m) for m in res.get("messages", []) or []]

    def find_sent(self, rfc_message_id: str, not_before_ms: int, *, send_returned_ms: int) -> tuple[Sent | None, bool]:
        """Page Sent (Trash and Spam included) newest first and read each Message-ID; match it exactly, never by
        To or Subject. The rules for when a miss is proof are MailClient.find_sent's. Gmail does not promise the
        listing order, so a page is read to its end and judged by its last dated message. Ten pages of 50 that
        never reach the claim time make an incomplete scan.
        """
        wanted = normalise_message_id(rfc_message_id)
        started_ms = int(self._clock().timestamp() * 1000)
        page_token, complete = None, False
        for _ in range(FIND_SENT_PAGES):
            query = {"labelIds": "SENT", "includeSpamTrash": "true", "maxResults": FIND_SENT_PAGE_SIZE}
            if page_token:
                query["pageToken"] = page_token
            res = self._call("GET", "/messages", query=query)
            dates: list[int | None] = []
            for ref in res.get("messages", []) or []:
                gid = _id(ref)
                msg = self._call("GET", f"/messages/{_quote(gid)}",
                                 query={"format": "metadata", "metadataHeaders": ["Message-ID"]})
                if normalise_message_id(_headers(msg).get("message-id")) == wanted:
                    return Sent(gmail_message_id=gid, thread_id=str(msg.get("threadId") or ref.get("threadId", "")),
                                rfc_message_id=rfc_message_id), True
                when = msg.get("internalDate")
                dates.append(None if when in (None, "") else _date(when))
            page_token = res.get("nextPageToken")
            if page_reaches(dates, not_before_ms) or not page_token:
                complete = True
                break
        return None, miss_is_proof(complete, started_ms, send_returned_ms, self.settle_minutes)


# -- sign-in ----------------------------------------------------------------------------------------------------

def authorize(directory: str) -> None:
    """Browser sign-in with PKCE and a loopback redirect. Run by him, once, from the live checkout."""
    c = _client(directory)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state, got = secrets.token_urlsafe(16), {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if q.get("state") == state:
                got.update(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Signed in. You can close this tab and go back to the terminal.")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    try:
        redirect = f"http://127.0.0.1:{srv.server_port}"
        url = AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
            "access_type": "offline", "prompt": "consent", "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256"})
        print("Opening Google sign-in in your browser. If it does not open, paste this URL:\n" + url)
        webbrowser.open(url)
        while "code" not in got and "error" not in got:
            srv.handle_request()
    finally:
        srv.server_close()
    if "error" in got:
        raise SystemExit("Google sign-in failed: " + scrub(got["error"]))
    try:
        tok = _post(TOKEN_URL, {
            "code": got["code"], "client_id": c["client_id"], "client_secret": c.get("client_secret", ""),
            "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
    except urllib.error.HTTPError as err:
        raise SystemExit(f"Google sign-in failed: {_google_error(err)}") from None
    tok["expires_at"] = time.time() + float(tok.get("expires_in", 3600)) - 60
    os.makedirs(directory, exist_ok=True)
    _write_private(os.path.join(directory, TOKEN_FILE), tok)
    if live_checkout.is_live():
        print("Signed in as", GmailClient(directory).profile().email)
    else:
        print("Signed in; the token is saved in", directory)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    cmd = args[0] if args else ""
    if cmd == "auth":
        directory = gmail_dir()
        check_auth_location(directory)
        if not os.path.exists(os.path.join(directory, CLIENT_FILE)):
            raise SystemExit(f"Put your OAuth client file at:\n  {os.path.join(directory, CLIENT_FILE)}")
        authorize(directory)
        return 0
    if cmd == "whoami":
        try:
            print(GmailClient().profile().email)
        except GmailError as err:
            print(f"Gmail sign-in is not working ({err}); run: {SIGN_IN}")
            return 1
        return 0
    print(__doc__)
    return 0 if not cmd else 2


if __name__ == "__main__":
    raise SystemExit(main())
