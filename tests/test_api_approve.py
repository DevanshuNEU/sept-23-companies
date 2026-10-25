"""Task 19: the server-side approve (Review Focus 3).

Approve decides on current data: it looks up the mail server first (outside any transaction), then inside one
`BEGIN IMMEDIATE` checks the revision and the state and runs `outreach.drafts.checks.run_checks`. Any block refuses
with 422 `blocked` and the checks, and those fresh checks are saved to the message in a second short transaction
(revision unchanged). Warns never block. Only this route can make a message `approved`.
"""
import ast
import dataclasses
import json
import re
import sqlite3
import unittest
from pathlib import Path

from outreach.api import openapi
from tests import apiseed as seed
from tests.apitest import NOW, ApiCase

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_BANNED = ROOT / "tests" / "fixtures" / "banned_phrases.txt"
PORTFOLIO = "https://www.devanshuchicholikar.com"
PROJECT = "https://github.com/devanshu/runner"
SUBJECT = "Your queue rewrite and the new grad backend role"
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


class Resolver:
    """A fake mail-server lookup: records each domain and answers with `records` (None is no answer)."""

    def __init__(self, records, check=None):
        self.records, self.check, self.asked = records, check, []

    def __call__(self, domain):
        self.asked.append(domain)
        if self.check is not None:
            self.check()
        return self.records


class ApproveBase(ApiCase):
    def setUp(self):
        super().setUp()
        self.settings = dataclasses.replace(self.settings, banned_phrases=FIXTURE_BANNED)
        self.mx_resolvers.append(Resolver(["10 mx.acme.example."]))
        self.build()
        self.acme = seed.company(self.conn, "Acme", status="working")
        self.pat = seed.person(self.conn, self.acme, "Pat Lee", email="pat@acme.example")
        self.touch = seed.touch(self.conn, self.pat)
        self.mid = seed.message(self.conn, self.touch, 0, subject=SUBJECT, body=BODY)
        self.conn.execute("UPDATE message SET hook = 'His post on the queue rewrite (2026-11-02)' "
                          "WHERE message_id = ?", (self.mid,))

    def message(self, mid=None):
        return self.conn.execute("SELECT * FROM message WHERE message_id = ?", (mid or self.mid,)).fetchone()

    def approve(self, mid=None, revision=1, key=None):
        return self.post(f"/api/messages/{mid or self.mid}/approve", {"revision": revision}, key=key)

    def events(self, kind="approved"):
        return self.conn.execute("SELECT * FROM event WHERE kind = ?", (kind,)).fetchall()

    def colleague(self, body=BODY, name="Sam Wu") -> int:
        pid = seed.person(self.conn, self.acme, name)
        return seed.message(self.conn, seed.touch(self.conn, pid), 0, subject="Another subject", body=body)


class ApproveTest(ApproveBase):
    def test_a_clean_draft_approves(self):
        res = self.approve(key="approve-key-1")
        self.assertEqual(res.status_code, 200, res.text)
        detail = res.json()
        self.assertEqual((detail["item"]["state"], detail["item"]["group"], detail["item"]["revision"]),
                         ("approved", "approved", 2))
        row = self.message()
        self.assertEqual((row["state"], row["approved_at"], row["revision"]), ("approved", NOW, 2))
        self.assertFalse([c for c in json.loads(row["checks"]) if c["severity"] == "block"])
        [event] = self.events()
        self.assertEqual((event["entity"], event["entity_id"], event["actor"], event["idempotency_key"]),
                         ("message", self.mid, "devanshu", "approve-key-1:0"))

    def test_approve_refuses_each_block_with_its_code_and_a_plain_reason(self):
        def mail_none():
            self.mx_resolvers[:] = [Resolver([])]

        def banned_missing():
            self.settings = dataclasses.replace(self.settings, banned_phrases=ROOT / "tests" / "no-such-list.txt")
            self.build()

        def body(text):
            return lambda: self.conn.execute("UPDATE message SET body = ? WHERE message_id = ?", (text, self.mid))

        def followup():
            self.conn.execute("UPDATE touch SET first_sent_at = ?, first_rfc_id = '<f@x>', gmail_thread_id = 't1' "
                              "WHERE touch_id = ?", (seed.T0, self.touch))
            self.conn.execute("UPDATE message SET state = 'skipped' WHERE message_id = ?", (self.mid,))
            self.mid = seed.message(self.conn, self.touch, 1, subject="A new subject", body="Hi Pat, one more note.")

        def no_go():
            self.conn.execute("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'other', "
                              "'devanshu', ?)", (self.pat, seed.T0))

        def said_no():  # said no on LinkedIn: it stops email too
            self.conn.execute("INSERT INTO outreach_touch(person_id, company_id, channel, sent_at, replied_at, "
                              "reply_kind, context, idempotency_key, created_at) VALUES (?, ?, 'linkedin_message', "
                              "?, ?, 'said_no', ?, 'li-1', ?)",
                              (self.pat, self.acme, seed.T0, seed.T0, json.dumps(CONTEXT), seed.T0))

        def closed():
            self.conn.execute("UPDATE company SET status = 'closed', close_reason = 'no_sponsorship' "
                              "WHERE company_id = ?", (self.acme,))

        cases = {
            "no_mail_server": mail_none,
            "banned_list_missing": banned_missing,
            "placeholder": body(BODY.replace("Thanks,", "TODO Thanks,")),
            "too_many_links": body(BODY.replace(f"at {PROJECT}.", f"at {PROJECT} and {PROJECT}/docs.")),
            "link_shortener": body(BODY.replace(PROJECT, "https://bit.ly/abc")),
            "followup_subject": followup,
            "near_identical": lambda: self.colleague(BODY.replace("Hi Pat,", "Hi Sam,")),
            "no_go_person": no_go,
            "said_no": said_no,
            "closed_no_sponsorship": closed,
        }
        from outreach.drafts.checks import BLOCK_CODES
        self.assertEqual(set(cases), set(BLOCK_CODES))
        for code, make in cases.items():
            with self.subTest(code=code):
                self.setUp()
                make()
                before = self.message()
                res = self.approve()
                self.assertEqual(res.status_code, 422, res.text)
                body_ = res.json()
                self.assertEqual(body_["code"], "blocked")
                blocks = [c for c in body_["checks"] if c["severity"] == "block"]
                self.assertIn(code, [c["code"] for c in blocks])
                self.assertTrue(all(c["detail"] for c in blocks))
                self.assertIn(blocks[0]["title"], body_["message"])
                row = self.message()
                self.assertEqual((row["state"], row["revision"]), (before["state"], before["revision"]))
                self.assertEqual(self.events(), [])

    def test_approve_blocks_on_a_no_go_added_after_the_draft_was_saved(self):
        self.conn.execute("UPDATE message SET checks = ? WHERE message_id = ?",
                          (json.dumps([{"code": "no_go_person", "severity": "pass", "title": "Not on the no-go list",
                                        "detail": "", "fix": None, "spans": []}]), self.mid))
        self.conn.execute("INSERT INTO no_go(company_id, reason, source, created_at) VALUES (?, 'other', 'devanshu', ?)",
                          (self.acme, seed.T0))
        res = self.approve()
        self.assertEqual(res.status_code, 422, res.text)
        self.assertIn("no_go_person", [c["code"] for c in res.json()["checks"] if c["severity"] == "block"])

    def test_refused_approve_saves_the_fresh_checks_to_the_message(self):
        self.colleague(BODY.replace("Hi Pat,", "Hi Sam,"))
        self.assertEqual(self.approve().status_code, 422)
        row = self.message()
        self.assertEqual((row["state"], row["revision"]), ("drafted", 1))
        saved = [c["code"] for c in json.loads(row["checks"]) if c["severity"] == "block"]
        self.assertEqual(saved, ["near_identical"])
        detail = self.get(f"/api/outbox/{self.touch}").json()
        self.assertEqual(detail["item"]["blockCount"], 1)
        self.assertEqual([c["code"] for c in detail["checks"] if c["severity"] == "block"], ["near_identical"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM api_request").fetchone()[0], 0)

    def test_banned_phrase_and_dash_approve_with_the_warnings_listed(self):
        self.conn.execute("UPDATE message SET body = ? WHERE message_id = ?",
                          (BODY.replace("I build", "I'm passionate about").replace("role, or", "role \u2014 or"),
                           self.mid))
        res = self.approve()
        self.assertEqual(res.status_code, 200, res.text)
        warns = [c["code"] for c in res.json()["checks"] if c["severity"] == "warn"]
        self.assertIn("banned_phrase", warns)
        self.assertIn("dash", warns)
        self.assertEqual(self.message()["state"], "approved")

    def test_approve_stale_revision_conflicts_and_nothing_changes(self):
        res = self.approve(revision=5)
        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(res.json()["code"], "conflict")
        row = self.message()
        self.assertEqual((row["state"], row["revision"], row["checks"]), ("drafted", 1, "[]"))
        self.assertEqual(self.events(), [])

    def test_approve_a_failed_message_is_not_allowed(self):
        path = ("approved", "sending", "send_unknown", "failed")  # the only way the store reaches each
        # Only one message is ever `sending`, so the one left there is walked last.
        for state in ("approved", "send_unknown", "failed", "sending"):
            n = path.index(state)
            with self.subTest(state=state):
                pid = seed.person(self.conn, self.acme, f"Walk {state}")
                mid = seed.message(self.conn, seed.touch(self.conn, pid), 0, subject=f"S {state}", body=BODY)
                self.conn.execute("UPDATE message SET state = 'approved', approved_at = ? WHERE message_id = ?",
                                  (NOW, mid))
                for step in path[1:n + 1]:
                    claimed = step in ("sending", "send_unknown")
                    self.conn.execute("UPDATE message SET state = ?, pending_rfc_id = ?, claimed_at = ? "
                                      "WHERE message_id = ?",
                                      (step, f"<p{mid}@x>" if claimed else None, NOW if claimed else None, mid))
                self.assertEqual(self.message(mid)["state"], state)
                res = self.approve(mid)
                self.assertEqual(res.status_code, 409, res.text)
                self.assertEqual(res.json()["code"], "not_allowed")
                self.assertEqual(res.json()["message"], "Only a draft can be approved.")
                row = self.message(mid)
                self.assertEqual((row["state"], row["revision"]), (state, 1))
        self.assertEqual(self.events(), [])

    def test_approve_a_sent_message_is_not_allowed(self):
        tid = seed.touch(self.conn, seed.person(self.conn, self.acme, "Sent Sue"))
        sent = seed.send_first(self.conn, tid)
        res = self.approve(sent)
        self.assertEqual(res.json()["code"], "not_allowed")
        self.assertEqual(self.message(sent)["state"], "sent")

    def test_approve_twice_with_one_key_approves_once_and_replays(self):
        first = self.approve(key="same-key-1")
        again = self.approve(key="same-key-1")
        self.assertEqual((first.status_code, again.status_code), (200, 200))
        self.assertEqual(again.headers.get("Idempotent-Replay"), "true")
        self.assertEqual(again.json(), first.json())
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.message()["revision"], 2)

    def test_approve_twice_with_two_keys_second_is_not_allowed_and_writes_no_event(self):
        self.assertEqual(self.approve(key="first-key-1").status_code, 200)
        res = self.approve(revision=2, key="second-key-1")
        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(res.json()["code"], "not_allowed")
        self.assertEqual(len(self.events()), 1)

    def test_unknown_mail_server_warns_and_none_blocks(self):
        self.mx_resolvers[:] = [Resolver(None)]  # no answer anywhere
        res = self.approve()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIn("mail_server_unknown", [c["code"] for c in res.json()["checks"] if c["severity"] == "warn"])
        self.assertIsNone(self.conn.execute("SELECT 1 FROM mx_check").fetchone())  # unknown is never stored

        other = seed.company(self.conn, "Gone", status="working")
        pid = seed.person(self.conn, other, "Nora None", email="nora@gone.example")
        mid = seed.message(self.conn, seed.touch(self.conn, pid), 0, subject="Hello Nora", body=BODY)
        self.mx_resolvers[:] = [Resolver([])]
        res = self.approve(mid)
        self.assertEqual(res.status_code, 422, res.text)
        self.assertIn("no_mail_server", [c["code"] for c in res.json()["checks"] if c["severity"] == "block"])
        self.assertEqual(self.conn.execute("SELECT status FROM mx_check WHERE domain = 'gone.example'").fetchone()[0],
                         "none")

    def test_approve_runs_no_network_inside_the_transaction(self):
        def no_write_lock():
            # Another connection can take the write lock at once, so nobody holds it during the lookup.
            probe = sqlite3.connect(self.settings.db_path, timeout=0)
            try:
                probe.execute("BEGIN IMMEDIATE")
                probe.execute("ROLLBACK")
            finally:
                probe.close()

        resolver = Resolver(["10 mx.acme.example."], check=no_write_lock)
        self.mx_resolvers[:] = [resolver]
        res = self.approve()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(resolver.asked, ["acme.example"])

    def test_unknown_message_is_404(self):
        self.assertEqual(self.approve(999).status_code, 404)


CONTEXT = {"source": "own", "lane": "own", "roleKind": "engineer", "roleAgeDays": 0, "rankerVersion": "",
           "signals": {}, "roleNamed": False}


class OnlyApproveApprovesTest(unittest.TestCase):
    def test_no_route_other_than_approve_sets_state_approved(self):
        ops = [(path, method) for path, item in openapi.build_schema()["paths"].items() for method in item
               if "approve" in path]
        self.assertEqual(sorted(ops), [("/api/messages/{message_id}/approve", "post"),
                                       ("/api/messages/{message_id}/unapprove", "post")])
        callers, setters = [], []
        sets_approved = re.compile(r"\bSET\b(?:(?!\bWHERE\b).)*?\bstate\s*=\s*'approved'", re.S | re.I)
        for path in sorted((ROOT / "outreach").rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith("outreach/sending/"):
                continue  # the sender's own failed-to-approved retry (#14), outside the API
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                    if name == "approve_message":
                        callers.append(rel)
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and sets_approved.search(node.value):
                    setters.append(rel)
                if isinstance(node, ast.Dict) and any(
                        isinstance(k, ast.Constant) and k.value == "state" and isinstance(v, ast.Constant)
                        and v.value == "approved" for k, v in zip(node.keys, node.values)):
                    setters.append(rel)
        self.assertEqual(callers, ["outreach/api/routers/outbox.py"])
        self.assertEqual(sorted(set(setters)), ["outreach/api/services/outbox.py"])


if __name__ == "__main__":
    unittest.main()
