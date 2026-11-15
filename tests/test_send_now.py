"""Send next and Send now (#9 outreach fixes ruling, 2026-11-14, point 3; #281).

Send next: first in its account's queue at the next allowed moment, every rule kept. Send now: the sender's next
tick, inside or outside the window, weekends and holidays included, past the spacing and the company gap; it still
needs his click, a signed-in sending account that is not paused, the marker proof and every stop; it counts toward
the account's day and may go past the day's limit by at most 3 per account per New York day. Send now means today
(fix round 1 ruling): one that did not go on the New York day he clicked it keeps its place and still skips weekends,
holidays, spacing and the company gap, but waits for the recipient's 08:00."""
import json
import random
from datetime import timedelta

from outreach.sending import accounts, queue
from outreach.sending.accounts import in_play
from tests.sendtest import iso, put_day, seed_email_person, seed_message, seed_sent, with_ramp
from tests.storetest import seed_company
from tests import test_sending_queue as tq
from tests import test_sending_tick as tt
from tests.test_sending_precheck import body

SAT = tq.ny(7, 18)  # Saturday 2026-11-07, 18:00 in New York


def mark(db, message_id: int, priority: str, at: str = "2026-11-02T13:30:00Z") -> None:
    """His click on Send next or Send now, as the API writes it."""
    db.execute("UPDATE message SET priority = ?, urgent_at = ? WHERE message_id = ?",
               (priority, at if priority == "now" else None, message_id))


class UrgentQueueTest(tq.Base):
    def sent_urgent(self, at, *, company=None) -> int:
        """An email that went out with Send now at `at` from account 1."""
        person = self.person(company=company or seed_company(self.db, f"U{self.n}"))
        mid = seed_message(self.db, person, state="drafted")
        self.db.execute("UPDATE message SET state = 'approved', approved_at = ?, priority = 'now', urgent_at = ? "
                        "WHERE message_id = ?", (iso(at), iso(at), mid))
        self.db.execute("UPDATE message SET state = 'sending', pending_rfc_id = ?, claimed_at = ? "
                        "WHERE message_id = ?", (f"<u{mid}@x>", iso(at), mid))
        self.db.execute("UPDATE message SET state = 'sent', sent_at = ?, rfc_message_id = ?, pending_rfc_id = NULL, "
                        "claimed_at = NULL WHERE message_id = ?", (iso(at), f"<u{mid}@x>", mid))
        return mid

    def test_send_now_on_saturday_evening_is_ready(self):
        _, normal = self.approve(company=seed_company(self.db, "N"))
        _, urgent = self.approve()
        mark(self.db, urgent, "now", iso(SAT))
        found = self.verdicts_at(SAT)
        self.assertEqual(found[normal].reason, "not_business_day")
        self.assertEqual(found[urgent].reason, "ready")

    def test_send_now_skips_the_window_the_spacing_and_the_company_gap(self):
        _, urgent = self.approve()
        mark(self.db, urgent, "now")
        late = tq.ny(2, 20)  # Monday 20:00, the window closed at 15:00
        self.assertEqual(self.reason(urgent, late), "ready")
        self.db.execute("UPDATE sender_account SET next_send_at = ? WHERE account_id = 1",
                        (iso(tq.MON + timedelta(minutes=30)),))
        self.sent_at(self.company, tq.MON - timedelta(minutes=5))  # its company got an email 5 minutes ago
        self.assertEqual(self.reason(urgent, tq.MON), "ready")
        mark(self.db, urgent, "normal")
        self.assertEqual(self.reason(urgent, tq.MON), "per_company_gap")

    def test_send_now_keeps_every_stop(self):
        cases = {}
        no_go_person, cases["no_go"] = self.approve(company=seed_company(self.db, "NoGo"))
        self.db.execute("INSERT INTO no_go(person_id, reason, source, created_at) VALUES (?, 'said_no', 'reply', "
                        "'2026-11-01T12:00:00Z')", (no_go_person,))
        held = seed_company(self.db, "Held")
        replier = self.person(company=held)
        seed_sent(self.db, replier, at=tq.ny(1, 9))
        self.db.execute("UPDATE company SET held_by_touch = ?, held_at = '2026-11-02T12:00:00Z' WHERE company_id = ?",
                        (self.touch_of(replier), held))
        _, cases["company_held"] = self.approve(company=held)
        paused = seed_company(self.db, "Paused")
        self.db.execute("UPDATE company SET status = 'working', paused_at = '2026-11-01T12:00:00Z' "
                        "WHERE company_id = ?", (paused,))
        _, cases["company_paused"] = self.approve(company=paused)
        closed = seed_company(self.db, "Closed")
        self.db.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?", (closed,))
        _, cases["company_closed"] = self.approve(company=closed)
        replied, cases["reply_stop"] = self.approve(company=seed_company(self.db, "Replied"))
        self.db.execute("UPDATE touch SET state = 'replied', replied_at = '2026-11-02T12:00:00Z', reply_kind = 'interested' WHERE person_id = ?",
                        (replied,))
        for mid in cases.values():
            mark(self.db, mid, "now")
        found = self.verdicts_at(SAT)
        for reason, mid in cases.items():
            self.assertEqual(found[mid].reason, reason)

    def test_send_now_waits_while_the_account_is_paused_or_signed_out(self):
        _, urgent = self.approve()
        mark(self.db, urgent, "now")
        self.db.execute("UPDATE sender SET paused = 1, pause_kind = 'by_you', paused_reason = 'x', "
                        "paused_at = '2026-11-02T12:00:00Z' WHERE sender_id = 1")
        self.assertEqual(self.reason(urgent, SAT), "sender_paused")
        self.db.execute("UPDATE sender SET paused = 0, pause_kind = NULL, paused_reason = '', paused_at = NULL")
        accounts.hard_pause(self.db, 1, "Gmail said no", "2026-11-02T13:00:00Z")
        self.assertEqual(self.reason(urgent, SAT), "sender_paused")
        self.db.execute("UPDATE sender_account SET paused = 0, pause_kind = NULL, paused_reason = '', "
                        "paused_at = NULL, auth_state = 'signed_out'")
        self.assertEqual(self.reason(urgent, SAT), "signed_out")

    def test_send_now_waits_while_replies_could_not_be_read(self):
        _, urgent = self.approve()
        mark(self.db, urgent, "now")
        found = {v.message_id: v for v in queue.verdicts(self.db, SAT, tq.CFG, in_play(self.db, tq.CFG), day=None,
                                                         gate=None, unread=frozenset({1}))}
        self.assertEqual(found[urgent].reason, "inbound_unread")

    def test_send_now_waits_while_the_proof_lets_only_one_email_go(self):
        _, first = self.approve(company=seed_company(self.db, "First"))
        _, urgent = self.approve()
        mark(self.db, urgent, "now")
        found = list(self.verdicts_at(tq.MON).values())
        # The first send of an unproven account: one email goes alone, and Send now takes that one slot first.
        gated = {v.message_id: v.reason for v in queue.gate_accounts(found, {}, [tq.ONE])}
        self.assertEqual(gated, {urgent: "ready", first: "message_id_unproven"})
        # The first email is claimed and not yet checked in Sent: nothing more goes, Send now included.
        gated = {v.message_id: v.reason for v in queue.gate_accounts(found, {tq.ONE: "message_id_unproven"})}
        self.assertEqual(gated, {urgent: "message_id_unproven", first: "message_id_unproven"})

    def test_past_the_limit_three_send_now_emails_go_and_the_fourth_waits(self):
        put_day(self.db, tq.MON_DAY, 2)
        self.sent_at(seed_company(self.db, "Day"), tq.ny(2, 8, 30), n=2)  # the day's limit is used up
        _, urgent = self.approve()
        mark(self.db, urgent, "now")
        for n in range(3):
            self.assertEqual(self.reason(urgent), "ready", n)
            self.sent_urgent(tq.MON - timedelta(minutes=10 - n))
        verdict = self.verdicts_at()[urgent]
        self.assertEqual(verdict.reason, "daily_limit")
        self.assertEqual(verdict.text, queue.URGENT_LIMIT_TEXT)
        self.assertIn("limit plus 3", verdict.text)
        self.assertNotIn("midnight", verdict.text)
        self.assertEqual(verdict.earliest_at, tq.ny(3, 8))  # the next morning, 08:00 in the recipient's time

    def test_send_now_emails_within_the_limit_do_not_use_up_the_three(self):
        # #9's words: past the day's limit by at most 3. Send now emails inside the limit are part of the day.
        put_day(self.db, tq.MON_DAY, 5)
        self.sent_at(seed_company(self.db, "Day"), tq.ny(2, 8, 30), n=2)
        for n in range(3):
            self.sent_urgent(tq.ny(2, 9, n))
        _, urgent = self.approve()
        mark(self.db, urgent, "now")
        self.assertEqual(self.reason(urgent), "ready")  # 5 of 5: three more may still go

    def test_a_capped_send_now_goes_at_eight_the_next_morning_not_at_midnight(self):
        put_day(self.db, tq.MON_DAY, 2)
        self.sent_at(seed_company(self.db, "Day"), tq.ny(2, 8, 30), n=5)  # the limit plus 3 is reached
        _, urgent = self.approve()
        _, west = self.approve(company=seed_company(self.db, "West"), tz="America/Los_Angeles")
        for mid in (urgent, west):
            mark(self.db, mid, "now", iso(tq.ny(2, 15)))
        found = self.verdicts_at(tq.ny(2, 16))
        self.assertEqual((found[urgent].reason, found[urgent].earliest_at), ("daily_limit", tq.ny(3, 8)))
        self.assertEqual(found[west].earliest_at, tq.ny(3, 11))  # 08:00 in Los Angeles
        # Just after midnight the day has room again, but Send now meant Monday: it waits for the morning.
        found = self.verdicts_at(tq.ny(3, 0, 5))
        self.assertEqual((found[urgent].reason, found[urgent].earliest_at), ("window", tq.ny(3, 8)))
        self.assertEqual(self.reason(urgent, tq.ny(3, 8)), "ready")
        self.assertEqual(self.reason(west, tq.ny(3, 8)), "window")
        # It keeps its first place: queue order puts it ahead of an older normal approval.
        self.assertEqual(queue.load_queue(self.db, tq.CFG)[0].message_id, urgent)

    def test_a_send_now_from_yesterday_skips_the_weekend_but_waits_for_the_morning(self):
        _, urgent = self.approve()
        mark(self.db, urgent, "now", iso(tq.ny(6, 20)))  # Friday 20:00, held overnight by a paused company
        found = self.verdicts_at(tq.ny(7, 7))
        self.assertEqual((found[urgent].reason, found[urgent].earliest_at), ("window", tq.ny(7, 8)))
        self.assertEqual(self.reason(urgent, tq.ny(7, 9)), "ready")  # Saturday 09:00: no wait for Monday
        self.assertEqual(self.reason(urgent, tq.ny(7, 18)), "window")  # past the recipient's hours

    def test_send_now_never_goes_more_than_three_past_a_lowered_limit(self):
        put_day(self.db, tq.MON_DAY, 2)
        self.sent_at(seed_company(self.db, "Day"), tq.ny(2, 8, 30), n=5)  # 3 past the limit, no Send now among them
        _, urgent = self.approve()
        mark(self.db, urgent, "now")
        self.assertEqual(self.reason(urgent), "daily_limit")

    def test_the_cap_is_per_account_per_new_york_day(self):
        put_day(self.db, tq.MON_DAY, 2)
        self.sent_at(seed_company(self.db, "Day"), tq.ny(2, 8, 30), n=2)
        for n in range(3):
            self.sent_urgent(tq.ny(1, 22, n))  # Sunday night in New York: yesterday's
        _, urgent = self.approve()
        mark(self.db, urgent, "now")
        self.assertEqual(self.reason(urgent), "ready")

    def test_a_send_now_that_is_skipped_or_stopped_drops_its_priority(self):
        from outreach.api.queries.outcomes import stop_unsent_messages
        from outreach.sending.followups import stop_touch_messages
        person, stopped = self.approve()
        mark(self.db, stopped, "now")
        stop_touch_messages(self.db, self.touch_of(person), reason="replied", now="2026-11-02T14:00:00Z")
        other, by_reply = self.approve(company=seed_company(self.db, "Said no"))
        mark(self.db, by_reply, "next")
        stop_unsent_messages(self.db, other, "said_no", now="2026-11-02T14:00:00Z", keys=iter(["k1", "k2"]))
        for mid in (stopped, by_reply):
            row = self.db.execute("SELECT state, priority, urgent_at FROM message WHERE message_id = ?",
                                  (mid,)).fetchone()
            self.assertEqual(tuple(row), ("skipped", "normal", None))

    def test_send_now_does_not_skip_the_per_company_first_email_limit(self):
        put_day(self.db, tq.MON_DAY, 15)
        self.sent_at(self.company, tq.ny(2, 8, 30), n=5)  # a small company's first emails today are used up
        _, urgent = self.approve()
        mark(self.db, urgent, "now")
        self.assertEqual(self.reason(urgent), "per_company_limit")

    def test_send_now_waits_for_a_send_to_its_company_in_the_same_tick(self):
        _, urgent = self.approve()
        mark(self.db, urgent, "now")
        tick_start = tq.MON - timedelta(seconds=30)
        self.sent_at(self.company, tq.MON - timedelta(seconds=10))  # another account sent there in this tick
        found = {v.message_id: v for v in queue.verdicts(self.db, tq.MON, tq.CFG, in_play(self.db, tq.CFG), day=None,
                                                         gate=None, fresh_after=tick_start)}
        self.assertEqual(found[urgent].reason, "per_company_gap")
        found = {v.message_id: v for v in queue.verdicts(self.db, tq.MON, tq.CFG, in_play(self.db, tq.CFG), day=None,
                                                         gate=None, fresh_after=tq.MON)}
        self.assertEqual(found[urgent].reason, "ready")

    def test_send_now_still_waits_for_a_follow_up_to_be_due(self):
        _, followup = self.approve_followup("2026-11-09")
        mark(self.db, followup, "now")
        self.assertEqual(self.reason(followup, SAT), "followup_not_due")

    def test_send_next_goes_first_in_its_account_and_keeps_the_window(self):
        older = [self.approve(company=seed_company(self.db, f"Old{i}"), approved_at="2026-11-02T12:00:00Z")[1]
                 for i in range(3)]
        _, nxt = self.approve(company=seed_company(self.db, "Next"), approved_at="2026-11-02T13:50:00Z")
        mark(self.db, nxt, "next")
        found = list(self.verdicts_at().values())
        items = queue.load_queue(self.db, tq.CFG)
        self.assertEqual(items[0].message_id, nxt)
        self.assertEqual(queue.pick(found, items, account=tq.ONE).message_id, nxt)
        picked = {queue.pick(found, items, random.Random(s), 5, now=tq.MON, cfg=tq.CFG, account=tq.ONE).message_id
                  for s in range(30)}
        self.assertEqual(picked, {nxt})
        late = tq.ny(2, 16)
        self.assertEqual({self.reason(m, late) for m in [*older, nxt]}, {"window"})
        self.assertEqual(self.reason(nxt, SAT), "not_business_day")

    def test_send_now_goes_before_send_next(self):
        _, nxt = self.approve(company=seed_company(self.db, "Next"))
        _, now = self.approve(company=seed_company(self.db, "Now"))
        mark(self.db, nxt, "next")
        mark(self.db, now, "now")
        found = list(self.verdicts_at().values())
        items = queue.load_queue(self.db, tq.CFG)
        self.assertEqual(queue.pick(found, items, random.Random(3), 5, now=tq.MON, cfg=tq.CFG, account=tq.ONE)
                         .message_id, now)


class UrgentTickTest(tt.Base):
    cfg = with_ramp(tt.CFG, ramp_floor=1)

    def test_send_now_goes_on_the_next_tick_on_saturday_evening(self):
        self.clock.set(SAT)
        normal = self.approve()  # a Send now clicked at Saturday 18:00 goes on the next tick
        urgent = self.approve()
        mark(self.db, urgent, "now", iso(SAT))
        report = self.tick()
        self.assertEqual(report.sent, (urgent,))
        self.assertEqual(self.message(urgent)["state"], "sent")
        self.assertEqual(self.message(urgent)["priority"], "now")
        self.assertEqual(self.message(normal)["state"], "approved")
        self.assertEqual(report.waiting, {"not_business_day": 1})
        self.assertEqual(self.problems, [])

    def test_send_now_to_a_bounced_address_goes_back_to_review(self):
        self.clock.set(SAT)
        urgent = self.approve()
        mark(self.db, urgent, "now", iso(SAT))
        self.db.execute("INSERT INTO bounced_address(address, first_seen_at, source) VALUES (?, ?, 'bounce')",
                        (self.emails[urgent], iso(SAT)))
        report = self.tick()
        self.assertEqual(report.sent, ())
        row = self.message(urgent)
        self.assertEqual((row["state"], row["priority"], row["urgent_at"]), ("drafted", "normal", None))
        self.assertIn("bounced_address", [c["code"] for c in json.loads(row["checks"])])
        self.assertEqual(self.gmail.sent, [])

    def test_four_send_now_emails_past_the_limit_three_go(self):
        put_day(self.db, tq.MON_DAY, 1)
        day = seed_email_person(self.db, seed_company(self.db, "Day"), "Day", "day@day.example")
        seed_sent(self.db, day, at=tt.MON - timedelta(hours=1), account_id=1)  # the day's one email is out
        urgent = [self.approve() for _ in range(4)]
        for mid in urgent:
            mark(self.db, mid, "now", iso(tt.MON))
        sent = []
        for _ in range(5):
            self.clock.advance(minutes=2)
            sent += self.tick().sent
        self.assertEqual(sorted(sent), sorted(urgent[:3]))
        report = self.tick()
        self.assertEqual(report.waiting, {"daily_limit": 1})
        self.assertEqual(self.message(urgent[3])["state"], "approved")


class UrgentTwoAccountsTest(tt.MultiBase):
    cfg = tt.TWO

    def test_two_send_now_emails_to_one_company_never_both_go_in_one_tick(self):
        for seed in range(6):
            with self.subTest(seed=seed):
                self.setUp()
                self.rng = random.Random(seed)
                company = seed_company(self.db, "Shared")
                for n, (name, account, mailbox) in enumerate((("a", 1, self.gmail),
                                                              ("b", self.two.account_id, self.gmail2))):
                    email = f"{name}@shared.example"
                    person = seed_email_person(self.db, company, name.upper(), email)
                    at = tt.MON - timedelta(days=9)
                    meta = mailbox.add_to_sent(email, f"Hello {n}", message_id=f"<{name}@shared.local>", at=at)
                    first = seed_sent(self.db, person, at=at, thread=meta.thread_id, rfc=f"<{name}@shared.local>",
                                      account_id=account)
                    self.db.execute("UPDATE message SET gmail_message_id = ? WHERE message_id = ?",
                                    (meta.gmail_message_id, first))
                    subject = self.db.execute("SELECT subject FROM message WHERE message_id = ?",
                                              (first,)).fetchone()[0]
                    mid = seed_message(self.db, person, seq=1, due_on="2026-11-02", subject=f"Re: {subject}",
                                       body=body(80) if n == 0 else tt.OTHER_BODY)
                    mark(self.db, mid, "now", iso(tt.MON))
                report = self.tick()
                self.assertEqual(len(report.sent), 1, (report.waiting, report.lines))
                self.clock.advance(minutes=2)
                self.assertEqual(len(self.tick().sent), 1)  # the next tick: no gap wait for Send now
