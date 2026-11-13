"""Signals per account: the hard bounce rule, hard pauses that stay on one account, notice keys and the resume
that clears an account's hard block."""
import unittest
from datetime import date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from outreach.sending import accounts, notify, ramp, signals
from outreach.sending.config import load_config
from tests.sendtest import UTC, bounce, iso, seed_email_person, seed_sent, with_ramp
from tests.storetest import fresh_store, seed_company

CFG = with_ramp(load_config(), ramp_start=15, ramp_step=3, ramp_floor=15, ramp_ceiling=40)
NY = ZoneInfo("America/New_York")
BASE = date(2026, 11, 2)
STAMP = "2026-11-02T15:00:00Z"


def ny(n: int, hour: int = 9) -> datetime:
    day = BASE + timedelta(days=n)
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=NY).astimezone(UTC)


class Base(unittest.TestCase):
    def setUp(self):
        self.db = fresh_store()
        self.company = seed_company(self.db)
        self.rows = accounts.sync(self.db, CFG, STAMP)
        self.a1, self.a2, self.a3 = (self.rows[a.address] for a in CFG.accounts)
        self.people = 0
        self.notices = []
        self.keys = 0

    def first_emails(self, account, n, at):
        out = []
        for _ in range(n):
            self.people += 1
            person = seed_email_person(self.db, self.company, f"S{self.people}", f"s{self.people}@acme.co")
            seed_sent(self.db, person, at=at, account_id=account.account_id)
            out.append(self.db.execute("SELECT touch_id FROM touch WHERE person_id = ?", (person,)).fetchone()[0])
        return out

    def note(self, account, kind, at, *, key=None):
        self.keys += 1
        ramp.note_signal(self.db, kind, account=account, at=iso(at), detail={}, key=key or f"{kind}:{self.keys}",
                         cfg=CFG, notify=lambda t, x: self.notices.append((t, x)), now=at)

    def acct_row(self, account):
        return self.db.execute("SELECT * FROM sender_account WHERE account_id = ?",
                               (account.account_id,)).fetchone()

    def sender(self):
        return self.db.execute("SELECT * FROM sender WHERE sender_id = 1").fetchone()


class BounceRateLast100Test(Base):
    def test_counts_the_last_100_first_emails_only(self):
        old = self.first_emails(self.a2, 10, ny(0))
        self.first_emails(self.a2, 100, ny(1))
        bounce(self.db, old[0], ny(2))  # the 10 oldest fall outside the last 100
        self.first_emails(self.a3, 5, ny(1))
        self.assertEqual(signals.bounce_rate_last_100(self.db, self.a2.account_id), (0, 100))
        self.assertEqual(signals.bounce_rate_last_100(self.db, self.a3.account_id), (0, 5))

    def test_follow_ups_are_not_first_emails(self):
        touches = self.first_emails(self.a2, 3, ny(0))
        person = self.db.execute("SELECT person_id FROM touch WHERE touch_id = ?", (touches[0],)).fetchone()[0]
        seed_sent(self.db, person, at=ny(1), seq=1)
        self.assertEqual(signals.bounce_rate_last_100(self.db, self.a2.account_id), (0, 3))

    def test_bounced_is_the_touches_in_state_bounced(self):
        touches = self.first_emails(self.a2, 50, ny(0))
        bounce(self.db, touches[0], ny(1))
        self.assertEqual(signals.bounce_rate_last_100(self.db, self.a2.account_id), (1, 50))


class BounceRateHardTest(Base):
    def test_30_sent_one_bounce_is_no_pause_and_50_sent_one_bounce_is(self):
        touches = self.first_emails(self.a2, 30, ny(0))
        bounce(self.db, touches[0], ny(0, 12))
        bounced, sent = signals.bounce_rate_last_100(self.db, self.a2.account_id)
        self.assertEqual((bounced, sent), (1, 30))
        self.assertTrue(signals.bounce_rate_hard(bounced, sent))  # 1/30 is 3.3%: the rule fires at 30 sent
        self.assertFalse(signals.bounce_rate_hard(0, 30))
        self.assertFalse(signals.bounce_rate_hard(1, 29))  # under 30 sent: never
        self.assertTrue(signals.bounce_rate_hard(1, 50))  # exactly 2%
        self.assertFalse(signals.bounce_rate_hard(1, 51))

    def test_bounce_rate_hard_pauses_only_that_account(self):
        touches = self.first_emails(self.a2, 50, ny(0))
        self.first_emails(self.a3, 50, ny(0))
        bounce(self.db, touches[0], ny(0, 12))
        bounced, sent = signals.bounce_rate_last_100(self.db, self.a2.account_id)
        self.assertEqual((bounced, sent), (1, 50))
        self.assertTrue(signals.bounce_rate_hard(bounced, sent))
        self.note(self.a2, "bounce_rate_hard", ny(0, 12))
        own = self.acct_row(self.a2)
        self.assertEqual((own["paused"], own["pause_kind"]), (1, "hard_block"))
        self.assertIn(self.a2.cfg.address, own["paused_reason"])
        self.assertEqual(self.acct_row(self.a3)["paused"], 0)
        self.assertEqual(self.acct_row(self.a1)["paused"], 0)
        self.assertEqual(self.sender()["paused"], 0)

    def test_the_hard_rule_runs_with_the_bounce_in_inbound(self):
        from outreach.sending import inbound
        touches = self.first_emails(self.a2, 50, ny(0))
        bounce(self.db, touches[0], ny(0, 12))
        meta = mock.Mock(gmail_message_id="g1")
        found = mock.Mock(dsn_class="hard")
        with mock.patch.object(inbound, "stop_touch_messages"), mock.patch.object(inbound, "_no_go_the_bounced"):
            inbound._apply_bounce(self.db, touches[0], meta, found, iso(ny(0, 12)), iso(ny(0, 12)), now=ny(0, 12),
                                  cfg=CFG, notify=lambda t, x: None)
        self.assertEqual(self.acct_row(self.a2)["paused"], 1)
        self.assertEqual(self.acct_row(self.a3)["paused"], 0)


class HardPauseScopeTest(Base):
    def test_gmail_limit_pauses_only_that_account(self):
        self.note(self.a2, "gmail_limit", ny(0, 11))
        self.assertEqual(self.acct_row(self.a2)["pause_kind"], "hard_block")
        self.assertEqual(self.acct_row(self.a3)["paused"], 0)
        self.assertEqual(self.sender()["paused"], 0)
        self.assertEqual(accounts.pause_of(self.db, self.a2.account_id).kind, "hard_block")
        self.assertIsNone(accounts.pause_of(self.db, self.a3.account_id))

    def test_his_pause_pauses_every_account(self):
        self.db.execute("UPDATE sender SET paused = 1, pause_kind = 'by_you', paused_reason = 'away', "
                        "paused_at = ? WHERE sender_id = 1", (STAMP,))
        for a in (self.a1, self.a2, self.a3):
            self.assertEqual(accounts.pause_of(self.db, a.account_id).kind, "by_you")

    def test_signal_events_use_the_account_as_entity_id(self):
        self.note(self.a2, "gmail_limit", ny(0, 11))
        self.note(self.a1, "policy_rejection", ny(0, 11))
        rows = self.db.execute("SELECT kind, entity_id FROM event WHERE entity = 'sender' AND kind IN "
                               "('gmail_limit', 'policy_rejection') ORDER BY event_id").fetchall()
        self.assertEqual([(r["kind"], r["entity_id"]) for r in rows],
                         [("gmail_limit", self.a2.account_id), ("policy_rejection", 1)])

    def test_one_accounts_warning_does_not_lower_another(self):
        for a in (self.a2, self.a3):
            self.db.execute("INSERT INTO send_day(account_id, day, send_limit, created_at) VALUES (?, ?, 30, ?)",
                            (a.account_id, BASE.isoformat(), STAMP))
        self.note(self.a2, "policy_rejection", ny(0, 11))
        self.assertEqual(ramp.effective_limit(self.db, self.a2, BASE, CFG), 27)
        self.assertEqual(ramp.effective_limit(self.db, self.a3, BASE, CFG), 30)

    def test_notice_keys_carry_the_account_so_one_does_not_swallow_another(self):
        self.note(self.a2, "gmail_limit", ny(0, 11))
        self.note(self.a3, "gmail_limit", ny(0, 12))
        self.assertEqual(len(self.notices), 2)
        keys = [r[0] for r in self.db.execute("SELECT idempotency_key FROM event WHERE kind = 'notified' "
                                              "ORDER BY event_id")]
        self.assertEqual(keys, [f"notify:gmail_limit:{self.a2.account_id}:2026-11-02",
                                f"notify:gmail_limit:{self.a3.account_id}:2026-11-02"])


class ResumeClearsAccountBlocksTest(Base):
    def test_resume_clears_account_hard_blocks_and_needs_acknowledgement(self):
        from outreach.api.errors import ApiProblem
        from outreach.api.queries import sending as q
        self.note(self.a2, "gmail_limit", ny(0, 11))
        ctx = mock.Mock(conn=self.db, now=STAMP)
        ctx.event_key = lambda i: f"resume:{i}"
        revision = self.sender()["revision"]
        with self.assertRaises(ApiProblem):
            q.resume_sending(ctx, revision, False, CFG, old_sender_loaded=False)
        self.assertEqual(self.acct_row(self.a2)["paused"], 1)
        q.resume_sending(ctx, revision, True, CFG, old_sender_loaded=False)
        own = self.acct_row(self.a2)
        self.assertEqual((own["paused"], own["pause_kind"], own["paused_at"]), (0, None, None))
        self.assertIsNone(accounts.pause_of(self.db, self.a2.account_id))


if __name__ == "__main__":
    unittest.main()
