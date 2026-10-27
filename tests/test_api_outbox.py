"""Task 18: the Outbox reads.

One row per email thread, grouped by the rules of the web fixture's `deriveGroup` (closed and bounced threads
first, then a reply, then a paused company), plus one rule the fixture gets by closing touches: an unsent email at a
closed or removed company is in Closed. An approved row's schedule is the sender's own verdict
(`outreach.sending.status.queue_schedule`), never a second reading of the rules.
"""
import unittest
from typing import get_args

from outreach.api.models.outbox import ScheduleReason
from outreach.sending.queue import REASONS
from outreach.store.writes import append_event
from tests import apiseed as seed
from tests.apitest import NOW, ApiCase

GROUPS = ("replied", "review", "approved", "sending", "checking", "sent", "paused", "closed")


class OutboxBase(ApiCase):
    def outbox(self) -> dict:
        res = self.get("/api/outbox")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def detail(self, touch_id: int) -> dict:
        res = self.get(f"/api/outbox/{touch_id}")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def prove(self):
        append_event(self.conn, entity="message", entity_id=1, kind="marker_verified", actor="sender",
                     at="2026-11-01T12:00:00Z", idempotency_key="proof")

    def thread(self, company: int, name: str, *, tz: str = "America/New_York", **touch) -> int:
        pid = seed.person(self.conn, company, name)
        self.conn.execute("UPDATE person SET tz = ? WHERE person_id = ?", (tz, pid))
        return seed.touch(self.conn, pid, **touch)

    def claimed(self, touch_id: int, state: str) -> int:
        """A claimed message: `sending`, or `send_unknown` through `sending` (the store allows no shortcut, and only
        one message is `sending` at a time)."""
        mid = seed.message(self.conn, touch_id, 0, "approved")
        self.conn.execute("UPDATE message SET state = 'sending', pending_rfc_id = ?, claimed_at = ? "
                          "WHERE message_id = ?", (f"<pending{mid}@x>", "2026-11-10T15:00:00Z", mid))
        if state == "send_unknown":
            self.conn.execute("UPDATE message SET state = 'send_unknown' WHERE message_id = ?", (mid,))
        return mid

    def by_touch(self, view: dict) -> dict[int, dict]:
        return {i["touchId"]: i for i in view["items"]}


class ViewTest(OutboxBase):
    def test_schedule_reasons_are_the_queues_own(self):
        self.assertEqual(get_args(ScheduleReason), REASONS)

    def test_empty_outbox_is_a_valid_view_with_zero_counts(self):
        view = self.outbox()
        self.assertEqual(view["asOf"], NOW)
        self.assertEqual(view["items"], [])
        self.assertEqual(view["counts"], {g: 0 for g in GROUPS})
        self.assertEqual(view["closedBreakdown"], {"bounced": 0, "skipped": 0, "companyClosed": 0})
        self.assertEqual(view["sending"]["state"], "running")

    def test_group_per_state_and_touch_state(self):
        working = seed.company(self.conn, "Working", status="working")
        paused = seed.company(self.conn, "Paused", status="working", paused_at=NOW)
        closed = seed.company(self.conn, "Closed", status="closed", close_reason="not_now")
        cases = {}

        t = self.thread(working, "Rae Replied", state="replied")
        seed.send_first(self.conn, t)
        cases[t] = "replied"
        t = self.thread(paused, "Pam Replied Paused", state="replied")  # a reply wins over the pause
        seed.send_first(self.conn, t)
        cases[t] = "replied"
        t = self.thread(working, "Dee Drafted")
        seed.message(self.conn, t, 0, "drafted")
        cases[t] = "review"
        t = self.thread(working, "Abe Approved")
        seed.message(self.conn, t, 0, "approved")
        cases[t] = "approved"
        t = self.thread(working, "Una Unknown")
        self.claimed(t, "send_unknown")
        cases[t] = "checking"
        t = self.thread(working, "Sid Sending")
        self.claimed(t, "sending")
        cases[t] = "sending"
        t = self.thread(working, "Sam Sent")
        seed.send_first(self.conn, t)
        cases[t] = "sent"
        t = self.thread(paused, "Pat Paused")
        seed.message(self.conn, t, 0, "approved")
        cases[t] = "paused"
        t = self.thread(working, "Bo Bounced", state="bounced")
        seed.send_first(self.conn, t)
        cases[t] = "closed"
        t = self.thread(working, "Cy Closed", state="closed")
        seed.send_first(self.conn, t)
        cases[t] = "closed"
        t = self.thread(working, "Ski Skipped")
        m = seed.message(self.conn, t, 0, "drafted")
        self.conn.execute("UPDATE message SET state = 'skipped' WHERE message_id = ?", (m,))
        cases[t] = "closed"
        t = self.thread(closed, "Coco Closed Co")
        seed.message(self.conn, t, 0, "drafted")
        cases[t] = "closed"

        view = self.outbox()
        got = {i["touchId"]: i["group"] for i in view["items"]}
        self.assertEqual(got, cases)
        self.assertEqual(view["counts"], {"replied": 2, "review": 1, "approved": 1, "sending": 1, "checking": 1,
                                          "sent": 1, "paused": 1, "closed": 4})
        self.assertEqual(view["closedBreakdown"], {"bounced": 1, "skipped": 1, "companyClosed": 1})
        self.assertEqual(set(GROUPS), {i["group"] for i in view["items"]})

    def test_the_row_shows_the_newest_message_and_the_next_follow_up(self):
        cid = seed.company(self.conn, "Acme", status="working")
        t = self.thread(cid, "Nia Next")
        seed.send_first(self.conn, t)
        self.conn.execute("INSERT INTO message(touch_id, seq, state, due_on, saved_by, created_at, updated_at) "
                          "VALUES (?, 1, 'planned', '2026-11-16', 'sender', ?, ?)", (t, seed.T0, seed.T0))
        [item] = self.outbox()["items"]
        self.assertEqual((item["seq"], item["state"], item["group"]), (0, "sent", "sent"))
        self.assertEqual(item["nextDue"], {"seq": 1, "on": "2026-11-16"})

    def test_counts_and_worst_come_from_the_stored_checks(self):
        cid = seed.company(self.conn, "Acme", status="working")
        t = self.thread(cid, "Kim Checks")
        m = seed.message(self.conn, t, 0, "drafted")
        checks = ('[{"code":"near_identical","severity":"block","title":"Near-identical","detail":"","fix":null,'
                  '"spans":[]},{"code":"dash","severity":"warn","title":"Dash","detail":"","fix":null,"spans":[]},'
                  '{"code":"recruiter","severity":"warn","title":"Recruiter","detail":"","fix":null,"spans":[]}]')
        self.conn.execute("UPDATE message SET checks = ? WHERE message_id = ?", (checks, m))
        [item] = self.outbox()["items"]
        self.assertEqual((item["worst"], item["blockCount"], item["warnCount"]), ("block", 1, 2))
        self.assertEqual(item["summary"], "Near-identical")

    def test_items_sort_replies_first_then_drafts_newest_first_then_the_approved_queue_by_send_time(self):
        self.prove()
        cids = [seed.company(self.conn, f"Co {i}", status="working") for i in range(6)]
        old_reply = self.thread(cids[0], "Old Reply", state="replied", replied_at="2026-11-08T10:00:00Z")
        new_reply = self.thread(cids[1], "New Reply", state="replied", replied_at="2026-11-09T10:00:00Z")
        for t in (old_reply, new_reply):
            seed.send_first(self.conn, t)
        old_draft = self.thread(cids[2], "Old Draft")
        self.conn.execute("UPDATE message SET updated_at = ? WHERE message_id = ?",
                          ("2026-11-09T09:00:00Z", seed.message(self.conn, old_draft, 0, "drafted")))
        new_draft = self.thread(cids[3], "New Draft")
        self.conn.execute("UPDATE message SET updated_at = ? WHERE message_id = ?",
                          ("2026-11-10T09:00:00Z", seed.message(self.conn, new_draft, 0, "drafted")))
        # Approved first but in Tokyo, where it is past midnight: it goes after the one that is ready now.
        later = self.thread(cids[4], "Tomo Tokyo", tz="Asia/Tokyo")
        self.conn.execute("UPDATE message SET approved_at = '2026-11-01T08:00:00Z' WHERE message_id = ?",
                          (seed.message(self.conn, later, 0, "approved"),))
        now = self.thread(cids[5], "Nell Now")
        self.conn.execute("UPDATE message SET approved_at = '2026-11-01T09:00:00Z' WHERE message_id = ?",
                          (seed.message(self.conn, now, 0, "approved"),))

        items = sorted(self.outbox()["items"], key=lambda i: i["sortKey"])
        self.assertEqual([i["touchId"] for i in items], [new_reply, old_reply, new_draft, old_draft, now, later])
        ready, waiting = items[4]["schedule"], items[5]["schedule"]
        self.assertEqual((ready["reason"], ready["earliestAt"]), ("ready", NOW))
        self.assertEqual(waiting["reason"], "window")
        self.assertEqual(waiting["tz"], "Asia/Tokyo")
        self.assertTrue(waiting["earliestAt"] > NOW)
        self.assertEqual((ready["companySentToday"], ready["companySlotsLeft"]), (0, 3))

    def test_an_approved_row_waits_with_the_senders_own_reason(self):
        cid = seed.company(self.conn, "Acme", status="working")
        first, second = self.thread(cid, "Una Unproven"), self.thread(cid, "Otto Other")
        seed.message(self.conn, first, 0, "approved")
        seed.message(self.conn, second, 0, "approved")
        rows = self.by_touch(self.outbox())
        # No marker proof yet: the first email goes alone, and the other waits for its check.
        self.assertEqual(rows[first]["schedule"]["reason"], "ready")
        waiting = rows[second]["schedule"]
        self.assertEqual(waiting["reason"], "message_id_unproven")
        self.assertIsNone(waiting["earliestAt"])
        self.assertEqual(waiting["text"], "The first email goes out alone, then Gmail is checked. The rest follow.")

    def test_a_row_waiting_on_the_first_emails_check_says_so(self):
        cid = seed.company(self.conn, "Acme", status="working")
        sent = self.claimed(self.thread(cid, "Fay First"), "sending")
        append_event(self.conn, entity="message", entity_id=sent, kind="claimed", actor="sender",
                     at="2026-11-10T15:00:00Z", idempotency_key="claimed:first",
                     detail={"pending_rfc_id": "<first@x>", "marker": "<first@x>"})
        self.conn.execute("UPDATE message SET state = 'sent', sent_at = '2026-11-10T15:00:00Z', "
                          "rfc_message_id = '<pending1@x>', gmail_message_id = 'g1', pending_rfc_id = NULL, "
                          "claimed_at = NULL WHERE message_id = ?", (sent,))
        t = self.thread(cid, "Nell Next")
        seed.message(self.conn, t, 0, "approved")
        schedule = self.by_touch(self.outbox())[t]["schedule"]
        self.assertEqual((schedule["reason"], schedule["text"]),
                         ("message_id_unproven", "First email sent, checking it in Sent. The rest follow."))

    def test_a_drafted_row_has_no_schedule(self):
        cid = seed.company(self.conn, "Acme", status="working")
        seed.message(self.conn, self.thread(cid, "Dee Draft"), 0, "drafted")
        [item] = self.outbox()["items"]
        self.assertIsNone(item["schedule"])

    def test_a_merged_duplicates_people_show_under_the_survivor(self):
        keep = seed.company(self.conn, "Keep", status="working", paused_at=NOW)
        lose = seed.company(self.conn, "Lose", status="removed", merged_into=keep)
        t = self.thread(lose, "Mo Merged")
        seed.message(self.conn, t, 0, "approved")
        [item] = self.outbox()["items"]
        self.assertEqual((item["company"]["id"], item["company"]["name"], item["group"]), (keep, "Keep", "paused"))


class DetailTest(OutboxBase):
    def test_detail_thread_interleaves_replies_in_time_order(self):
        cid = seed.company(self.conn, "Acme", status="working")
        t = self.thread(cid, "Tia Thread", state="replied", replied_at="2026-11-09T10:00:00Z",
                        reply_kind="interested")
        seed.send_first(self.conn, t, at="2026-11-02T15:00:00Z")
        seed.inbound(self.conn, t, "2026-11-03T15:00:00Z", body="Out of office")
        fu = seed.message(self.conn, t, 1, "sent", subject="Re: Hello", body="Following up", sent_at="2026-11-06T15:00:00Z")
        seed.inbound(self.conn, t, "2026-11-09T10:00:00Z", body="Happy to chat")
        d = self.detail(t)
        self.assertEqual([(m["direction"], m["at"]) for m in d["thread"]],
                         [("out", "2026-11-02T15:00:00Z"), ("in", "2026-11-03T15:00:00Z"),
                          ("out", "2026-11-06T15:00:00Z"), ("in", "2026-11-09T10:00:00Z")])
        self.assertEqual(d["item"]["messageId"], fu)
        self.assertEqual(d["reply"]["text"], "Happy to chat")
        self.assertEqual(d["reply"]["kind"], "interested")
        self.assertEqual(d["firstSentAt"], "2026-11-02T15:00:00Z")
        thread_id = self.conn.execute("SELECT gmail_thread_id FROM touch WHERE touch_id = ?", (t,)).fetchone()[0]
        self.assertEqual(d["gmailThreadUrl"], f"https://mail.google.com/mail/u/0/#all/{thread_id}")
        self.assertEqual(d["followups"], [{"seq": 1, "dueOn": "2026-11-12", "sentAt": "2026-11-06T15:00:00Z",
                                           "state": "sent"}])

    def test_detail_hook_date_is_parsed_from_the_hook_text(self):
        cid = seed.company(self.conn, "Acme", status="working")
        for hook, want in (("Her post on durable queues (2026-11-01)", ("Her post on durable queues", "2026-11-01")),
                           ("His talk on retries", ("His talk on retries", None)),
                           ("A post (2026-13-45)", ("A post (2026-13-45)", None)),
                           ("", ("", None))):
            with self.subTest(hook=hook):
                t = self.thread(cid, f"Hook {len(hook)}")
                m = seed.message(self.conn, t, 0, "drafted")
                self.conn.execute("UPDATE message SET hook = ?, project = 'Saar' WHERE message_id = ?", (hook, m))
                d = self.detail(t)
                self.assertEqual((d["hook"]["text"], d["hook"]["on"]), want)
                self.assertEqual((d["project"], d["body"], d["savedBy"]), ("Saar", "Hi there", "claude"))

    def test_a_held_company_lists_the_emails_it_holds_on_the_holding_thread(self):
        cid = seed.company(self.conn, "Acme", status="working")
        holder = self.thread(cid, "Hana Holder", state="replied")
        seed.send_first(self.conn, holder)
        self.conn.execute("UPDATE company SET held_by_touch = ?, held_at = ? WHERE company_id = ?", (holder, NOW, cid))
        other = self.thread(cid, "Ole Other")
        seed.message(self.conn, other, 0, "approved")
        d = self.detail(holder)
        self.assertEqual(d["held"], [{"touchId": other, "personName": "Ole Other", "state": "approved", "seq": 0,
                                      "dueOn": None}])
        self.assertEqual(self.detail(other)["held"], [])

    def test_a_bounce_shows_its_reason(self):
        cid = seed.company(self.conn, "Acme", status="working")
        t = self.thread(cid, "Bea Bounce", state="bounced")
        seed.send_first(self.conn, t)
        seed.inbound(self.conn, t, "2026-11-06T10:00:00Z", kind="bounce",
                     body="\n550 5.1.1 The email account that you tried to reach does not exist.\nMore text")
        d = self.detail(t)
        self.assertEqual(d["bounceReason"], "550 5.1.1 The email account that you tried to reach does not exist.")
        self.assertEqual(d["item"]["group"], "closed")

    def test_unknown_touch_is_404_and_a_huge_id_is_400(self):
        self.assertEqual(self.get("/api/outbox/999").status_code, 404)
        self.assertEqual(self.get(f"/api/outbox/{2**63}").status_code, 400)


class CompareTest(OutboxBase):
    def test_compare_only_two_messages_at_one_company_else_bad_request(self):
        acme = seed.company(self.conn, "Acme", status="working")
        other = seed.company(self.conn, "Other", status="working")
        body = "Your post on durable queues changed how I think about job state. " * 3
        a = seed.message(self.conn, self.thread(acme, "Ava A"), 0, "drafted", body=body + "Would you refer me?")
        b = seed.message(self.conn, self.thread(acme, "Ben B"), 0, "drafted", body=body + "Could you refer me?")
        c = seed.message(self.conn, self.thread(other, "Cal C"), 0, "drafted", body=body)

        res = self.get("/api/outbox/compare", params={"messageId": a, "otherMessageId": b})
        self.assertEqual(res.status_code, 200, res.text)
        got = res.json()
        self.assertEqual((got["a"]["messageId"], got["a"]["personName"], got["b"]["personName"]), (a, "Ava A", "Ben B"))
        self.assertGreater(got["similarity"], 0.9)
        self.assertTrue(got["matches"])
        for m in got["matches"]:
            self.assertEqual(got["a"]["body"][m["aStart"]:m["aEnd"]], got["b"]["body"][m["bStart"]:m["bEnd"]])

        for x, y in ((a, c), (a, a)):
            with self.subTest(pair=(x, y)):
                res = self.get("/api/outbox/compare", params={"messageId": x, "otherMessageId": y})
                self.assertEqual(res.status_code, 400, res.text)
                self.assertEqual(res.json()["code"], "bad_request")
        self.assertEqual(self.get("/api/outbox/compare", params={"messageId": a, "otherMessageId": 999}).status_code, 404)
        self.assertEqual(self.get("/api/outbox/compare", params={"messageId": a}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
