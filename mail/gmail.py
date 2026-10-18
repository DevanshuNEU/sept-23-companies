"""
Gmail access for the cold-email sender, using only the standard library.

Secrets live outside the project, in ~/Library/Application Support/outreach-mail/:
  client_secret.json   the OAuth "Desktop app" client you download from Google Cloud Console
  token.json           written by `python3 mail/gmail.py auth` (chmod 600); holds the refresh token

Scopes: gmail.send (send approved emails) and gmail.metadata (read headers of our own threads, to
notice replies and bounces). Neither scope can read message bodies or delete anything.

  python3 mail/gmail.py auth     sign in once in the browser (you type your password into Google, not here)
  python3 mail/gmail.py whoami   print the signed-in address
"""
import base64, hashlib, http.server, json, os, secrets, sys, time, urllib.parse, urllib.request, urllib.error, webbrowser
from email.message import EmailMessage
from email.utils import make_msgid, formataddr

DIR = os.path.expanduser("~/Library/Application Support/outreach-mail")
CLIENT, TOKEN = os.path.join(DIR, "client_secret.json"), os.path.join(DIR, "token.json")
SCOPES = "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.metadata"
API = "https://gmail.googleapis.com/gmail/v1/users/me"

def _client():
    c = json.load(open(CLIENT))
    return c.get("installed") or c.get("web")

def authorized():
    return os.path.exists(TOKEN)

def authorize():
    """Browser sign-in with PKCE and a loopback redirect. Run by you, once."""
    c = _client()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state, got = secrets.token_urlsafe(16), {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if q.get("state") == state: got.update(q)
            self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers()
            self.wfile.write(b"Signed in. You can close this tab and go back to the terminal.")
        def log_message(self, *a): pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    print("Opening Google sign-in in your browser. If it does not open, paste this URL:\n" + url)
    webbrowser.open(url)
    while "code" not in got and "error" not in got: srv.handle_request()
    if "error" in got: raise SystemExit("Google sign-in failed: " + got["error"])
    tok = _post("https://oauth2.googleapis.com/token", {
        "code": got["code"], "client_id": c["client_id"], "client_secret": c.get("client_secret", ""),
        "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    os.makedirs(DIR, exist_ok=True)
    with open(TOKEN, "w") as f: json.dump(tok, f)
    os.chmod(TOKEN, 0o600)
    print("Signed in as", whoami())

def _post(url, form):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=30) as r: return json.loads(r.read())

def _token():
    tok = json.load(open(TOKEN))
    if time.time() >= tok.get("expires_at", 0):
        c = _client()
        new = _post("https://oauth2.googleapis.com/token", {
            "client_id": c["client_id"], "client_secret": c.get("client_secret", ""),
            "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
        tok.update(new); tok["expires_at"] = time.time() + new.get("expires_in", 3600) - 60
        with open(TOKEN, "w") as f: json.dump(tok, f)
        os.chmod(TOKEN, 0o600)
    return tok["access_token"]

def api(method, path, body=None, query=None):
    url = API + path + ("?" + urllib.parse.urlencode(query, doseq=True) if query else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": "Bearer " + _token(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r: return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Gmail API {e.code}: {e.read()[:300].decode('utf-8', 'replace')}")

def whoami():
    return api("GET", "/profile")["emailAddress"]

def new_message_id(from_addr):
    return make_msgid(domain=from_addr.split("@")[-1])

def send(to, subject, body, from_name, from_addr, thread_id=None, in_reply_to=None, message_id=None):
    """Send one plain-text email. Returns ids needed to track the thread and reply in it later.
    The caller passes the Message-ID it saved before calling, so an unclear outcome can be checked in Sent."""
    m = EmailMessage()
    m["To"], m["From"], m["Subject"] = to, formataddr((from_name, from_addr)), subject
    m["Message-ID"] = message_id or new_message_id(from_addr)
    if in_reply_to:
        m["In-Reply-To"], m["References"] = in_reply_to, in_reply_to
    m.set_content(body)
    raw = base64.urlsafe_b64encode(m.as_bytes()).decode()
    res = api("POST", "/messages/send", {"raw": raw, **({"threadId": thread_id} if thread_id else {})})
    return {"messageId": res["id"], "threadId": res["threadId"], "rfcMessageId": m["Message-ID"]}

AUTO_HEADERS = ["Auto-Submitted", "X-Autoreply", "X-Autorespond", "Precedence", "X-Auto-Response-Suppress"]

def _headers(msg):
    return {x["name"].lower(): x["value"] for x in (msg or {}).get("payload", {}).get("headers", [])}

def thread(thread_id):
    """Every message in a thread: [{from, subject, date, internalDate, labels, headers}].
    headers holds the auto-reply markers (lower-cased names) so the sender can tell an out-of-office from a person."""
    t = api("GET", f"/threads/{thread_id}",
            query={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"] + AUTO_HEADERS})
    out = []
    for msg in t.get("messages", []):
        h = _headers(msg)
        out.append({"from": h.get("from", ""), "subject": h.get("subject", ""), "date": h.get("date", ""),
                    "internalDate": int(msg.get("internalDate", 0)), "labels": msg.get("labelIds", []),
                    "headers": {k.lower(): h[k.lower()] for k in AUTO_HEADERS if k.lower() in h}})
    return out

def find_sent(rfc_id, to, subject, look_back=50):
    """Look in Sent for a message we may or may not have sent. Matches on our Message-ID, else on the same
    recipient and subject. Returns {messageId, threadId, rfcMessageId} or None. (The metadata scope cannot search,
    so this pages the newest Sent messages.)"""
    listing = api("GET", "/messages", query={"labelIds": "SENT", "maxResults": look_back}) or {}
    fallback = None
    for ref in listing.get("messages", []):
        h = _headers(api("GET", f"/messages/{ref['id']}",
                         query={"format": "metadata", "metadataHeaders": ["Message-ID", "To", "Subject"]}))
        hit = {"messageId": ref["id"], "threadId": ref["threadId"], "rfcMessageId": h.get("message-id", "")}
        if h.get("message-id") == rfc_id: return hit
        if not fallback and to.lower() in h.get("to", "").lower() and h.get("subject") == subject: fallback = hit
    return fallback

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "auth":
        if not os.path.exists(CLIENT): raise SystemExit(f"Put your OAuth client file at:\n  {CLIENT}")
        authorize()
    elif cmd == "whoami": print(whoami())
    else: print(__doc__)
