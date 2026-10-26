"""Task 10: the plan job (#15). Scratch store, fake crawler and ranker hooks, no network."""
import json
import sys
import types
import unittest
from datetime import date
from unittest import mock

from outreach.plan import build as build_module
from outreach.plan.boards import BoardsReadError, crawler_boards_reader, rank_runner
from outreach.plan.build import build_plan
from outreach.plan.config import default_plan_config
from outreach.ranking.common import canonical_ids
from outreach.store import transaction
from outreach.store.timefmt import utcnow
from tests.plan_seed import PEOPLE_ON, TODAY, seed_daily_entry, seed_outbound, seed_person, seed_scored_company
from tests.storetest import fresh_store

CFG = default_plan_config()
MON, TUE, FRI, SAT = date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 6), date(2026, 11, 14)
TEAM = ("team", "https://acme.example/team", "an engineer on the team")


def count(conn, table, where=""):
    return conn.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0]


def snapshot(conn):
    return (count(conn, "daily_list"), count(conn, "daily_list_entry"), count(conn, "job_run", "WHERE job = 'plan'"), count(conn, "event"),
            conn.execute("SELECT group_concat(built_at) FROM daily_list").fetchone()[0])


class Fixture(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()

    def world(self, n=12, with_people=(1, 3, 5, 7, 9), lanes=("level_role",)):
        ids = []
        people = {}
        for i in range(n):
            company = seed_scored_company(self.conn, f"Co{i:02}", 90 - i, list(lanes))
            ids.append(company)
            if i in with_people:
                people[company] = seed_person(self.conn, company, f"Person {i}", "engineer", sources=[TEAM])
        return ids, people

    def history(self, n, day, *, company_name="History"):
        """`n` plan-sourced LinkedIn requests on `day` at one unscored company."""
        now = utcnow()
        company = self.conn.execute("INSERT INTO company(name, status_at, created_at) VALUES (?, ?, ?)",
                                    (company_name, now, now)).lastrowid
        with transaction(self.conn):
            for i in range(n):
                person = seed_person(self.conn, company, f"Past {i}")
                seed_outbound(self.conn, person, company, "linkedin_request", day)
        return company

    def entries(self, day=TUE):
        return self.conn.execute("SELECT * FROM daily_list_entry WHERE list_date = ? ORDER BY rank",
                                 (day.isoformat(),)).fetchall()


class BuildTest(Fixture):
    def test_builds_a_plan_with_people_and_find_someone_rows(self):
        ids, people = self.world()
        result = build_plan(self.conn, today=TUE, cfg=PEOPLE_ON)
        self.assertEqual(result.status, "built")
        self.assertEqual(result.boards, "absent")
        self.assertEqual(result.capacity, result.linkedin.today + result.email.slots)
        self.assertEqual(result.linkedin.today, 8)  # queue.companies_per_day, with the whole week's share left
        rows = self.entries()
        self.assertEqual(len(rows), 8)  # 12 companies, a queue of 8, nobody to email
        self.assertEqual(self.conn.execute("SELECT capacity FROM daily_list WHERE list_date = ?",
                                           (TUE.isoformat(),)).fetchone()[0], result.capacity)
        with_person = [r for r in rows if r["person_id"] is not None]
        self.assertEqual([r["rank"] for r in with_person], [1, 2, 3, 4])  # people rows first
        self.assertEqual([r["person_id"] for r in with_person], [people[ids[i]] for i in (1, 3, 5, 7)])
        self.assertTrue(all(r["channel"] == "linkedin_request" for r in rows))
        for r in rows[4:]:
            self.assertIsNone(r["person_id"])
            why = json.loads(r["why"])
            self.assertTrue(why["find_someone"]["search_url"].startswith("https://www.linkedin.com/search/"))
        first = json.loads(rows[0]["why"])
        self.assertEqual(first["person"]["person_id"], rows[0]["person_id"])
        self.assertEqual(first["first_proposed"], TUE.isoformat())
        self.assertEqual(rows[0]["posting_id"], first["role"]["posting_id"])
        runs = self.conn.execute("SELECT job, status FROM job_run WHERE job = 'plan'").fetchall()
        self.assertEqual([(r["job"], r["status"]) for r in runs], [("plan", "ok")])
        event = self.conn.execute("SELECT actor, kind, entity FROM event WHERE entity = 'daily_list'").fetchone()
        self.assertEqual((event["actor"], event["kind"]), ("daily_list", "built"))

    def test_company_first_build_has_no_person_and_no_email_rows(self):
        # The shipped config (plan.propose_people false): every row is a company row, the email slots are not used,
        # and nothing but the plan's own tables is written (no person, no note).
        ids, people = self.world()
        founder = seed_scored_company(self.conn, "Founded", 99, ["level_role"])
        seed_person(self.conn, founder, "Fay Founder", "founder", email="fay@founded.example", sources=[TEAM])
        seed_daily_entry(self.conn, MON, ids[1], rank=1, person_id=people[ids[1]], why={"first_proposed": "2026-11-05"})
        before = (count(self.conn, "person"), count(self.conn, "linkedin_draft"), count(self.conn, "outreach_touch"))
        result = build_plan(self.conn, today=TUE)
        self.assertEqual(result.status, "built")
        self.assertEqual(result.capacity, result.linkedin.today)  # no email slots: the plan writes no email rows
        rows = self.entries()
        self.assertEqual(len(rows), result.linkedin.today)
        self.assertEqual({(r["channel"], r["person_id"]) for r in rows}, {("linkedin_request", None)})
        self.assertIn(founder, [r["company_id"] for r in rows])  # the company still comes, as a company
        carried = {r["company_id"]: json.loads(r["why"]) for r in rows}[ids[1]]
        self.assertEqual((carried["carried_from"], carried["first_proposed"]), ("2026-11-09", "2026-11-05"))
        for r in rows:
            why = json.loads(r["why"])
            self.assertIsNone(why["person"])
            self.assertIsNone(why["draft_id"])
            self.assertTrue(why["find_someone"]["search_url"].startswith("https://www.linkedin.com/search/"))
        self.assertEqual((count(self.conn, "person"), count(self.conn, "linkedin_draft"),
                          count(self.conn, "outreach_touch")), before)
        summary = json.loads(self.conn.execute("SELECT summary FROM job_run WHERE job = 'plan'").fetchone()[0])
        self.assertEqual((summary["people_rows"], summary["email_rows"]), (0, 0))

    def test_second_run_changes_nothing(self):
        self.world()
        first = build_plan(self.conn, today=TUE)
        before = snapshot(self.conn)
        again = build_plan(self.conn, today=TUE)
        self.assertEqual((again.status, again.capacity, again.run_id), ("already_built", first.capacity, None))
        self.assertEqual(snapshot(self.conn), before)

    def test_weekend_builds_nothing_unless_forced(self):
        self.world()
        result = build_plan(self.conn, today=SAT)
        self.assertEqual((result.status, result.capacity, result.chosen), ("weekend", 0, ()))
        self.assertEqual(snapshot(self.conn)[:4], (0, 0, 0, 0))
        forced = build_plan(self.conn, today=SAT, force_weekend=True)
        self.assertEqual(forced.status, "built")
        self.assertGreater(count(self.conn, "daily_list_entry"), 0)

    def test_failure_midway_writes_nothing_and_a_rerun_builds(self):
        ids, _ = self.world()
        seed_daily_entry(self.conn, MON, ids[0], rank=1)
        with mock.patch.object(build_module, "append_event", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                build_plan(self.conn, today=TUE)
        self.assertEqual(count(self.conn, "daily_list", "WHERE list_date = '2026-11-10'"), 0)
        self.assertEqual(count(self.conn, "daily_list_entry", "WHERE list_date = '2026-11-10'"), 0)
        self.assertEqual(self.conn.execute("SELECT state FROM daily_list_entry WHERE list_date = '2026-11-09'"
                                           ).fetchone()[0], "proposed")
        run = self.conn.execute("SELECT status, error FROM job_run WHERE job = 'plan'").fetchone()
        self.assertEqual(run["status"], "failed")
        self.assertIn("disk full", run["error"])
        self.assertFalse(self.conn.in_transaction)
        again = build_plan(self.conn, today=TUE)
        self.assertEqual(again.status, "built")
        self.assertEqual(count(self.conn, "job_run", "WHERE job = 'plan' AND status = 'failed'"), 1)
        self.assertEqual(count(self.conn, "job_run", "WHERE job = 'plan' AND status = 'ok'"), 1)

    def test_old_proposed_rows_become_backlog_and_unfinished_ones_are_carried_with_their_date(self):
        ids, people = self.world(with_people=(0, 1))
        seed_daily_entry(self.conn, MON, ids[0], rank=1, person_id=people[ids[0]],
                         why={"first_proposed": "2026-11-05"})
        seed_daily_entry(self.conn, MON, ids[1], rank=2, person_id=people[ids[1]])
        seed_outbound(self.conn, people[ids[1]], ids[1], "linkedin_request", MON)  # done on the row's day
        build_plan(self.conn, today=TUE, cfg=PEOPLE_ON)
        old = {r["company_id"]: r for r in self.conn.execute(
            "SELECT * FROM daily_list_entry WHERE list_date = '2026-11-09'")}
        self.assertTrue(all(r["state"] == "backlog" and r["decided_at"] and r["revision"] == 2
                            for r in old.values()))
        today_rows = {r["company_id"]: r for r in self.entries()}
        carried = json.loads(today_rows[ids[0]]["why"])
        self.assertEqual((carried["carried_from"], carried["first_proposed"]), ("2026-11-09", "2026-11-05"))
        self.assertEqual(today_rows[ids[0]]["person_id"], people[ids[0]])
        self.assertNotIn(ids[1], today_rows)  # done: its company now waits, and the row is not carried
        self.assertTrue(all(r["state"] == "proposed" and r["decided_at"] is None for r in today_rows.values()))

    def test_a_heavy_week_cuts_the_queue_to_whole_waves(self):
        # #9 company first: min(8, floor(remaining / 4)). 130 plan requests leave 20, so 5 companies, not 8.
        self.world()
        self.history(130, date(2026, 11, 6))
        result = build_plan(self.conn, today=TUE)
        self.assertEqual((result.linkedin.remaining, result.linkedin.today, len(self.entries())), (20, 5, 5))
        self.assertEqual(self.conn.execute("SELECT capacity FROM daily_list").fetchone()[0], 5)

    def test_zero_capacity_is_a_finished_plan(self):
        self.world()
        self.history(150, date(2026, 11, 4))  # six days before Tuesday: the week's plan share is spent
        self.conn.execute("UPDATE sender SET paused = 1, pause_kind = 'by_you', paused_at = ?", (utcnow(),))
        result = build_plan(self.conn, today=TUE)
        self.assertEqual((result.status, result.capacity, result.chosen), ("built", 0, ()))
        self.assertEqual(self.conn.execute("SELECT capacity FROM daily_list WHERE list_date = '2026-11-10'"
                                           ).fetchone()[0], 0)
        self.assertEqual(self.entries(), [])
        self.assertEqual(self.conn.execute("SELECT status FROM job_run WHERE job = 'plan'").fetchone()[0], "ok")

    def test_monday_after_a_slept_friday_builds_from_the_last_plan_and_counts_the_week_correctly(self):
        ids, people = self.world(with_people=(0,))
        self.history(14, date(2026, 11, 3))
        self.history(5, date(2026, 11, 2), company_name="Older")  # seven days before Monday: out of the window
        seed_daily_entry(self.conn, FRI, ids[0], rank=1, person_id=people[ids[0]])
        # no plan was built on the days between: Friday's is the last
        result = build_plan(self.conn, today=MON, cfg=PEOPLE_ON)
        self.assertEqual((result.linkedin.used, result.linkedin.remaining), (14, 136))
        self.assertEqual(result.linkedin.today, 8)  # the queue: a missed day brings no catch-up
        top = self.conn.execute("SELECT person_id, why FROM daily_list_entry WHERE list_date = '2026-11-09' "
                                "AND rank = 1").fetchone()
        self.assertEqual(top["person_id"], people[ids[0]])
        self.assertEqual(json.loads(top["why"])["carried_from"], "2026-11-06")

    def test_every_entry_person_belongs_to_its_company_and_no_company_repeats(self):
        ids, _ = self.world(n=30, with_people=range(0, 30, 2))
        build_plan(self.conn, today=TUE)
        rows = self.entries()
        self.assertEqual(len({r["company_id"] for r in rows}), len(rows))
        canon = canonical_ids(self.conn)
        for r in rows:
            if r["person_id"] is not None:
                owner = self.conn.execute("SELECT company_id FROM person WHERE person_id = ?",
                                          (r["person_id"],)).fetchone()[0]
                self.assertEqual(canon[owner], r["company_id"])
        self.assertEqual(sorted(r["rank"] for r in rows), list(range(1, len(rows) + 1)))

    def test_entries_never_exceed_sixty(self):
        self.world(n=80, with_people=range(80))
        build_plan(self.conn, today=TUE)
        self.assertLessEqual(len(self.entries()), 60)
        self.assertLessEqual(self.conn.execute("SELECT capacity FROM daily_list").fetchone()[0], 60)

    def test_dry_run_writes_nothing(self):
        self.world()
        before = snapshot(self.conn)
        result = build_plan(self.conn, today=TUE, dry_run=True, boards_reader=lambda ids: self.fail("read"),
                            ranker=lambda conn: self.fail("rank"))
        self.assertEqual((result.status, result.run_id, result.boards), ("built", None, "skipped"))
        self.assertEqual(len(result.chosen), 8)
        self.assertEqual(snapshot(self.conn), before)
        self.assertEqual(count(self.conn, "daily_list_entry"), 0)

    def test_a_person_with_a_queued_email_keeps_the_company_out(self):
        from tests.plan_seed import seed_email_touch, seed_message
        ids, people = self.world(n=3, with_people=(0,))
        seed_message(self.conn, seed_email_touch(self.conn, people[ids[0]], None), 0, "drafted")
        build_plan(self.conn, today=TUE)
        self.assertNotIn(ids[0], {r["company_id"] for r in self.entries()})


class BoardsAndRankTest(Fixture):
    def test_boards_reader_gets_the_top_companies_and_its_failure_is_recorded_not_raised(self):
        ids, _ = self.world(n=40, with_people=())
        seen = []

        def reader(company_ids):
            seen.append(list(company_ids))
            raise BoardsReadError("exit 1")

        calls = []
        result = build_plan(self.conn, today=TUE, boards_reader=reader, ranker=lambda conn: calls.append(1))
        self.assertEqual(seen, [ids[:CFG.get("plan", "board_read_top_n")]])
        self.assertEqual((result.status, result.boards), ("built", "failed"))
        self.assertEqual(calls, [1])  # a failed read never decides whether rank runs
        summary = json.loads(self.conn.execute("SELECT summary FROM job_run WHERE job = 'plan'").fetchone()[0])
        self.assertEqual(summary["boards"], "failed")
        self.assertIn("exit 1", summary["boards_error"])
        self.assertEqual(self.conn.execute("SELECT status FROM job_run WHERE job = 'plan'").fetchone()[0], "ok")
        self.assertGreater(count(self.conn, "daily_list_entry"), 0)

    def test_a_reader_that_exits_the_process_is_recorded_too(self):
        self.world()

        def reader(company_ids):
            raise SystemExit("reading boards only runs from the live checkout")

        self.assertEqual(build_plan(self.conn, today=TUE, boards_reader=reader).boards, "failed")

    def test_absent_crawler_is_skipped(self):
        self.world()
        result = build_plan(self.conn, today=TUE, boards_reader=None)
        self.assertEqual((result.status, result.boards), ("built", "absent"))

    def test_rank_runs_when_the_crawler_is_absent(self):
        ids, _ = self.world(n=3, with_people=())
        calls = []

        def ranker(conn):
            calls.append(1)
            conn.execute("UPDATE company_score SET score = 500 WHERE company_id = ?", (ids[2],))

        result = build_plan(self.conn, today=TUE, boards_reader=None, ranker=ranker)
        self.assertEqual((calls, result.boards), ([1], "absent"))
        self.assertEqual(self.entries()[0]["company_id"], ids[2])  # the plan used the fresh scores
        summary = json.loads(self.conn.execute("SELECT summary FROM job_run WHERE job = 'plan'").fetchone()[0])
        self.assertEqual((summary["boards"], summary["rank"]), ("absent", "ok"))

    def test_rank_runs_after_a_failed_read(self):
        self.world()
        calls = []

        def reader(company_ids):
            raise RuntimeError("network down")

        result = build_plan(self.conn, today=TUE, boards_reader=reader, ranker=lambda conn: calls.append(1))
        self.assertEqual((calls, result.boards), ([1], "failed"))

    def test_a_partial_read_is_recorded_as_partial_and_rank_still_runs(self):
        self.world()
        calls = []

        def reader(company_ids):
            raise BoardsReadError("one board was refused", partial=True)

        result = build_plan(self.conn, today=TUE, boards_reader=reader, ranker=lambda conn: calls.append(1))
        self.assertEqual((calls, result.boards, result.status), ([1], "partial", "built"))
        summary = json.loads(self.conn.execute("SELECT summary FROM job_run WHERE job = 'plan'").fetchone()[0])
        self.assertEqual(summary["boards"], "partial")
        self.assertIn("one board was refused", summary["boards_error"])

    def test_a_raising_rank_does_not_stop_the_plan(self):
        self.world()

        def ranker(conn):
            raise RuntimeError("ranker broke")

        result = build_plan(self.conn, today=TUE, boards_reader=None, ranker=ranker)
        self.assertEqual(result.status, "built")
        self.assertEqual(len(self.entries()), 8)  # built on the stored scores
        self.assertFalse(self.conn.in_transaction)

    def test_ranker_runs_after_a_successful_board_read(self):
        ids, _ = self.world(n=3, with_people=())
        order = []

        def ranker(conn):
            order.append("rank")
            # the re-rank is what the plan uses: the last company becomes the best one
            conn.execute("UPDATE company_score SET score = 500 WHERE company_id = ?", (ids[2],))

        result = build_plan(self.conn, today=TUE, boards_reader=lambda c: order.append("read"), ranker=ranker)
        self.assertEqual((order, result.boards), (["read", "rank"], "read"))
        self.assertEqual(self.entries()[0]["company_id"], ids[2])
        summary = json.loads(self.conn.execute("SELECT summary FROM job_run WHERE job = 'plan'").fetchone()[0])
        self.assertEqual(summary["rank"], "ok")

    def test_a_ranker_failure_is_recorded_not_raised(self):
        self.world()

        def ranker(conn):
            raise RuntimeError("ranker broke")

        result = build_plan(self.conn, today=TUE, boards_reader=lambda c: None, ranker=ranker)
        self.assertEqual((result.status, result.boards), ("built", "read"))
        summary = json.loads(self.conn.execute("SELECT summary FROM job_run WHERE job = 'plan'").fetchone()[0])
        self.assertEqual(summary["rank"], "failed")
        self.assertIn("ranker broke", summary["rank_error"])

    def test_read_boards_false_skips_both(self):
        self.world()
        result = build_plan(self.conn, today=TUE, read_boards=False, boards_reader=lambda c: self.fail("read"),
                            ranker=lambda conn: self.fail("rank"))
        self.assertEqual((result.status, result.boards), ("built", "skipped"))


class HookTest(unittest.TestCase):
    def test_crawler_hook_calls_the_read_command(self):
        calls = []
        fake = types.SimpleNamespace(main=lambda argv: calls.append(argv) or 0)
        with mock.patch.dict(sys.modules, {"outreach.crawl.__main__": fake}):
            reader = crawler_boards_reader()
            reader([3, 9])
        self.assertEqual(calls, [["read", "--company", "3", "9"]])

    def test_absent_crawler_module_gives_none(self):
        with mock.patch.dict(sys.modules, {"outreach.crawl.__main__": None}):
            self.assertIsNone(crawler_boards_reader())

    def test_a_non_zero_return_or_a_refusal_raises_boards_read_error(self):
        with mock.patch.dict(sys.modules, {"outreach.crawl.__main__": types.SimpleNamespace(main=lambda argv: 1)}):
            with self.assertRaises(BoardsReadError) as caught:
                crawler_boards_reader()([3])
        self.assertTrue(caught.exception.partial)  # the command ran to its end, some board was not read

        def refuse(argv):
            raise SystemExit("reading boards only runs from the live checkout")

        with mock.patch.dict(sys.modules, {"outreach.crawl.__main__": types.SimpleNamespace(main=refuse)}):
            with self.assertRaises(BoardsReadError) as caught:
                crawler_boards_reader()([3])
        self.assertFalse(caught.exception.partial)

    def test_the_rank_runner_is_run_rank_when_it_imports(self):
        from outreach.ranking.job import run_rank
        self.assertIs(rank_runner(), run_rank)
        with mock.patch.dict(sys.modules, {"outreach.ranking.job": None}):
            self.assertIsNone(rank_runner())


if __name__ == "__main__":
    unittest.main()
