"""The 200 a day send limit (#227, #9 2026-11-12): migration 0012 widens `send_day.send_limit` to 0..200 and keeps
every row, and the shipped pacing fits 200 sends in one business day when the queue has enough companies."""
import dataclasses
import sqlite3
import unittest
from datetime import date

from outreach.sending.config import load_config
from outreach.sending.simulate import Event, Scenario, cal_days, ny, simulate
from outreach.store.migrate import MIGRATIONS_DIR
from tests import SHIPPED_SEND_CONFIG
from tests.storetest import fresh_store

SQL = (MIGRATIONS_DIR / "0012_send_limit_200.sql").read_text()
T = "2026-11-12T05:00:00Z"


class SendDayMigrationTest(unittest.TestCase):
    def setUp(self):
        self.db = fresh_store()
        self.addCleanup(self.db.close)

    def put(self, day, limit):
        self.db.execute("INSERT INTO send_day(day, send_limit, created_at) VALUES (?, ?, ?)", (day, limit, T))

    def test_a_limit_of_200_is_stored_and_201_is_refused(self):
        self.put("2026-11-12", 200)
        with self.assertRaises(sqlite3.IntegrityError):
            self.put("2026-11-13", 201)
        with self.assertRaises(sqlite3.IntegrityError):
            self.put("2026-11-16", -1)

    def test_the_rebuild_keeps_every_row_and_column(self):
        self.put("2026-11-10", 100)
        self.db.execute("INSERT INTO send_day(day, send_limit, outcome, outcome_note, created_at, closed_at) "
                        "VALUES ('2026-11-11', 150, 'warning', 'bounce rate', ?, ?)", (T, T))
        before = [tuple(r) for r in self.db.execute("SELECT * FROM send_day ORDER BY day")]
        self.db.executescript(SQL)
        self.assertEqual([tuple(r) for r in self.db.execute("SELECT * FROM send_day ORDER BY day")], before)
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(send_day)")]
        self.assertEqual(cols, ["day", "send_limit", "outcome", "outcome_note", "created_at", "closed_at"])


class TwoHundredFitsInADayTest(unittest.TestCase):
    def test_the_shipped_pacing_sends_200_in_a_day_with_enough_companies(self):
        cfg = load_config(SHIPPED_SEND_CONFIG)
        self.assertEqual(cfg.ramp_ceiling, 200)
        # Exact times and first-come order, so the run is the same every time; the limit and the gaps are the shipped ones.
        cfg = dataclasses.replace(cfg, pick_pool=1, day_start_jitter_minutes=0, per_company_gap_jitter_minutes=0)
        day = cal_days(date(2026, 11, 16), 1, cfg)[0]
        scenario = Scenario(start=day, days=1, companies=100, people_per_company=3,
                            zones=("America/New_York", "America/Chicago", "America/Los_Angeles"),
                            events=(Event(ny(day, "07:00"), "approve", {"count": 300}),))
        result = simulate(scenario, seed=11, cfg=cfg)
        self.addCleanup(result.close)
        (row,) = result.days
        self.assertEqual((row.start_limit, row.sent), (200, 200))


if __name__ == "__main__":
    unittest.main()
