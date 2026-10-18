"""
Local backend for the dashboard's Email tab: http://127.0.0.1:3002 (this machine only).

  GET  /api/state                        contacts, today's count and cap, paused, signed in
  POST /api/approve   {id}               drafted|failed -> approved, followup_ready -> followup_approved
  POST /api/unapprove {id}               back to drafted / followup_ready (only before it is sent)
  POST /api/save      {id, which, subject, body}   edit the draft ("draft") or follow-up ("followup")
  POST /api/skip      {id}               never send this one
  POST /api/pause     {paused: bool}

Writes are accepted only from the dashboard's own origin (http://localhost:3001) with a JSON body, so no
other web page open in the browser can approve or edit anything. Approving never sends: sender.py does
that later, one email at a time, inside the sending rules.
"""
import http.server, json, os, sys, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store, gmail, sender

ORIGINS = {"http://localhost:3001", "http://127.0.0.1:3001"}
EDITABLE = {"draft": ("drafted", "approved", "failed"), "followup": ("followup_ready", "followup_approved")}

def state():
    db, cfg = store.load(), store.config()
    return {"contacts": db["contacts"], "today": sender.sent_today(db), "cap": sender.cap_today(db, cfg),
            "paused": cfg["paused"], "pausedWhy": cfg["pausedWhy"], "signedIn": gmail.authorized(),
            "window": cfg["window"]}

def act(path, body):
    with store.locked():
        if path == "/api/pause":
            cfg = store.config(); cfg["paused"] = bool(body.get("paused")); cfg["pausedWhy"] = "" if not cfg["paused"] else "paused by you"
            store.save_config(cfg); return {"ok": True}
        db = store.load()
        c = next((x for x in db["contacts"] if x["id"] == body.get("id")), None)
        if not c: return {"error": "no such contact"}
        s = c["status"]
        if path == "/api/approve":
            if s in ("drafted", "failed"): c["status"] = "approved"
            elif s == "followup_ready": c["status"] = "followup_approved"
            else: return {"error": f"cannot approve from {s}"}
            c["approvedAt"] = store.now(); store.log(c, "approved by Devanshu")
        elif path == "/api/unapprove":
            if s == "approved": c["status"] = "drafted"
            elif s == "followup_approved": c["status"] = "followup_ready"
            else: return {"error": f"cannot unapprove from {s}"}
            store.log(c, "approval withdrawn")
        elif path == "/api/save":
            which = body.get("which", "draft")
            if which not in EDITABLE or s not in EDITABLE[which]: return {"error": f"cannot edit the {which} now"}
            if not (body.get("subject") or "").strip() or not (body.get("body") or "").strip(): return {"error": "subject and body are required"}
            c[which] = {"subject": body["subject"].strip(), "body": body["body"].rstrip()}
            if s in ("approved", "followup_approved"):              # an edit after approval needs a fresh approval
                c["status"] = "drafted" if which == "draft" else "followup_ready"
            store.log(c, f"{which} edited")
        elif path == "/api/skip":
            if s in ("sent", "followup_sent", "replied", "bounced", "closed"): return {"error": "already sent"}
            if s in ("sending", "send_unknown"): return {"error": "checking whether it went out; try again in a few minutes"}
            c["status"] = "skipped"; store.log(c, "skipped")
        else: return {"error": "unknown action"}
        store.save(db)
        return {"ok": True, "status": c["status"]}

class H(http.server.BaseHTTPRequestHandler):
    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        origin = self.headers.get("Origin", "")
        if origin in ORIGINS: self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Content-Type", "application/json"); self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(data)
    def do_OPTIONS(self):
        self.send_response(204)
        if self.headers.get("Origin", "") in ORIGINS:
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
            self.send_header("Access-Control-Allow-Methods", "GET, POST"); self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
    def do_GET(self):
        if self.path == "/api/state": return self._send(200, state())
        self._send(404, {"error": "not found"})
    def do_POST(self):
        if self.headers.get("Origin", "") not in ORIGINS or "application/json" not in self.headers.get("Content-Type", ""):
            return self._send(403, {"error": "only the dashboard can make changes"})
        try: body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
        except Exception: return self._send(400, {"error": "bad JSON"})
        res = act(self.path, body)
        self._send(400 if "error" in res else 200, res)
    def log_message(self, *a): pass

if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", 3002), H).serve_forever()
