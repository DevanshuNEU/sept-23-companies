"""Company size pacing (#229, #9 2026-11-12): a company's daily first emails and its gap follow its size band.

Shipped bands: up to 100 people 2 a day (gap 60 + 0..60), 101 to 200 4 (60 + 0..30), 201 to 500 5 (45 + 0..30),
501 to 1,000 8 (30 + 0..20), over 1,000 15 (20 + 0..15). No size on file but the job list's big guess (a Workday or
Oracle board, or over 50 open US postings): as 501 to 1,000. No size and no sign of being big: the default, 2.
"""
import dataclasses
import random
import unittest
from datetime import date, timedelta
from unittest import mock

from outreach.sending import human, pace, queue, ramp
from outreach.sending.config import load_config
from outreach.sending.simulate import Event, Scenario, cal_days, ny as sim_ny, simulate
from tests import SHIPPED_SEND_CONFIG
from tests.ranking_store import board, decision, evidence, posting
from tests.sendtest import put_day, seed_email_person, seed_message, seed_sent
from tests.storetest import fresh_store, seed_company
from tests.test_sending_config import config_with
from tests.test_sending_queue import MON, ny

SHIPPED = load_config(SHIPPED_SEND_CONFIG)
# The shipped bands with exact gaps (no jitter), on the tests' floor of 1, so a test can count minutes.
CFG = dataclasses.replace(load_config(), ramp_floor=1,
                          size_band_gap_jitter_minutes=(0,) * len(SHIPPED.size_band_daily))


class ShippedBandsTest(unittest.TestCase):
    def test_the_shipped_bands(self):
        self.assertEqual(SHIPPED.size_band_up_to, (100, 200, 500, 1000))
        self.assertEqual(SHIPPED.size_band_daily, (2, 4, 5, 8, 15))
        self.assertEqual(SHIPPED.size_band_gap_minutes, (60, 60, 45, 30, 20))
        self.assertEqual(SHIPPED.size_band_gap_jitter_minutes, (60, 30, 30, 20, 15))
        self.assertEqual(SHIPPED.size_guessed_big_band, 4)
        self.assertEqual((SHIPPED.per_company_daily, SHIPPED.per_company_gap_minutes,
                          SHIPPED.per_company_gap_jitter_minutes), (2, 60, 60))

    def test_a_size_picks_its_band(self):
        got = {size: pace.pace_for(size, False, SHIPPED).daily for size in (1, 100, 101, 200, 201, 500, 501, 1000,
                                                                            1001, 250000)}
        self.assertEqual(got, {1: 2, 100: 2, 101: 4, 200: 4, 201: 5, 500: 5, 501: 8, 1000: 8, 1001: 15, 250000: 15})

    def test_no_size_is_the_default_unless_the_company_looks_big(self):
        self.assertEqual(pace.pace_for(None, False, SHIPPED), pace.CompanyPace(2, 60, 60))
        self.assertEqual(pace.pace_for(None, True, SHIPPED), pace.CompanyPace(8, 30, 20))
        self.assertEqual(pace.pace_for(50, True, SHIPPED).daily, 2)  # a size on file wins over the guess


class BandConfigTest(unittest.TestCase):
    def refused(self, key, **changes):
        with self.assertRaises(ValueError) as cm:
            load_config(config_with(**changes))
        self.assertIn(key, str(cm.exception))

    def test_columns_must_line_up(self):
        self.refused("size_band_daily", size_band_daily="[2, 4, 5, 8]")
        self.refused("size_band_gap_minutes", size_band_gap_minutes="[60, 60]")
        self.refused("size_band_gap_jitter_minutes", size_band_gap_jitter_minutes="[60]")

    def test_bounds_must_increase(self):
        self.refused("size_band_up_to", size_band_up_to="[100, 100, 500, 1000]")
        self.refused("size_band_up_to", size_band_up_to="[0, 200, 500, 1000]")

    def test_a_daily_number_is_at_least_one_and_a_gap_zero_or_more(self):
        self.refused("size_band_daily", size_band_daily="[2, 4, 0, 8, 15]")
        self.refused("size_band_gap_minutes", size_band_gap_minutes="[60, 60, -1, 30, 20]")

    def test_the_guessed_band_must_exist(self):
        self.refused("size_guessed_big_band", size_guessed_big_band="6")
        self.refused("size_guessed_big_band", size_guessed_big_band="0")


class CompanyPacesTest(unittest.TestCase):
    def setUp(self):
        self.db = fresh_store()
        self.addCleanup(self.db.close)

    def paces(self, *ids):
        return pace.company_paces(self.db, set(ids), SHIPPED)

    def test_a_size_fact_sets_the_band(self):
        small, big = seed_company(self.db, "Small"), seed_company(self.db, "Big")
        evidence(self.db, small, "size", num_low=11, num_high=50)
        evidence(self.db, big, "size", num_low=5001, num_high=10000)
        got = self.paces(small, big)
        self.assertEqual((got[small].daily, got[big].daily), (2, 15))

    def test_a_size_on_a_company_merged_into_it_counts(self):
        survivor, dupe = seed_company(self.db, "Ember"), seed_company(self.db, "Ember Inc")
        self.db.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                        "WHERE company_id = ?", (survivor, dupe))
        evidence(self.db, dupe, "size", num_low=300, num_high=400)
        self.assertEqual(self.paces(survivor)[survivor].daily, 5)

    def test_a_workday_board_makes_a_company_with_no_size_a_guessed_big_one(self):
        cid = seed_company(self.db, "Enterprise")
        board(self.db, cid, ats="workday")
        self.assertEqual(self.paces(cid)[cid].daily, 8)

    def test_an_inactive_enterprise_board_is_no_sign(self):
        cid = seed_company(self.db, "Gone")
        board(self.db, cid, ats="oracle_orc", active=0)
        self.assertEqual(self.paces(cid)[cid].daily, 2)

    def test_many_open_us_postings_make_a_guessed_big_one(self):
        many, few = seed_company(self.db, "Many"), seed_company(self.db, "Few")
        for _ in range(51):
            decision(self.db, posting(self.db, many), us="yes")
        for _ in range(50):
            decision(self.db, posting(self.db, few), us="yes")
        got = self.paces(many, few)
        self.assertEqual((got[many].daily, got[few].daily), (8, 2))


class QueueUsesTheBandTest(unittest.TestCase):
    def setUp(self):
        self.db = fresh_store()
        self.addCleanup(self.db.close)
        self.n = 0
        put_day(self.db, date(2026, 11, 2), 200)

    def company(self, name, size):
        cid = seed_company(self.db, name)
        evidence(self.db, cid, "size", num_low=size, num_high=size)
        return cid

    def person(self, cid):
        self.n += 1
        return seed_email_person(self.db, cid, f"P{self.n}", f"p{self.n}@acme.co")

    def reason(self, message, now):
        day = ramp.open_day(self.db, now, CFG)
        return {v.message_id: v for v in queue.verdicts(self.db, now, CFG, day=day, gate=None)}[message].reason

    def test_a_small_company_stops_at_two_and_a_big_one_goes_on(self):
        small, big = self.company("Tiny", 40), self.company("Giant", 50000)
        for cid, n in ((small, 2), (big, 2)):
            for k in range(n):
                seed_sent(self.db, self.person(cid), at=ny(2, 5 + k))
        small_next = seed_message(self.db, self.person(small))
        big_next = seed_message(self.db, self.person(big))
        self.assertEqual(self.reason(small_next, MON), "per_company_limit")
        self.assertEqual(self.reason(big_next, MON), "ready")

    def test_a_big_company_stops_at_fifteen(self):
        big = self.company("Giant", 50000)
        for k in range(15):
            seed_sent(self.db, self.person(big), at=ny(2, 0, 2 * k))
        self.assertEqual(self.reason(seed_message(self.db, self.person(big)), MON), "per_company_limit")

    def test_the_gap_shrinks_with_size(self):
        small, big = self.company("Tiny", 40), self.company("Giant", 50000)
        last = MON - timedelta(minutes=25)
        seed_sent(self.db, self.person(small), at=last)
        seed_sent(self.db, self.person(big), at=last)
        self.assertEqual(self.reason(seed_message(self.db, self.person(small)), MON), "per_company_gap")
        self.assertEqual(self.reason(seed_message(self.db, self.person(big)), MON), "ready")

    def test_the_gap_for_a_band_has_its_own_jitter_and_floor(self):
        big = pace.CompanyPace(15, 20, 15)
        last = MON
        gap = human.company_gap(7, last, SHIPPED, big)
        self.assertGreaterEqual(gap, timedelta(minutes=20))
        self.assertLess(gap, timedelta(minutes=35))
        self.assertEqual(human.max_company_gap(SHIPPED), timedelta(minutes=120))


_WORDS = [f"w{n}" for n in range(4000)]


def _distinct_first_email(rng: random.Random, name: str, company: str, index: int) -> tuple[str, str]:
    """A first email whose words are drawn fresh for each person, so 30 colleagues never read as near-identical
    (the simulator's own pools separate only 4)."""
    words = " ".join(rng.sample(_WORDS, 60))
    return (f"Note {index} for {company}",
            f"Hi {name.split()[0]},\n\n{words}\n\nhttps://www.devanshuchicholikar.com\nhttps://example.com/{index}\n\n"
            "Thanks,\nDevanshu")


class BigCompanyDayTest(unittest.TestCase):
    @mock.patch("outreach.sending.simulate._first_email", _distinct_first_email)
    def test_a_big_company_gets_fifteen_first_emails_in_a_day(self):
        cfg = dataclasses.replace(SHIPPED, pick_pool=1, day_start_jitter_minutes=0)
        day = cal_days(date(2026, 11, 16), 1, cfg)[0]
        scenario = Scenario(start=day, days=1, companies=1, people_per_company=30, zones=("America/New_York",),
                            events=(Event(sim_ny(day, "07:00"), "approve", {"count": 30}),),
                            sizes={0: 20000})
        result = simulate(scenario, seed=5, cfg=cfg)
        self.addCleanup(result.close)
        firsts = [s for s in result.sends if s.seq == 0]
        self.assertEqual(len(firsts), 15)
        gaps = [(b.at - a.at) for a, b in zip(firsts, firsts[1:])]
        self.assertGreaterEqual(min(gaps), timedelta(minutes=20))


if __name__ == "__main__":
    unittest.main()
