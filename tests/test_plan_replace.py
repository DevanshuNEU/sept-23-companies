"""`build_plan(..., replace=True)`: today's untouched plan rebuilt with the current config (#15). Scratch store only.

The old plan here is a people plan (`PEOPLE_ON`, as the 08:00 build of 2026-10-26 was); the replace runs with the
shipped config (company rows only). A replace refuses, writing nothing, as soon as he did anything on the list.
"""
import json
import unittest
from datetime import date

from outreach.outcomes.requests import record_request
from outreach.plan.build import ReplaceRefused, build_plan
from outreach.plan.config import default_plan_config
from outreach.plan.drafts import confirm_reason, save_linkedin_draft
from outreach.store import transaction
from outreach.store.writes import append_event, log_touch
from tests.plan_seed import (PEOPLE_ON, seed_daily_entry, seed_email_touch, seed_message, seed_outbound, seed_person,
                             seed_scored_company)
from tests.storetest import fresh_store

CFG = default_plan_config()
MON, TUE, WED, SAT = date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 14)
TEAM = ("team", "https://acme.example/team", "an engineer on the team")
ON_TUE = "2026-11-10T15:00:00Z"
CONTEXT = {"source": "plan", "lane": "level_role", "roleKind": None, "roleAgeDays": None, "rankerVersion": None,
           "signals": {}, "roleNamed": False}
# Every table a replace must leave alone, and the plan's own two.
OTHERS = ("outreach_touch", "linkedin_log", "application", "person", "person_source", "linkedin_draft", "touch",
          "message", "review_item", "company", "posting", "no_go", "company_score")


def count(conn, table, where="", args=()):
    return conn.execute(f"SELECT COUNT(*) FROM {table} {where}", args).fetchone()[0]


def everything(conn):
    """Every row of every table, as text: a refusal must leave this exactly as it was."""
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                         "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {t: rows(conn, t) for t in tables}


def rows(conn, table):
    """Every row of `table` in a stable order (some tables have no rowid)."""
    return sorted((tuple(r) for r in conn.execute(f"SELECT * FROM {table}")), key=repr)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.ids, self.people = [], {}
        for i in range(12):
            company = seed_scored_company(self.conn, f"Co{i:02}", 90 - i, ["level_role"])
            self.ids.append(company)
            if i in (1, 3, 5, 7, 9):
                self.people[company] = seed_person(self.conn, company, f"Person {i}", "engineer", sources=[TEAM])

    def old_plan(self, day=TUE):
        """The morning build with the old config: person rows first."""
        result = build_plan(self.conn, today=day, cfg=PEOPLE_ON)
        self.assertEqual(result.status, "built")
        rows = self.entries(day)
        self.assertTrue(any(r["person_id"] is not None for r in rows))
        return rows

    def entries(self, day=TUE):
        return self.conn.execute("SELECT rowid, * FROM daily_list_entry WHERE list_date = ? ORDER BY rank",
                                 (day.isoformat(),)).fetchall()

    def row_person(self):
        """A person named on one of today's old rows, and their company."""
        row = next(r for r in self.entries() if r["person_id"] is not None)
        return row["person_id"], row["company_id"]

    def replace(self, **kw):
        return build_plan(self.conn, today=kw.pop("today", TUE), replace=True, **kw)

    def refused(self, why: str, **kw):
        """The replace refuses with a reason that says `why`, and writes nothing at all."""
        before = everything(self.conn)
        with self.assertRaises(ReplaceRefused) as caught:
            self.replace(**kw)
        self.assertIn(why, str(caught.exception))
        self.assertEqual(everything(self.conn), before)
        self.assertFalse(self.conn.in_transaction)
        return caught.exception


class ReplaceTest(Fixture):
    def test_an_untouched_people_plan_becomes_a_company_plan_with_the_current_config(self):
        old = self.old_plan()
        result = self.replace(reason="the config switched to company rows")
        self.assertEqual(result.status, "built")
        self.assertEqual(result.replaced, len(old))
        self.assertEqual([r.company_id for r in result.old_rows], [r["company_id"] for r in old])
        rows = self.entries()
        self.assertEqual(len(rows), result.linkedin.today)
        self.assertEqual(result.capacity, result.linkedin.today)  # company rows only: no email slots
        self.assertEqual({(r["channel"], r["person_id"]) for r in rows}, {("linkedin_request", None)})
        self.assertTrue(all(r["state"] == "proposed" and r["revision"] == 1 for r in rows))
        self.assertEqual([r["company_id"] for r in rows], self.ids[:8])  # by score, nobody carried
        self.assertEqual(self.conn.execute("SELECT capacity FROM daily_list WHERE list_date = ?",
                                           (TUE.isoformat(),)).fetchone()[0], result.capacity)
        summary = json.loads(self.conn.execute("SELECT summary FROM job_run WHERE run_id = ?",
                                               (result.run_id,)).fetchone()[0])
        self.assertEqual((summary["replaced"], summary["people_rows"]), (len(old), 0))

    def test_the_event_says_how_many_rows_were_replaced_and_why(self):
        old = self.old_plan()
        result = self.replace(reason="the config switched to company rows")
        events = self.conn.execute("SELECT * FROM event WHERE kind = 'plan_replaced'").fetchall()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual((event["entity"], event["actor"]), ("daily_list", "system"))
        detail = json.loads(event["detail"])
        self.assertEqual(detail["date"], TUE.isoformat())
        self.assertEqual(detail["replaced_rows"], len(old))
        self.assertEqual(detail["reason"], "the config switched to company rows")
        self.assertEqual(detail["rows"], len(self.entries()))
        self.assertEqual(detail["run_id"], result.run_id)
        # the new plan's own built event is written too (the first one's key is taken)
        self.assertEqual(count(self.conn, "event", "WHERE entity = 'daily_list' AND kind = 'built'"), 2)

    def test_a_replace_with_no_reason_names_the_default(self):
        self.old_plan()
        self.replace()
        detail = json.loads(self.conn.execute("SELECT detail FROM event WHERE kind = 'plan_replaced'").fetchone()[0])
        self.assertEqual(detail["reason"], "rebuilt with the current config")

    def test_replacing_twice_replaces_the_plan_it_just_wrote(self):
        self.old_plan()
        first = self.replace()
        rows = self.entries()
        second = self.replace()
        self.assertEqual((second.status, second.replaced), ("built", len(rows)))
        self.assertEqual([r["company_id"] for r in self.entries()], [r["company_id"] for r in rows])
        self.assertEqual(count(self.conn, "event", "WHERE kind = 'plan_replaced'"), 2)
        self.assertNotEqual(first.run_id, second.run_id)

    def test_new_rows_never_take_an_old_row_id(self):
        # The page and the API name a row by its rowid: a stale tab must never reach a new row through an old id.
        old = {r["rowid"] for r in self.old_plan()}
        self.replace()
        new = {r["rowid"] for r in self.entries()}
        self.assertTrue(new)
        self.assertGreater(min(new), max(old))
        self.replace()
        newer = {r["rowid"] for r in self.entries()}
        self.assertGreater(min(newer), max(new))

    def test_nothing_outside_the_plan_rows_is_deleted_or_changed(self):
        self.old_plan()
        person, company = self.row_person()
        other = seed_person(self.conn, company, "Someone Else", "engineer", added_by="devanshu")
        seed_outbound(self.conn, other, self.ids[10], "linkedin_request", date(2026, 10, 1))
        self.conn.execute("INSERT INTO application(company_id, applied_on) VALUES (?, '2026-10-01')", (self.ids[11],))
        self.conn.execute("INSERT INTO review_item(kind, company_id, reason, evidence_key, raised_by, created_at) "
                          "VALUES ('size_unknown', ?, 'no size', 'k', 'rules', ?)", (company, ON_TUE))
        save_linkedin_draft(self.conn, person_id=person, body="Hi, a proposed note.", reason_line="a reason",
                            facts=[], saved_by="claude", cfg=CFG, banned=[])
        seed_daily_entry(self.conn, MON, self.ids[11], rank=1, state="removed")
        before = {t: rows(self.conn, t) for t in OTHERS}
        monday = [tuple(r) for r in self.conn.execute("SELECT * FROM daily_list_entry WHERE list_date < ?",
                                                     (TUE.isoformat(),))]
        events = [tuple(r) for r in self.conn.execute("SELECT * FROM event ORDER BY event_id")]
        self.replace()
        after = {t: rows(self.conn, t) for t in OTHERS}
        self.assertEqual(after, before)
        self.assertEqual([tuple(r) for r in self.conn.execute("SELECT * FROM daily_list_entry WHERE list_date < ?",
                                                              (TUE.isoformat(),))], monday)
        now = [tuple(r) for r in self.conn.execute("SELECT * FROM event ORDER BY event_id")]
        self.assertEqual(now[:len(events)], events)  # events are only added
        self.assertEqual(sorted(r["kind"] for r in self.conn.execute(
            "SELECT kind FROM event WHERE event_id > ?", (events[-1][0],))), ["built", "plan_replaced"])

    def test_dry_run_names_what_would_go_and_the_new_list_and_writes_nothing(self):
        old = self.old_plan()
        before = everything(self.conn)
        result = self.replace(dry_run=True, boards_reader=lambda c: self.fail("read"),
                              ranker=lambda conn: self.fail("rank"))
        self.assertEqual((result.status, result.run_id, result.replaced), ("built", None, len(old)))
        self.assertEqual([(r.rank, r.company_id, r.person_id) for r in result.old_rows],
                         [(r["rank"], r["company_id"], r["person_id"]) for r in old])
        self.assertEqual([c.company_id for c in result.chosen], self.ids[:8])
        self.assertTrue(all(c.person_id is None for c in result.chosen))
        self.assertEqual(everything(self.conn), before)

    def test_a_dry_run_refuses_too(self):
        self.old_plan()
        self.conn.execute("UPDATE daily_list_entry SET state = 'backlog', decided_at = ?, revision = 2 "
                          "WHERE list_date = ? AND rank = 1", (ON_TUE, TUE.isoformat()))
        self.refused("decision", dry_run=True)

    def test_a_weekend_is_still_a_weekend_unless_forced(self):
        before = everything(self.conn)
        self.assertEqual(self.replace(today=SAT).status, "weekend")
        self.assertEqual(everything(self.conn), before)
        build_plan(self.conn, today=SAT, cfg=PEOPLE_ON, force_weekend=True)
        result = self.replace(today=SAT, force_weekend=True)
        self.assertEqual(result.status, "built")
        self.assertGreater(result.replaced, 0)
        rows = self.entries(SAT)
        self.assertTrue(rows and all(r["person_id"] is None for r in rows))

    def test_with_no_plan_yet_a_replace_is_a_plain_build(self):
        result = self.replace()
        self.assertEqual((result.status, result.replaced, result.old_rows), ("built", None, ()))
        self.assertEqual(len(self.entries()), 8)
        self.assertEqual(count(self.conn, "event", "WHERE kind = 'plan_replaced'"), 0)

    def test_without_replace_an_existing_plan_is_still_already_built(self):
        self.old_plan()
        before = everything(self.conn)
        self.assertEqual(build_plan(self.conn, today=TUE).status, "already_built")
        self.assertEqual(everything(self.conn), before)

    def test_the_rank_hook_runs_as_in_a_normal_build_and_no_read_boards_skips_it(self):
        self.old_plan()
        calls = []
        self.replace(boards_reader=lambda c: calls.append("read"), ranker=lambda conn: calls.append("rank"))
        self.assertEqual(calls, ["read", "rank"])
        self.replace(read_boards=False, boards_reader=lambda c: self.fail("read"), ranker=lambda c: self.fail("rank"))


class CarryForwardTest(Fixture):
    def test_a_carried_row_that_was_not_acted_on_is_replaced_and_carried_again_from_yesterday(self):
        # Monday's plan proposed a person at Co11 (first proposed on Nov 5). Tuesday's 08:00 build moved Monday's row
        # to backlog and carried it. The replace deletes Tuesday's copy; the rebuild reads Monday's plan again (the
        # carry walks plans before today only), finds the backlog row, and carries it as its company with the same
        # dates.
        co11 = self.ids[11]
        person = seed_person(self.conn, co11, "Carried Person", "engineer", sources=[TEAM])
        seed_daily_entry(self.conn, MON, co11, rank=1, person_id=person, why={"first_proposed": "2026-11-05"})
        old = {r["company_id"]: r for r in self.old_plan()}
        self.assertEqual(old[co11]["person_id"], person)
        self.assertEqual(json.loads(old[co11]["why"])["carried_from"], MON.isoformat())
        monday = self.conn.execute("SELECT * FROM daily_list_entry WHERE list_date = ?", (MON.isoformat(),)).fetchone()
        self.assertEqual(monday["state"], "backlog")
        self.replace()
        rows = {r["company_id"]: r for r in self.entries()}
        self.assertIn(co11, rows)
        self.assertIsNone(rows[co11]["person_id"])  # a carried person row comes back as its company
        why = json.loads(rows[co11]["why"])
        self.assertEqual((why["carried_from"], why["first_proposed"]), (MON.isoformat(), "2026-11-05"))
        self.assertEqual(tuple(self.conn.execute("SELECT * FROM daily_list_entry WHERE list_date = ?",
                                                 (MON.isoformat(),)).fetchone()), tuple(monday))
        summary = json.loads(self.conn.execute("SELECT summary FROM job_run ORDER BY run_id DESC").fetchone()[0])
        self.assertEqual(summary["carried_rows"], 1)


class RefusalTest(Fixture):
    def setUp(self):
        super().setUp()
        self.old_plan()

    def test_a_logged_request_on_a_row(self):
        person, company = self.row_person()
        with transaction(self.conn):
            record_request(self.conn, person_id=person, company_id=company, sent_at=ON_TUE, recorded_at=ON_TUE,
                           context=CONTEXT, idempotency_key="row-request")
        self.conn.execute("UPDATE daily_list_entry SET revision = revision + 1 WHERE list_date = ? AND person_id = ?",
                          (TUE.isoformat(), person))
        self.refused("logged")

    def test_a_request_logged_without_the_row_moving(self):
        # the touch alone (the row's revision not moved) still refuses
        person, company = self.row_person()
        with transaction(self.conn):
            record_request(self.conn, person_id=person, company_id=company, sent_at=ON_TUE, recorded_at=ON_TUE,
                           context=CONTEXT, idempotency_key="row-request")
        self.refused("touch")

    def test_a_linkedin_log_row_alone(self):
        person, _ = self.row_person()
        self.conn.execute("INSERT INTO linkedin_log(person_id, kind, at, recorded_at) VALUES (?, 'note', ?, ?)",
                          (person, ON_TUE, ON_TUE))
        self.refused("LinkedIn log")

    def today(self, table, key, row_id):
        """Move a seeded row's created_at and updated_at onto Tuesday (the seeders write the real clock)."""
        cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
        sets = ", ".join(f"{c} = ?" for c in ("created_at", "updated_at") if c in cols)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE {key} = ?",
                          (*([ON_TUE] * sets.count("?")), row_id))

    def test_an_email_for_a_person_on_a_row(self):
        person, _ = self.row_person()
        touch = seed_email_touch(self.conn, person, None)
        message = seed_message(self.conn, touch, 0, "drafted")
        self.today("touch", "touch_id", touch)
        self.today("message", "message_id", message)
        self.refused("email")

    def test_an_email_draft_written_today_on_an_older_thread(self):
        person, _ = self.row_person()
        touch = seed_email_touch(self.conn, person, None)
        self.today("message", "message_id", seed_message(self.conn, touch, 0, "drafted"))
        self.refused("email")

    def test_an_email_to_someone_else_at_a_listed_company(self):
        _, company = self.row_person()
        someone = seed_person(self.conn, company, "Other Person", "engineer", email="other@co.example")
        self.today("touch", "touch_id", seed_email_touch(self.conn, someone, None))
        self.refused("email")

    def test_an_old_email_thread_does_not_refuse(self):
        _, company = self.row_person()
        someone = seed_person(self.conn, company, "Old Thread", "engineer", email="old@co.example")
        seed_message(self.conn, seed_email_touch(self.conn, someone, None), 0, "drafted")  # written on the real clock
        self.assertEqual(self.replace().status, "built")

    def test_an_application(self):
        company = self.entries()[-1]["company_id"]
        self.conn.execute("INSERT INTO application(company_id, applied_on) VALUES (?, ?)", (company, TUE.isoformat()))
        self.refused("application")

    def test_an_application_touch(self):
        company = self.entries()[-1]["company_id"]
        with transaction(self.conn):
            log_touch(self.conn, person_id=None, company_id=company, channel="application", sent_at=ON_TUE,
                      context=CONTEXT, idempotency_key="applied")
        self.refused("touch")

    def test_a_skip(self):
        # skip_row: the entry goes to backlog with decided_at and its revision moves on
        self.conn.execute("UPDATE daily_list_entry SET state = 'backlog', decided_at = ?, revision = revision + 1 "
                          "WHERE list_date = ? AND rank = 2", (ON_TUE, TUE.isoformat()))
        self.refused("decision")

    def test_a_carry_a_pick_or_a_remove(self):
        for state in ("backlog", "picked", "removed"):
            with self.subTest(state=state):
                self.conn.execute("UPDATE daily_list_entry SET state = ?, decided_at = ? WHERE list_date = ? "
                                  "AND rank = 3", (state, ON_TUE, TUE.isoformat()))
                self.refused("decision")
                self.conn.execute("UPDATE daily_list_entry SET state = 'proposed', decided_at = NULL "
                                  "WHERE list_date = ? AND rank = 3", (TUE.isoformat(),))

    def test_a_row_written_to_after_the_build(self):
        # a saved search or any step log moves the row's revision on
        self.conn.execute("UPDATE daily_list_entry SET revision = 2 WHERE list_date = ? AND rank = 4",
                          (TUE.isoformat(),))
        self.refused("written to")

    def test_a_note_he_saved(self):
        person, _ = self.row_person()
        save_linkedin_draft(self.conn, person_id=person, body="Hi, my own note.", reason_line="my reason", facts=[],
                            saved_by="devanshu", cfg=CFG, banned=[])
        self.refused("note")

    def test_a_confirmed_note(self):
        person, _ = self.row_person()
        draft = save_linkedin_draft(self.conn, person_id=person, body="Hi, a note.", reason_line="a reason",
                                    facts=[], saved_by="claude", cfg=CFG, banned=[])
        confirm_reason(self.conn, draft.draft_id, draft.revision, cfg=CFG, banned=[])
        self.refused("note")

    def test_a_proposed_note_by_claude_does_not_refuse(self):
        person, _ = self.row_person()
        save_linkedin_draft(self.conn, person_id=person, body="Hi, a proposed note.", reason_line="a reason",
                            facts=[], saved_by="claude", cfg=CFG, banned=[])
        self.assertEqual(self.replace().status, "built")
        self.assertEqual(count(self.conn, "linkedin_draft"), 1)  # the note is not the plan's: it stays

    def test_a_touch_today_at_a_listed_company_with_nobody_on_the_row(self):
        row = next(r for r in self.entries() if r["person_id"] is None)
        someone = seed_person(self.conn, row["company_id"], "Asked Today", "engineer")
        seed_outbound(self.conn, someone, row["company_id"], "linkedin_request", TUE)
        self.refused("touch")

    def test_a_touch_today_at_a_merged_away_duplicate_of_a_listed_company(self):
        row = next(r for r in self.entries() if r["person_id"] is None)
        now = ON_TUE
        dup = self.conn.execute("INSERT INTO company(name, status, remove_reason, status_at, created_at, merged_into) "
                                "VALUES ('Dup', 'removed', 'merged', ?, ?, ?)", (now, now, row["company_id"])).lastrowid
        someone = seed_person(self.conn, dup, "At The Duplicate", "engineer")
        seed_outbound(self.conn, someone, dup, "linkedin_request", TUE)
        self.refused("touch")

    def test_a_person_he_added_today_at_a_listed_company(self):
        row = next(r for r in self.entries() if r["person_id"] is None)
        self.today("person", "person_id",
                   seed_person(self.conn, row["company_id"], "Added By Him", "engineer", added_by="devanshu"))
        self.refused("added")

    def test_a_person_claude_added_today_does_not_refuse(self):
        row = next(r for r in self.entries() if r["person_id"] is None)
        self.today("person", "person_id", seed_person(self.conn, row["company_id"], "Found By Claude", "engineer"))
        self.assertEqual(self.replace().status, "built")

    def test_an_event_of_his_today_at_a_listed_company(self):
        row = self.entries()[0]
        append_event(self.conn, entity="company", entity_id=row["company_id"], company_id=row["company_id"],
                     kind="linkedin_search_saved", actor="devanshu", at=ON_TUE, detail={"url": "x"})
        self.refused("you did")

    def test_a_list_the_plan_job_did_not_build(self):
        # The lean Today's list (no channel on any row, no plan run for the date) is not the plan's to delete.
        self.conn.execute("DELETE FROM daily_list_entry WHERE list_date = ?", (TUE.isoformat(),))
        self.conn.execute("DELETE FROM daily_list WHERE list_date = ?", (TUE.isoformat(),))
        self.conn.execute("UPDATE job_run SET summary = json_set(summary, '$.date', '2026-01-01') WHERE job = 'plan'")
        seed_daily_entry(self.conn, TUE, self.ids[0], rank=1)
        self.refused("not built by the plan job")

    def test_a_plan_for_a_later_day(self):
        build_plan(self.conn, today=WED)
        self.refused("later")

    def test_a_request_logged_while_the_boards_were_read_is_caught_in_the_transaction(self):
        # The checks run first and again inside the write transaction, so a log that lands in between (here, during
        # the ranker hook) still refuses, and the old plan stays.
        person, company = self.row_person()
        old = [tuple(r) for r in self.entries()]

        def ranker(conn):
            with transaction(conn):
                record_request(conn, person_id=person, company_id=company, sent_at=ON_TUE, recorded_at=ON_TUE,
                               context=CONTEXT, idempotency_key="mid-build")

        with self.assertRaises(ReplaceRefused):
            self.replace(boards_reader=None, ranker=ranker)
        self.assertEqual([tuple(r) for r in self.entries()], old)
        self.assertEqual(count(self.conn, "event", "WHERE kind = 'plan_replaced'"), 0)
        self.assertFalse(self.conn.in_transaction)

    def test_a_row_added_while_the_boards_were_read_refuses(self):
        # The delete must take exactly the rows the checks saw: one more (or one fewer) refuses and keeps the plan.
        spare = self.ids[11]

        def ranker(conn):
            conn.execute("INSERT INTO daily_list_entry(list_date, company_id, list_group, rank, why, proposed_at) "
                         "VALUES (?, ?, 'level_role', 99, '{}', ?)", (TUE.isoformat(), spare, ON_TUE))

        before = [tuple(r) for r in self.entries()]
        with self.assertRaises(ReplaceRefused) as caught:
            self.replace(boards_reader=None, ranker=ranker)
        self.assertIn("changed while", str(caught.exception))
        self.assertEqual([tuple(r) for r in self.entries()][:-1], before)
        self.assertEqual(count(self.conn, "event", "WHERE kind = 'plan_replaced'"), 0)

    def test_a_touch_today_at_a_company_not_on_the_list_does_not_refuse(self):
        far = seed_scored_company(self.conn, "Elsewhere", 1, ["level_role"])
        someone = seed_person(self.conn, far, "Elsewhere Person", "engineer")
        seed_outbound(self.conn, someone, far, "linkedin_request", TUE)  # today, but not at a listed company
        self.assertEqual(self.replace().status, "built")


if __name__ == "__main__":
    unittest.main()
