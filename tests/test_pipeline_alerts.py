"""The Mac alert for a new job at his level (#156, Task 5): one grouped notice, quiet at night, once per posting.

A fresh store, hand-written rows and a list for a notifier: nothing here pops a notice or reaches the network.
"""
import itertools
import unittest
from unittest import mock

from outreach.crawl import tick
from outreach.pipeline import alerts
from outreach.store import append_event
from outreach.store.timefmt import utcnow
from tests.ranking_store import BACKFILL, board, decision, evidence, no_go, posting
from tests.storetest import fresh_store, seed_company

NOW = "2026-11-03T15:00:00Z"  # 10:00 on 2026-11-03 in New York (EST)
TODAY = "2026-11-03"
_names = itertools.count(1)


class AlertCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.notices: list[tuple[str, str]] = []

    def notify(self, title, text):
        self.notices.append((title, text))

    def alert(self, now=NOW):
        return alerts.run(self.conn, self.notify, now)

    def job(self, title="Software Engineer", *, company=None, size=None, posted=TODAY, tier="new_grad", ok=1,
            seen="2026-11-03T14:00:00Z", seen_gap_s=1800, posted_at=None, repost_of=None, list_at=BACKFILL, **dec):
        """A posting first seen at 09:00 New York today on a board read half an hour before (not its first read);
        its list time is what the 0009 backfill gives those dates unless `list_at` says otherwise."""
        cid = company or seed_company(self.conn, f"Company {next(_names)}")
        if size is not None:
            evidence(self.conn, cid, "size", num_low=size, num_high=size)
        pid = posting(self.conn, cid, title, posted_on=posted, first_seen_at=seen, last_seen_at=seen,
                      seen_gap_s=seen_gap_s, posted_at=posted_at, repost_of=repost_of, list_at=list_at)
        decision(self.conn, pid, level_tier=tier, level_ok=ok, **dec)
        return pid

    def events(self, kind="alerted"):
        return self.conn.execute("SELECT * FROM event WHERE kind = ? ORDER BY event_id", (kind,)).fetchall()

    def last_alert(self):
        row = self.conn.execute("SELECT value FROM pipeline_meta WHERE key = 'last_alert_at'").fetchone()
        return row[0] if row else None


class OncePerPostingTests(AlertCase):
    def test_alert_once_per_posting(self):
        first = self.job("Backend Engineer", company=seed_company(self.conn, "Acme"))
        self.assertEqual(self.alert(), 1)
        self.assertEqual(self.notices, [("New jobs at your level", "Backend Engineer at Acme")])
        e, = self.events()
        self.assertEqual((e["entity"], e["entity_id"], e["actor"], e["idempotency_key"]),
                         ("posting", first, "system", f"alert:posting:{first}"))
        self.assertEqual(self.last_alert(), NOW)
        # Past the 15 minutes, with nothing new: no notice. With one more posting: only the new one is announced.
        self.assertEqual(self.alert("2026-11-03T15:30:00Z"), 0)
        second = self.job("Data Engineer", company=seed_company(self.conn, "Beta"))
        self.assertEqual(self.alert("2026-11-03T15:31:00Z"), 1)
        self.assertEqual(self.notices[1], ("New jobs at your level", "Data Engineer at Beta"))
        self.assertEqual([e["entity_id"] for e in self.events()], [first, second])

    def test_one_notice_names_three_and_counts_the_rest(self):
        for n in range(5):
            self.job(f"Role {n}", company=seed_company(self.conn, f"Co {n}"), size=10 + n)
        self.assertEqual(self.alert(), 5)
        title, text = self.notices[0]
        self.assertEqual(len(self.notices), 1)
        self.assertEqual(text, "Role 0 at Co 0; Role 1 at Co 1; Role 2 at Co 2 and 2 more")
        self.assertEqual(len(self.events()), 5)

    def test_only_a_date_of_today_in_new_york_alerts(self):
        self.job("Yesterday", posted="2026-11-02")
        self.assertEqual(self.alert(), 0)
        self.assertEqual(self.notices, [])
        self.assertEqual(self.events(), [])
        self.assertIsNone(self.last_alert())

    def test_a_future_dated_row_does_not_silence_todays_alerts(self):
        # A list time on tomorrow's New York date (a clock ahead): it heads the list, and must not hide today's rows.
        self.job("Tomorrow", posted=None, list_at="2026-11-04T06:00:00Z")
        today = self.job("Today", company=seed_company(self.conn, "Acme"))
        self.assertEqual(self.alert(), 1)
        self.assertEqual(self.notices, [("New jobs at your level", "Today at Acme")])
        self.assertEqual([e["entity_id"] for e in self.events()], [today])

    def test_a_big_day_is_one_query_per_tick(self):
        for n in range(120):
            self.job(f"Role {n}", company=seed_company(self.conn, f"Co {n}"))
        with mock.patch.object(alerts, "query", wraps=alerts.query) as q:
            self.assertEqual(self.alert(), 120)
        self.assertEqual(q.call_count, 1)

    def test_a_posting_with_no_posted_date_uses_the_day_it_was_seen(self):
        cid = seed_company(self.conn, "Seen Co")
        pid = posting(self.conn, cid, "Seen Engineer", first_seen_at="2026-11-03T14:00:00Z",
                      last_seen_at="2026-11-03T14:00:00Z", seen_gap_s=600)  # 09:00 NY today, the board read before
        decision(self.conn, pid, level_tier="new_grad", level_ok=1)
        self.assertEqual(self.alert(), 1)

    def test_a_passed_posting_and_a_no_go_company_do_not_alert(self):
        passed = self.job("Passed")
        append_event(self.conn, entity="posting", entity_id=passed, kind="posting_passed", actor="devanshu",
                     detail={"posting_id": passed})
        banned = seed_company(self.conn, "Banned")
        self.job("Banned Job", company=banned)
        no_go(self.conn, banned)
        self.assertEqual(self.alert(), 0)


class ListTimeTests(AlertCase):
    """An alert is for a job posted today on a board Knock already read, never a repost or a board's first read
    (#193, design 4.4)."""

    def test_job_posted_today_on_known_board_alerts(self):
        cid = seed_company(self.conn, "Acme")
        board(self.conn, cid, ats="greenhouse")
        # Posted 09:30 New York, seen ten minutes later on a board read half an hour before.
        today = self.job("Today", company=cid, posted=None, seen="2026-11-03T14:40:00Z",
                         posted_at="2026-11-03T14:30:00Z", list_at="2026-11-03T14:30:00Z")
        # Seen today on the same board, but the board says it went up yesterday: not new.
        self.job("Yesterday", company=cid, posted=None, seen="2026-11-03T14:40:00Z",
                 posted_at="2026-11-02T20:00:00Z", list_at="2026-11-02T20:00:00Z")
        # Date unknown: no list time, never an alert.
        self.job("Unknown", company=cid, posted=None, list_at=None)
        self.assertEqual(self.alert(), 1)
        self.assertEqual(self.notices, [("New jobs at your level", "Today at Acme")])
        self.assertEqual([e["entity_id"] for e in self.events()], [today])

    def test_first_read_never_alerts(self):
        # A newly added board's job posted this morning: its list time is today, but it came in on the first read.
        self.job("First read", posted=None, seen_gap_s=None, posted_at="2026-11-03T13:30:00Z",
                 list_at="2026-11-03T13:30:00Z")
        self.assertEqual(self.alert(), 0)
        self.assertEqual((self.notices, self.events(), self.last_alert()), ([], [], None))

    def test_repost_never_alerts(self):
        cid = seed_company(self.conn, "Acme")
        gone = posting(self.conn, cid, "Software Engineer", first_seen_at="2026-08-01T12:00:00Z",
                       last_seen_at="2026-10-20T12:00:00Z", closed=True)
        self.job("Software Engineer", company=cid, posted=None, repost_of=gone, posted_at="2026-11-03T13:30:00Z",
                 list_at="2026-11-03T13:30:00Z")
        fresh = self.job("Data Engineer", company=cid)
        self.assertEqual(self.alert(), 1)
        self.assertEqual(self.notices, [("New jobs at your level", "Data Engineer at Acme")])
        self.assertEqual([e["entity_id"] for e in self.events()], [fresh])


class QuietHoursTests(AlertCase):
    def test_no_alert_in_quiet_hours(self):
        self.job()
        # 07:59 NY, 22:00 NY and 23:30 NY of the same New York day, and 03:00 NY.
        for now in ("2026-11-03T12:59:00Z", "2026-11-04T03:00:00Z", "2026-11-04T04:30:00Z"):
            self.assertEqual(self.alert(now), 0, now)
        self.assertEqual(self.notices, [])
        self.assertEqual(self.events(), [])
        self.assertIsNone(self.last_alert())

    def test_what_came_in_overnight_is_announced_at_eight(self):
        self.job()
        self.assertEqual(self.alert("2026-11-03T12:59:00Z"), 0)  # 07:59
        self.assertEqual(self.alert("2026-11-03T13:00:00Z"), 1)  # 08:00 sharp
        self.assertEqual(len(self.notices), 1)

    def test_just_before_ten_at_night_still_alerts(self):
        self.job()
        self.assertEqual(self.alert("2026-11-04T02:59:00Z"), 1)  # 21:59 NY


class SpacingTests(AlertCase):
    def test_at_most_one_notice_per_15_minutes(self):
        self.job("One")
        self.assertEqual(self.alert("2026-11-03T15:00:00Z"), 1)
        self.job("Two")
        self.assertEqual(self.alert("2026-11-03T15:14:59Z"), 0)
        self.assertEqual(len(self.notices), 1)
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.alert("2026-11-03T15:15:00Z"), 1)
        self.assertEqual(len(self.notices), 2)
        self.assertEqual(self.last_alert(), "2026-11-03T15:15:00Z")


class LevelTests(AlertCase):
    def test_uncertain_row_does_not_alert(self):
        small = seed_company(self.conn, "Tiny")
        evidence(self.conn, small, "size", num_low=5, num_high=5)
        self.job("Unknown level", company=small, tier="not_stated", ok=0)
        self.job("Open level", company=small, tier="open", ok=0)
        self.job("Senior", tier="senior", ok=0)
        self.job("Big company, level not stated", tier="not_stated", ok=0, company=self.big())
        self.assertEqual(self.alert(), 0)
        self.assertEqual(self.notices, [])

    def big(self):
        cid = seed_company(self.conn, "Huge")
        evidence(self.conn, cid, "size", num_low=5000, num_high=5000)
        return cid

    def test_an_open_tier_posting_with_level_ok_is_at_his_level_and_alerts(self):
        pid = self.job("Open but fits", tier="open", ok=1, company=seed_company(self.conn, "Fits"))
        self.assertEqual(self.alert(), 1)
        self.assertEqual(self.events()[0]["entity_id"], pid)

    def test_a_higher_level_posting_does_not_alert(self):
        self.job("Big company SDE 2", tier="swe2", ok=0, company=self.big())
        self.assertEqual(self.alert(), 0)

    def test_only_fit_alerts_not_uncertain_at_a_small_company(self):
        small = seed_company(self.conn, "Tiny")
        evidence(self.conn, small, "size", num_low=5, num_high=5)
        self.job("Not stated", company=small, tier="not_stated", ok=0, level_state="uncertain")
        fit = self.job("Fits", company=small, tier="swe1", level_state="fit")
        self.assertEqual(self.alert(), 1)
        self.assertEqual([e["entity_id"] for e in self.events()], [fit])
        self.assertEqual(self.notices, [("New jobs at your level", "Fits at Tiny")])

    def test_a_reject_does_not_alert_even_with_level_ok(self):
        self.job("Years over three", tier="new_grad", ok=1, level_state="reject")
        self.assertEqual(self.alert(), 0)

    def test_a_row_with_no_state_alerts_by_level_ok(self):
        # Before the rebuild fills `level_state`, the v1 rule decides: fit when level_ok.
        small = seed_company(self.conn, "Tiny")
        evidence(self.conn, small, "size", num_low=5, num_high=5)
        self.job("Unsure", company=small, tier="not_stated", ok=0)
        pid = self.job("Fits", company=small)
        self.assertEqual(self.alert(), 1)
        self.assertEqual(self.events()[0]["entity_id"], pid)


class TickTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def test_tick_survives_alert_error(self):
        notices = []
        with mock.patch.object(tick.alerts, "run", side_effect=RuntimeError("boom")):
            report = tick.run_tick(self.conn, mock.Mock(), now_fn=lambda: NOW, notifier=lambda t, x: notices.append(x),
                                   limit=1)
        self.assertIn("boom", report.alert_error)
        self.assertEqual(report.alerted, 0)
        self.assertFalse(self.conn.in_transaction)
        summary = self.conn.execute("SELECT summary FROM job_run WHERE job = 'read_boards'").fetchone()[0]
        self.assertIn("boom", summary)

    def test_the_tick_reports_how_many_it_announced(self):
        with mock.patch.object(tick.alerts, "run", return_value=2) as run:
            report = tick.run_tick(self.conn, mock.Mock(), now_fn=lambda: NOW, limit=1)
        self.assertEqual((report.alerted, report.alert_error), (2, ""))
        self.assertEqual(run.call_args.args[2], NOW)


if __name__ == "__main__":
    unittest.main()
