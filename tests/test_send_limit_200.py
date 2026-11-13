"""The 200 a day send limit (#227, #9 2026-11-12): migration 0012 widens `send_day.send_limit` to 0..200 and keeps
every row; and an account at its ceiling of 40 sends all 40 inside the window."""
import dataclasses
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date
from zoneinfo import ZoneInfo
from pathlib import Path

from outreach.sending.config import load_config
from outreach.sending.simulate import Event, Scenario, cal_days, ny, simulate
from outreach.store.migrate import MIGRATIONS_DIR, migrate, migration_files
from tests import SHIPPED_SEND_CONFIG
from tests.sendtest import with_ramp
from tests.storetest import fresh_store

SQL = (MIGRATIONS_DIR / "0012_send_limit_200.sql").read_text()
T = "2026-11-12T05:00:00Z"


class SendDayMigrationTest(unittest.TestCase):
    def setUp(self):
        self.db = fresh_store()
        self.addCleanup(self.db.close)

    def put(self, day, limit):
        self.db.execute("INSERT INTO send_day(account_id, day, send_limit, created_at) VALUES (1, ?, ?, ?)", (day, limit, T))

    def test_a_limit_of_200_is_stored_and_201_is_refused(self):
        self.put("2026-11-12", 200)
        with self.assertRaises(sqlite3.IntegrityError):
            self.put("2026-11-13", 201)
        with self.assertRaises(sqlite3.IntegrityError):
            self.put("2026-11-16", -1)

    def test_the_rebuild_keeps_every_row_and_column(self):
        # 0012 runs on a store at version 11 (0015 later rebuilds send_day again, per account).
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for version, _name, path in migration_files():
            if version <= 11:
                shutil.copy(path, Path(tmp.name) / path.name)
        db = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(db.close)
        self.assertEqual(migrate(db, Path(tmp.name)), 11)
        db.execute("INSERT INTO send_day(day, send_limit, created_at) VALUES ('2026-11-10', 100, ?)", (T,))
        db.execute("INSERT INTO send_day(day, send_limit, outcome, outcome_note, created_at, closed_at) "
                   "VALUES ('2026-11-11', 90, 'warning', 'bounce rate', ?, ?)", (T, T))
        before = [tuple(r) for r in db.execute("SELECT * FROM send_day ORDER BY day")]
        db.executescript(SQL)
        self.assertEqual([tuple(r) for r in db.execute("SELECT * FROM send_day ORDER BY day")], before)
        cols = [r[1] for r in db.execute("PRAGMA table_info(send_day)")]
        self.assertEqual(cols, ["day", "send_limit", "outcome", "outcome_note", "created_at", "closed_at"])


class AccountAtItsCeilingTest(unittest.TestCase):
    """The flat 200 is gone from the shipped config (#9, 2026-11-12, multi-sender). What stays is that an account at its
    ceiling of 40 sends all 40 inside the 08:00 to 15:00 window, with recipients in New York and Los Angeles."""

    def test_an_account_at_its_ceiling_sends_40_in_the_window(self):
        cfg = with_ramp(load_config(SHIPPED_SEND_CONFIG), ramp_start=40, ramp_step=3, ramp_floor=15, ramp_ceiling=40)
        # Exact times and first-come order, so the run is the same every time.
        cfg = dataclasses.replace(cfg, pick_pool=1, day_start_jitter_minutes=0, per_company_gap_jitter_minutes=0)
        day = cal_days(date(2026, 11, 16), 1, cfg)[0]
        # 30 companies, 20 in New York and then 10 in Los Angeles, 2 first emails a day each: 60 could go.
        scenario = Scenario(start=day, days=1, companies=30, people_per_company=2,
                            zones=("America/New_York",) * 20 + ("America/Los_Angeles",) * 10,
                            events=(Event(ny(day, "07:00"), "approve", {"count": 60}),))
        result = simulate(scenario, seed=11, cfg=cfg)
        self.addCleanup(result.close)
        (row,) = result.days
        self.assertEqual((row.start_limit, row.sent), (40, 40))
        for send in result.sends:
            local = send.at.astimezone(ZoneInfo(send.zone))
            self.assertTrue(cfg.window_start <= local.time() <= cfg.window_end, f"{send.person} at {local}")
        self.assertEqual({s.zone for s in result.sends}, {"America/New_York", "America/Los_Angeles"})


if __name__ == "__main__":
    unittest.main()
