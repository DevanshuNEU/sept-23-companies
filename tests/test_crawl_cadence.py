"""Cadence, backoff and due-board selection (issue #21, Task 10)."""
import unittest

from outreach.crawl.boardstate import BoardRow, load_board
from outreach.crawl.cadence import (
    add_seconds, due_boards, error_backoff_s, last_new_age_s, next_interval_s, plan_company_ids)
from outreach.crawl.config import load_crawl_config
from outreach.crawl.postings import source_key
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


if __name__ == "__main__":
    unittest.main()
