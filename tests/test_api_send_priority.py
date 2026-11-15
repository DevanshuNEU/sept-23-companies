"""Send next and Send now in the API (#9 outreach fixes ruling, 2026-11-14, point 3; #281).

Approve takes his choice (`priority`: normal, next or now) in the same write that approves. An approved email's
choice changes only through `POST /api/messages/{id}/priority`. The Outbox row carries the choice, and a Send now row
says "Sends on the next run" or the reason it cannot go."""
import ast
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path

from outreach.api import openapi
from tests import apiseed as seed
from tests.test_api_approve import ApproveBase
from tests.test_api_outbox_writes import WritesBase

ROOT = Path(__file__).resolve().parent.parent


class ApproveWithPriorityTest(ApproveBase):
    def test_approve_defaults_to_normal(self):
        res = self.approve()
        self.assertEqual(res.status_code, 200, res.text)
        row = self.message()
        self.assertEqual((row["state"], row["priority"], row["urgent_at"]), ("approved", "normal", None))
        self.assertEqual(res.json()["item"]["priority"], "normal")

    def test_approve_send_next(self):
        res = self.post(f"/api/messages/{self.mid}/approve", {"revision": 1, "priority": "next"})
        self.assertEqual(res.status_code, 200, res.text)
        row = self.message()
        self.assertEqual((row["state"], row["priority"], row["urgent_at"]), ("approved", "next", None))
        self.assertEqual(res.json()["item"]["priority"], "next")
        [event] = self.events()
        self.assertIn('"priority": "next"', event["detail"])

    def test_approve_send_now_stamps_when_he_asked(self):
        res = self.post(f"/api/messages/{self.mid}/approve", {"revision": 1, "priority": "now"})
        self.assertEqual(res.status_code, 200, res.text)
        row = self.message()
        self.assertEqual((row["state"], row["priority"]), ("approved", "now"))
        self.assertEqual(row["urgent_at"], row["approved_at"])
        self.assertEqual(res.json()["item"]["priority"], "now")

    def test_a_priority_that_is_not_one_of_the_three_is_bad_request(self):
        res = self.post(f"/api/messages/{self.mid}/approve", {"revision": 1, "priority": "asap"})
        self.assertEqual(res.status_code, 400, res.text)
        self.assertEqual(self.message()["state"], "drafted")

    def test_a_blocked_approve_sets_no_priority(self):
        self.conn.execute("UPDATE message SET body = body || ' TODO' WHERE message_id = ?", (self.mid,))
        res = self.post(f"/api/messages/{self.mid}/approve", {"revision": 1, "priority": "now"})
        self.assertEqual(res.status_code, 422, res.text)
        row = self.message()
        self.assertEqual((row["state"], row["priority"], row["urgent_at"]), ("drafted", "normal", None))


class PriorityRouteTest(WritesBase):
    def approved(self) -> int:
        return seed.message(self.conn, self.touch, 0, "approved")

    def set(self, mid, priority, revision=1, key=None):
        return self.post(f"/api/messages/{mid}/priority", {"priority": priority, "revision": revision}, key=key)

    def test_send_now_on_an_approved_email(self):
        mid = self.approved()
        res = self.set(mid, "now", key="priority-key-1")
        self.assertEqual(res.status_code, 200, res.text)
        row = self.message(mid)
        self.assertEqual((row["state"], row["priority"], row["revision"]), ("approved", "now", 2))
        self.assertIsNotNone(row["urgent_at"])
        item = res.json()["item"]
        self.assertEqual(item["priority"], "now")
        [event] = self.conn.execute("SELECT * FROM event WHERE kind = 'priority_set'").fetchall()
        self.assertEqual((event["actor"], event["idempotency_key"]), ("devanshu", "priority-key-1:0"))
        self.assertIn('"from": "normal"', event["detail"])
        self.assertIn('"to": "now"', event["detail"])

    def test_back_to_normal_clears_the_urgent_time(self):
        mid = self.approved()
        self.assertEqual(self.set(mid, "now").status_code, 200)
        res = self.set(mid, "normal", revision=2)
        self.assertEqual(res.status_code, 200, res.text)
        row = self.message(mid)
        self.assertEqual((row["priority"], row["urgent_at"]), ("normal", None))

    def test_only_an_approved_email_takes_a_priority(self):
        mid = seed.message(self.conn, self.touch, 0)
        res = self.set(mid, "now")
        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(res.json()["code"], "not_allowed")
        self.assertEqual(self.message(mid)["priority"], "normal")

    def test_a_claimed_email_keeps_its_priority(self):
        mid = self.approved()
        self.conn.execute("UPDATE message SET state = 'sending', pending_rfc_id = '<p@x>', claimed_at = ? "
                          "WHERE message_id = ?", ("2026-11-09T12:00:00Z", mid))
        res = self.set(mid, "now")
        self.assertEqual(res.json()["code"], "not_allowed")

    def test_a_stale_revision_conflicts(self):
        mid = self.approved()
        self.assertEqual(self.set(mid, "next", revision=7).json()["code"], "conflict")
        self.assertEqual(self.message(mid)["priority"], "normal")

    def test_the_same_priority_again_is_not_allowed(self):
        mid = self.approved()
        self.assertEqual(self.set(mid, "normal").json()["code"], "not_allowed")

    def test_skip_drops_the_priority(self):
        mid = self.approved()
        self.set(mid, "now")
        res = self.post(f"/api/messages/{mid}/skip", {"revision": 2})
        self.assertEqual(res.status_code, 200, res.text)
        row = self.message(mid)
        self.assertEqual((row["state"], row["priority"], row["urgent_at"]), ("skipped", "normal", None))

    def test_unapprove_drops_the_priority(self):
        mid = self.approved()
        self.set(mid, "now")
        res = self.post(f"/api/messages/{mid}/unapprove", {"revision": 2})
        self.assertEqual(res.status_code, 200, res.text)
        row = self.message(mid)
        self.assertEqual((row["state"], row["priority"], row["urgent_at"]), ("drafted", "normal", None))

    def test_a_send_now_row_says_it_sends_on_the_next_run(self):
        mid = self.approved()
        res = self.set(mid, "now")
        schedule = res.json()["item"]["schedule"]
        self.assertEqual(schedule["reason"], "ready")
        self.assertEqual(schedule["text"], "Sends on the next run")
        self.assertEqual(res.json()["item"]["summary"], "Sends on the next run")

    def yesterday_now(self) -> int:
        """An approved email he marked Send now on an earlier New York day (NOW is 2026-11-10, a Tuesday)."""
        mid = self.approved()
        self.conn.execute("UPDATE message SET priority = 'now', urgent_at = '2026-11-09T18:00:00Z' "
                          "WHERE message_id = ?", (mid,))
        return mid

    def test_a_send_now_from_an_earlier_day_reads_as_send_next(self):
        mid = self.yesterday_now()
        res = self.get(f"/api/outbox/{self.touch}")
        self.assertEqual(res.status_code, 200, res.text)
        item = res.json()["item"]
        self.assertEqual(item["priority"], "next")
        self.assertNotEqual(item["schedule"]["text"], "Sends on the next run")
        self.assertEqual(self.message(mid)["priority"], "now")  # nothing is written by a read

    def test_send_now_again_on_a_send_now_from_an_earlier_day_restamps_it(self):
        mid = self.yesterday_now()
        res = self.set(mid, "now")
        self.assertEqual(res.status_code, 200, res.text)
        row = self.message(mid)
        self.assertEqual((row["priority"], row["revision"]), ("now", 2))
        self.assertGreater(row["urgent_at"], "2026-11-10")
        self.assertEqual(res.json()["item"]["priority"], "now")

    def test_a_send_now_that_cannot_go_says_why(self):
        mid = self.approved()
        self.conn.execute("UPDATE company SET paused_at = ? WHERE company_id = ?", ("2026-11-09T12:00:00Z", self.acme))
        res = self.set(mid, "now")
        schedule = res.json()["item"]["schedule"]
        self.assertEqual(schedule["reason"], "company_paused")
        self.assertIn("paused", schedule["text"])


class OnlyHisClicksSetPriorityTest(unittest.TestCase):
    def test_priority_is_written_only_by_approve_the_priority_route_and_the_reset(self):
        ops = sorted((path, method) for path, item in openapi.build_schema()["paths"].items() for method in item
                     if "priority" in path)
        self.assertEqual(ops, [("/api/messages/{message_id}/priority", "post")])
        sets_priority = re.compile(r"\bUPDATE\s+message\s+SET\b(?:(?!\bWHERE\b).)*?\bpriority\s*=", re.S | re.I)
        writers = []
        for path in sorted((ROOT / "outreach").rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and sets_priority.search(node.value):
                    writers.append(rel)
                # `bump(conn, "message", ..., {"priority": ...})`: a row write of message naming the column
                if isinstance(node, ast.Call) and any(isinstance(a, ast.Constant) and a.value == "message"
                                                      for a in node.args) and any(
                        isinstance(a, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == "priority"
                                                        for k in a.keys) for a in node.args):
                    writers.append(rel)
        # The API's approve and priority route set it; everything else only puts it back to normal when a message
        # leaves approved (back to review, skipped, stopped by a reply or a bounce, the importer's skip).
        self.assertIn("outreach/api/services/outbox.py", writers)
        for rel in sorted(set(writers) - {"outreach/api/services/outbox.py"}):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertEqual(set(re.findall(r"priority = (\S+?)[,\s]", text)), {"'normal'"}, rel)


if __name__ == "__main__":
    unittest.main()
