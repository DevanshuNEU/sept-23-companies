"""Cadence, backoff and due-board selection (issue #21, Task 10)."""
import dataclasses
import unittest

from outreach.crawl.boardstate import BoardRow, load_board
from outreach.crawl.cadence import (
    add_seconds, due_boards, error_backoff_s, last_new_age_s, low_yield, next_interval_s, plan_company_ids)
from outreach.crawl.config import load_crawl_config
from outreach.crawl.postings import source_key
from tests.ranking_store import decision, posting
from tests.storetest import fresh_store, seed_board, seed_company

CFG = load_crawl_config()
NOW = "2026-10-22T12:00:00Z"
DAY = 86400
SUPPORTED = frozenset({"greenhouse", "lever"})


def ago(seconds: int) -> str:
    return add_seconds(NOW, -seconds)


class IntervalTests(unittest.TestCase):
    def test_interval_is_floor_for_a_fresh_board(self):
        self.assertEqual(next_interval_s(0, None, False, CFG), 21600)
        self.assertEqual(next_interval_s(2 * DAY, None, False, CFG), 21600)

    def test_interval_grows_with_quiet_time(self):
        self.assertEqual(next_interval_s(14 * DAY, None, False, CFG), 151200)  # 42 hours

    def test_interval_is_capped_at_the_ceiling(self):
        self.assertEqual(next_interval_s(100 * DAY, None, False, CFG), 604800)

    def test_the_interval_is_a_whole_number_of_seconds(self):
        self.assertIsInstance(next_interval_s(14 * DAY + 5, None, False, CFG), int)

    def test_a_change_shrinks_the_interval(self):
        self.assertEqual(next_interval_s(40 * DAY, 3 * DAY, True, CFG), 129600)  # base 5 days, half of 3 days wins
        self.assertEqual(next_interval_s(40 * DAY, 3 * DAY, False, CFG), 5 * DAY)  # no change: base
        self.assertEqual(next_interval_s(40 * DAY, None, True, CFG), 5 * DAY)  # nothing to halve: base

    def test_a_change_never_goes_below_the_floor(self):
        self.assertEqual(next_interval_s(40 * DAY, 21600, True, CFG), 21600)
        self.assertEqual(next_interval_s(40 * DAY, 1, True, CFG), 21600)

    def test_a_change_never_raises_the_interval_above_base(self):
        self.assertEqual(next_interval_s(14 * DAY, 100 * DAY, True, CFG), 151200)

    def test_backoff_doubles_from_the_floor(self):
        self.assertEqual([error_backoff_s(n, CFG) for n in (1, 2, 3)], [21600, 43200, 86400])

    def test_backoff_stays_at_24_hours_after_the_third_error(self):
        # Ruling 5: 6, 12, 24 hours, then it stays 24. The ceiling (7 days) is for the change interval only.
        self.assertEqual([error_backoff_s(n, CFG) for n in (4, 8, 100000)], [86400, 86400, 86400])

    def test_backoff_treats_a_streak_below_one_as_one(self):
        self.assertEqual(error_backoff_s(0, CFG), 21600)

    def test_add_seconds_is_canonical_utc(self):
        self.assertEqual(add_seconds("2026-10-22T23:59:30Z", 45), "2026-10-23T00:00:15Z")
        self.assertEqual(add_seconds(NOW, -DAY), "2026-10-21T12:00:00Z")


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.n = 0

    def company(self, status="candidate", merged_into=None):
        self.n += 1
        c = seed_company(self.conn, f"Company {self.n}")
        if status == "closed":
            self.conn.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?", (c,))
        elif status == "removed":
            self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'test' WHERE company_id = ?", (c,))
        elif status != "candidate":
            self.conn.execute("UPDATE company SET status = ? WHERE company_id = ?", (status, c))
        if merged_into:
            self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'merged', merged_into = ? "
                              "WHERE company_id = ?", (merged_into, c))
        return c

    def board(self, company, *, ats="greenhouse", active=1, last_read=None, last_read_at=None, next_read_at=None):
        self.n += 1
        b = seed_board(self.conn, company, ats, f"token{self.n}")
        sets = {"active": active, "next_read_at": next_read_at}
        if last_read:
            sets.update(last_read=last_read, last_read_at=last_read_at, streak_since=last_read_at,
                        error_streak=1 if last_read == "error" else 0, empty_streak=1 if last_read == "empty" else 0)
        cols = ", ".join(f"{k} = ?" for k in sets)
        self.conn.execute(f"UPDATE board SET {cols} WHERE board_id = ?", (*sets.values(), b))
        return b

    def due(self, **kw):
        kw.setdefault("supported", SUPPORTED)
        kw.setdefault("plan_ids", ())
        kw.setdefault("limit", 100)
        return [b.board_id for b in due_boards(self.conn, NOW, cfg=CFG, **kw)]


class LastNewAgeTests(StoreTestCase):
    def test_last_new_age_uses_newest_first_seen_or_board_created(self):
        c = self.company()
        b = self.board(c)
        self.conn.execute("UPDATE board SET created_at = ? WHERE board_id = ?", (ago(10 * DAY), b))
        self.assertEqual(last_new_age_s(self.conn, load_board(self.conn, b), NOW), 10 * DAY)
        for i, seen in enumerate((ago(5 * DAY), ago(2 * DAY), ago(7 * DAY))):
            self.conn.execute(
                "INSERT INTO posting(company_id, board_id, source_key, title, first_seen_at, last_seen_at) "
                "VALUES (?, ?, ?, 'Engineer', ?, ?)", (c, b, source_key("greenhouse", "x", str(i)), seen, seen))
        self.assertEqual(last_new_age_s(self.conn, load_board(self.conn, b), NOW), 2 * DAY)

    def test_last_new_age_ignores_other_boards_and_closed_postings_still_count(self):
        c = self.company()
        b, other = self.board(c), self.board(c)
        self.conn.execute("UPDATE board SET created_at = ? WHERE board_id = ?", (ago(30 * DAY), b))
        self.conn.execute(
            "INSERT INTO posting(company_id, board_id, source_key, title, first_seen_at, last_seen_at) "
            "VALUES (?, ?, 'greenhouse:y:1', 'Engineer', ?, ?)", (c, other, ago(DAY), ago(DAY)))
        self.assertEqual(last_new_age_s(self.conn, load_board(self.conn, b), NOW), 30 * DAY)

    def test_last_new_age_is_never_negative(self):
        c = self.company()
        b = self.board(c)
        self.conn.execute("UPDATE board SET created_at = ? WHERE board_id = ?", (add_seconds(NOW, 60), b))
        self.assertEqual(last_new_age_s(self.conn, load_board(self.conn, b), NOW), 0)


class PlanTests(StoreTestCase):
    def entry(self, company, state, date="2026-10-22", rank=None):
        self.conn.execute("INSERT INTO daily_list(list_date, built_at, capacity) VALUES (?, ?, 10) ON CONFLICT DO NOTHING",
                          (date, NOW))
        self.n += 1
        decided = None if state == "proposed" else NOW
        self.conn.execute(
            "INSERT INTO daily_list_entry(list_date, company_id, list_group, rank, state, proposed_at, decided_at) "
            "VALUES (?, ?, 'level_role', ?, ?, ?, ?)", (date, company, rank or self.n, state, NOW, decided))

    def test_plan_company_ids_reads_todays_proposed_and_picked_only(self):
        a, b, c, d, e = (self.company() for _ in range(5))
        self.entry(a, "proposed")
        self.entry(b, "picked")
        self.entry(c, "backlog")
        self.entry(d, "removed")
        self.entry(e, "proposed", date="2026-10-21")
        self.assertEqual(plan_company_ids(self.conn, "2026-10-22"), frozenset({a, b}))
        self.assertEqual(plan_company_ids(self.conn, "2026-10-23"), frozenset())


class DueBoardTests(StoreTestCase):
    def test_returns_board_rows(self):
        b = self.board(self.company())
        (row,) = due_boards(self.conn, NOW, supported=SUPPORTED, plan_ids=(), cfg=CFG, limit=10)
        self.assertIsInstance(row, BoardRow)
        self.assertEqual(row, load_board(self.conn, b))

    def test_due_boards_filters(self):
        live = self.board(self.company())
        self.board(self.company(), active=0)
        self.board(self.company("closed"))
        self.board(self.company("removed"))
        keeper = self.company()
        self.board(self.company(merged_into=keeper))
        self.board(self.company(), ats="workday")  # outside `supported`
        working = self.board(self.company("working"))
        backlog = self.board(self.company("backlog"))
        self.assertEqual(sorted(self.due()), sorted([live, working, backlog]))

    def test_a_board_not_yet_due_is_left_out(self):
        self.board(self.company(), last_read="ok", last_read_at=ago(3600), next_read_at=add_seconds(NOW, 60))
        due = self.board(self.company(), last_read="ok", last_read_at=ago(DAY), next_read_at=NOW)
        self.assertEqual(self.due(), [due])

    def test_nothing_supported_or_no_company_filter_returns_nothing(self):
        self.board(self.company())
        self.assertEqual(self.due(supported=()), [])
        self.assertEqual(self.due(only_company_ids=()), [])

    def test_never_read_boards_come_before_due_ones(self):
        c = self.company()
        late = self.board(c, last_read="ok", last_read_at=ago(2 * DAY), next_read_at=ago(3600))
        early = self.board(c, last_read="ok", last_read_at=ago(5 * DAY), next_read_at=ago(2 * DAY))
        never = self.board(c)
        self.assertEqual(self.due(), [never, early, late])

    def test_plan_company_board_is_due_after_the_floor_even_if_next_read_is_later(self):
        plan = self.company()
        b = self.board(plan, last_read="ok", last_read_at=ago(7 * 3600), next_read_at=add_seconds(NOW, 3 * DAY))
        other = self.board(self.company(), last_read="ok", last_read_at=ago(7 * 3600),
                           next_read_at=add_seconds(NOW, 3 * DAY))
        self.assertEqual(self.due(plan_ids={plan}), [b])
        self.assertNotIn(other, self.due(plan_ids={plan}))

    def test_plan_company_board_read_an_hour_ago_is_not_forced(self):
        plan = self.company()
        self.board(plan, last_read="ok", last_read_at=ago(3600), next_read_at=add_seconds(NOW, 3 * DAY))
        self.assertEqual(self.due(plan_ids={plan}), [])

    def test_erroring_plan_board_keeps_its_backoff(self):
        plan = self.company()
        self.board(plan, last_read="error", last_read_at=ago(7 * 3600), next_read_at=add_seconds(NOW, 5 * 3600))
        self.assertEqual(self.due(plan_ids={plan}), [])
        backed_off_out = self.board(plan, last_read="error", last_read_at=ago(13 * 3600), next_read_at=ago(60))
        self.assertEqual(self.due(plan_ids={plan}), [backed_off_out])

    def test_plan_boards_are_listed_first(self):
        plan = self.company()
        other = self.company()
        late = self.board(other, last_read="ok", last_read_at=ago(2 * DAY), next_read_at=ago(2 * DAY))
        never_other = self.board(other)
        plan_late = self.board(plan, last_read="ok", last_read_at=ago(DAY), next_read_at=ago(60))
        plan_never = self.board(plan)
        self.assertEqual(self.due(plan_ids={plan}), [plan_never, plan_late, never_other, late])

    def test_limit_applies_after_ordering(self):
        plan = self.company()
        other = self.company()
        for _ in range(3):
            self.board(other)
        plan_board = self.board(plan, last_read="ok", last_read_at=ago(DAY), next_read_at=ago(60))
        self.assertEqual(self.due(plan_ids={plan}, limit=1), [plan_board])

    def test_force_ignores_next_read_at_but_not_the_floor(self):
        c = self.company()
        later = self.board(c, last_read="ok", last_read_at=ago(8 * 3600), next_read_at=add_seconds(NOW, 3 * DAY))
        self.board(c, last_read="ok", last_read_at=ago(3600), next_read_at=add_seconds(NOW, 3 * DAY))
        never = self.board(c)
        self.assertEqual(sorted(self.due(only_company_ids={c}, force=True)), sorted([later, never]))
        self.assertEqual(self.due(only_company_ids={c}), [never])

    def test_force_keeps_the_error_backoff(self):
        c = self.company()
        self.board(c, last_read="error", last_read_at=ago(8 * 3600), next_read_at=add_seconds(NOW, 4 * 3600))
        self.assertEqual(self.due(only_company_ids={c}, force=True), [])

    def test_only_company_ids_restricts_the_boards(self):
        a, b = self.company(), self.company()
        board_a = self.board(a)
        self.board(b)
        self.assertEqual(self.due(only_company_ids={a}), [board_a])

    def test_force_still_skips_closed_companies_and_inactive_boards(self):
        c = self.company("closed")
        self.board(c)
        d = self.company()
        self.board(d, active=0)
        self.assertEqual(self.due(only_company_ids={c, d}, force=True), [])

    def test_a_large_plan_does_not_hit_the_variable_limit(self):
        c = self.company()
        b = self.board(c)
        self.assertEqual(self.due(plan_ids=set(range(100000, 140000)) | {c}), [b])


class LowYieldTests(StoreTestCase):
    """`low_yield`: a board with 200 or more open postings, under 10% of them US engineering, and nothing new at his
    level in the last 30 days."""
    OLD = "2026-08-01T00:00:00Z"

    def fill(self, board, other=0, us_eng=0, **fields):
        """`us_eng` US engineering postings (first seen long ago unless `first_seen_at` is given) and `other`
        sales postings, all open on `board`, each with a decision."""
        c = self.conn.execute("SELECT company_id FROM board WHERE board_id = ?", (board,)).fetchone()[0]
        first = fields.pop("first_seen_at", self.OLD)
        for _ in range(us_eng):
            decision(self.conn, posting(self.conn, c, board_id=board, first_seen_at=first, last_seen_at=NOW), **fields)
        for _ in range(other):
            decision(self.conn, posting(self.conn, c, "Account Executive", board_id=board, first_seen_at=self.OLD,
                                        last_seen_at=NOW),
                     lane="not_engineering", level_tier="not_stated")

    def big(self, other=240, us_eng=10, **fields):
        b = self.board(self.company())
        self.fill(b, other, us_eng, **fields)
        return b

    def test_a_big_board_with_few_us_engineering_jobs_and_none_fresh_is_low_yield(self):
        self.assertTrue(low_yield(self.conn, self.big(), NOW, CFG))

    def test_small_board_never_low_yield(self):
        self.assertFalse(low_yield(self.conn, self.big(other=140, us_eng=10), NOW, CFG))  # 150 open

    def test_exactly_min_open_is_big_enough(self):
        self.assertTrue(low_yield(self.conn, self.big(other=190, us_eng=10), NOW, CFG))
        self.assertFalse(low_yield(self.conn, self.big(other=189, us_eng=10), NOW, CFG))

    def test_a_board_with_no_postings_is_not_low_yield(self):
        self.assertFalse(low_yield(self.conn, self.board(self.company()), NOW, CFG))

    def test_a_share_of_ten_percent_or_more_is_not_low_yield(self):
        self.assertFalse(low_yield(self.conn, self.big(other=180, us_eng=20), NOW, CFG))  # 20 of 200 = 10%
        self.assertTrue(low_yield(self.conn, self.big(other=181, us_eng=19), NOW, CFG))  # 19 of 200 = 9.5%

    def test_share_counts_us_engineering_only(self):
        # 30 engineering postings, 20 of them outside the US: 10 of 250 count, so the board is still low yield.
        b = self.big(other=220, us_eng=10)
        self.fill(b, us_eng=20, us="no")
        self.assertTrue(low_yield(self.conn, b, NOW, CFG))
        # The same 30 all in the US is 12%: not low yield.
        b = self.big(other=220, us_eng=10)
        self.fill(b, us_eng=20, us="unknown")
        self.assertFalse(low_yield(self.conn, b, NOW, CFG))

    def test_a_fresh_us_engineering_posting_at_his_level_restores_the_interval(self):
        b = self.big()
        self.fill(b, us_eng=1, first_seen_at=ago(DAY))  # new_grad, level_ok = 1
        self.assertFalse(low_yield(self.conn, b, NOW, CFG))

    def test_a_fresh_posting_with_no_stated_level_counts(self):
        for tier in ("not_stated", "open"):
            b = self.big()
            self.fill(b, us_eng=1, first_seen_at=ago(DAY), level_tier=tier)
            self.assertFalse(low_yield(self.conn, b, NOW, CFG), tier)

    def test_a_fresh_posting_that_is_not_his_level_does_not_count(self):
        b = self.big()
        self.fill(b, us_eng=1, first_seen_at=ago(DAY), level_tier="senior")
        self.assertTrue(low_yield(self.conn, b, NOW, CFG))

    def test_a_fresh_non_us_or_non_engineering_posting_does_not_count(self):
        b = self.big()
        self.fill(b, us_eng=1, first_seen_at=ago(DAY), us="no")
        self.fill(b, us_eng=1, first_seen_at=ago(DAY), lane="intern")
        self.assertTrue(low_yield(self.conn, b, NOW, CFG))

    def test_a_fit_posting_older_than_quiet_days_is_not_fresh(self):
        b = self.big()
        self.fill(b, us_eng=1, first_seen_at=ago(31 * DAY))
        self.assertTrue(low_yield(self.conn, b, NOW, CFG))
        self.fill(b, us_eng=1, first_seen_at=ago(29 * DAY))
        self.assertFalse(low_yield(self.conn, b, NOW, CFG))

    def test_closed_postings_and_other_boards_do_not_count(self):
        b = self.big(other=100, us_eng=10)  # 110 open
        c = self.conn.execute("SELECT company_id FROM board WHERE board_id = ?", (b,)).fetchone()[0]
        for _ in range(150):
            posting(self.conn, c, board_id=b, closed=True)
        self.fill(self.board(c), other=300)
        self.assertFalse(low_yield(self.conn, b, NOW, CFG))

    def test_an_undecided_posting_is_open_but_not_us_engineering(self):
        b = self.big(other=189, us_eng=10)
        c = self.conn.execute("SELECT company_id FROM board WHERE board_id = ?", (b,)).fetchone()[0]
        posting(self.conn, c, board_id=b, first_seen_at=ago(DAY), last_seen_at=NOW)  # no decision: nothing fresh at his level
        self.assertTrue(low_yield(self.conn, b, NOW, CFG))

    def test_only_the_current_ruleset_counts(self):
        b = self.big()
        self.conn.execute("INSERT INTO ruleset(ruleset_version, config_toml, created_at, is_current) "
                          "VALUES ('r-0000000000b2', '', ?, 0)", (NOW,))
        pid = self.conn.execute("SELECT max(posting_id) FROM posting WHERE board_id = ?", (b,)).fetchone()[0]
        self.conn.execute("UPDATE posting SET first_seen_at = ? WHERE posting_id = ?", (ago(DAY), pid))
        self.conn.execute("UPDATE decision SET ruleset_version = 'r-0000000000b2' WHERE posting_id = ?", (pid,))
        self.assertTrue(low_yield(self.conn, b, NOW, CFG))  # the fit row is under an old ruleset: undecided now

    def test_the_thresholds_come_from_the_config(self):
        b = self.big(other=100, us_eng=10)  # 110 open, 9%
        self.assertFalse(low_yield(self.conn, b, NOW, CFG))
        small = dataclasses.replace(CFG, big_board=dataclasses.replace(CFG.big_board, min_open=100))
        self.assertTrue(low_yield(self.conn, b, NOW, small))
        strict = dataclasses.replace(small, big_board=dataclasses.replace(small.big_board, max_share=0.05))
        self.assertFalse(low_yield(self.conn, b, NOW, strict))


class LowYieldDueTests(StoreTestCase):
    def test_plan_company_board_never_slowed(self):
        # A low-yield board of a company on today's list: next_read_at is a day out, yet the plan floor reads it.
        plan = self.company()
        b = self.board(plan, last_read="ok", last_read_at=ago(7 * 3600), next_read_at=add_seconds(NOW, DAY))
        for _ in range(250):
            decision(self.conn, posting(self.conn, plan, "Account Executive", board_id=b, first_seen_at=ago(60 * DAY)),
                     lane="not_engineering", level_tier="not_stated")
        self.assertTrue(low_yield(self.conn, b, NOW, CFG))
        self.assertEqual(self.due(plan_ids={plan}), [b])
        self.assertEqual(self.due(), [])  # a company off the plan waits the day


if __name__ == "__main__":
    unittest.main()


class TierTests(StoreTestCase):
    """#251: a single-request board is read every 15 minutes while jobs at his level appear there, every 2 hours
    otherwise, and once a day when nothing new has come for 90 days. Other ATSs keep the old rhythm (None)."""

    def tiered(self, board):
        from outreach.crawl.cadence import tier_wait_s
        return tier_wait_s(self.conn, load_board(self.conn, board), NOW, CFG)

    def job(self, board, days_ago, **dec):
        c = self.conn.execute("SELECT company_id FROM board WHERE board_id = ?", (board,)).fetchone()[0]
        pid = posting(self.conn, c, board_id=board, first_seen_at=ago(days_ago * DAY), last_seen_at=NOW)
        decision(self.conn, pid, **dec)
        return pid

    def test_a_fresh_job_at_his_level_makes_it_hot(self):
        b = self.board(self.company())
        self.job(b, 10, level_state="fit")
        self.assertEqual(self.tiered(b), 900)

    def test_level_not_stated_counts_as_his_level(self):
        b = self.board(self.company(), ats="ashby")
        self.job(b, 3, level_state="uncertain")
        self.assertEqual(self.tiered(b), 900)

    def test_a_senior_other_or_foreign_job_is_warm_not_hot(self):
        for dec in ({"level_state": "reject"}, {"level_state": "fit", "role": "other"},
                    {"level_state": "fit", "us": "no"}, {"level_state": "fit", "lane": "not_engineering"}):
            with self.subTest(dec=dec):
                b = self.board(self.company())
                self.job(b, 5, **dec)
                self.assertEqual(self.tiered(b), 7200)

    def test_nothing_new_for_90_days_is_dead(self):
        b = self.board(self.company())
        self.job(b, 120, level_state="fit")
        self.assertEqual(self.tiered(b), 86400)

    def test_a_new_board_with_no_postings_is_warm(self):
        b = self.board(self.company(), ats="lever")
        self.assertEqual(self.tiered(b), 7200)

    def test_workday_keeps_its_rhythm(self):
        b = self.board(self.company(), ats="workday")
        self.job(b, 1, level_state="fit")
        self.assertIsNone(self.tiered(b))

    def test_the_tier_settings(self):
        self.assertEqual((CFG.tiers.hot_s, CFG.tiers.warm_s, CFG.tiers.dead_s, CFG.tiers.hot_days,
                          CFG.tiers.dead_days), (900, 7200, 86400, 90, 90))
        self.assertIn("greenhouse", CFG.tiers.ats)
        self.assertNotIn("workday", CFG.tiers.ats)
