"""Go ahead (#156), the plan side: `add_own_pick` adds a company to today's list as his own pick, or records the request
for the plan job; `build_plan` puts those requests first. Scratch store, no network."""
import json
import unittest
from datetime import date

from outreach.plan.build import build_plan
from outreach.plan.picks import OwnPick, OwnPickRefused, add_own_pick, own_pick_requests
from outreach.ranking.common import canonical_ids
from tests.plan_seed import seed_daily_entry, seed_outbound, seed_person, seed_scored_company
from tests.storetest import fresh_store

TUE = date(2026, 11, 10)
SAT, SUN, MON = date(2026, 11, 14), date(2026, 11, 15), date(2026, 11, 16)
NOW = "2026-11-10T15:14:00Z"


def count(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


class OwnPickCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def built(self):
        self.conn.execute("INSERT INTO daily_list(list_date, built_at, capacity) VALUES (?, '2026-11-10T13:00:00Z', 8)",
                          (TUE.isoformat(),))

    def add(self, company, **kw):
        return add_own_pick(self.conn, company, TUE, NOW, **kw)


class AddedTest(OwnPickCase):
    def test_a_built_plan_gets_a_row_at_the_next_free_rank(self):
        self.built()
        first = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        seed_daily_entry(self.conn, TUE, first, rank=1)
        other = seed_scored_company(self.conn, "Bolt", 70, ["level_role"])
        got = self.add(other)
        self.assertEqual((got.state, got.rank), ("added", 2))
        row = self.conn.execute("SELECT * FROM daily_list_entry WHERE company_id = ?", (other,)).fetchone()
        self.assertEqual((row["rank"], row["state"], row["channel"], row["person_id"]), (2, "proposed", "linkedin_request", None))
        self.assertEqual(count(self.conn, "SELECT rowid FROM daily_list_entry WHERE company_id = ?", other), got.entry_id)
        why = json.loads(row["why"])
        self.assertEqual(why["reason"], "You chose this posting on the job list.")
        self.assertEqual(why["lane"], "level_role")

    def test_the_posting_he_chose_leads_the_row(self):
        self.built()
        co = seed_scored_company(self.conn, "Acme", 80, ["level_role"], with_posting=True)
        chosen = self.conn.execute("SELECT posting_id FROM posting WHERE company_id = ?", (co,)).fetchone()[0]
        self.add(co, posting_id=chosen)
        self.assertEqual(count(self.conn, "SELECT posting_id FROM daily_list_entry WHERE company_id = ?", co), chosen)

    def test_a_company_with_no_score_is_still_added(self):
        self.built()
        co = self.conn.execute("INSERT INTO company(name, status_at, created_at) VALUES ('Bare', '2026-11-01T12:00:00Z', "
                               "'2026-11-01T12:00:00Z')").lastrowid
        self.assertEqual(self.add(co).state, "added")
        self.assertEqual(json.loads(self.conn.execute("SELECT why FROM daily_list_entry").fetchone()[0])["lane"],
                         "any_us_role")

    def test_a_merged_away_id_adds_the_survivor(self):
        self.built()
        survivor = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        dupe = seed_scored_company(self.conn, "Acme Inc", 10, [], status="removed")
        self.conn.execute("UPDATE company SET merged_into = ? WHERE company_id = ?", (survivor, dupe))
        self.add(dupe)
        self.assertEqual(count(self.conn, "SELECT company_id FROM daily_list_entry"), survivor)

    def test_a_not_now_cooldown_and_a_stale_wave_never_stop_it(self):
        self.built()
        co = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        self.conn.execute("INSERT INTO event(at, recorded_at, entity, entity_id, company_id, kind, actor, detail) "
                          "VALUES ('2026-11-09T15:00:00Z', '2026-11-09T15:00:00Z', 'company', ?, ?, 'pick_passed', "
                          "'devanshu', '{\"reason\": \"too_big\"}')", (co, co))
        person = seed_person(self.conn, co, "Ana Bell")
        seed_outbound(self.conn, person, co, "linkedin_request", date(2026, 10, 1))  # 40 days ago: no longer waiting
        self.assertEqual(self.add(co).state, "added")


class RefusedTest(OwnPickCase):
    def refused(self, company, words):
        with self.assertRaises(OwnPickRefused) as err:
            self.add(company)
        self.assertIn(words, str(err.exception))

    def test_a_no_go_company(self):
        self.built()
        co = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        self.conn.execute("INSERT INTO no_go(company_id, reason, source, created_at) VALUES (?, 'no_sponsorship', "
                          "'devanshu', '2026-11-01T12:00:00Z')", (co,))
        self.refused(co, "Acme is on your no-go list")

    def test_a_company_merged_into_a_no_go_company(self):
        self.built()
        survivor = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        dupe = seed_scored_company(self.conn, "Acme Inc", 10, [], status="removed")
        self.conn.execute("UPDATE company SET merged_into = ? WHERE company_id = ?", (survivor, dupe))
        self.conn.execute("INSERT INTO no_go(company_id, reason, source, created_at) VALUES (?, 'no_sponsorship', "
                          "'devanshu', '2026-11-01T12:00:00Z')", (survivor,))
        self.refused(dupe, "no-go list")

    def test_a_company_on_todays_list_in_any_state(self):
        self.built()
        co = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        seed_daily_entry(self.conn, TUE, co, rank=1)
        self.refused(co, "Acme is already on today's list.")
        self.conn.execute("UPDATE daily_list_entry SET state = 'removed', decided_at = '2026-11-10T14:00:00Z'")
        self.refused(co, "was on today's list earlier")

    def test_someone_contacted_and_waiting(self):
        self.built()
        co = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        person = seed_person(self.conn, co, "Ana Bell")
        seed_outbound(self.conn, person, co, "linkedin_request", date(2026, 11, 8))
        self.refused(co, "Acme is in flight")

    def test_a_refusal_writes_nothing(self):
        self.built()
        co = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        seed_daily_entry(self.conn, TUE, co, rank=1)
        before = count(self.conn, "SELECT count(*) FROM event")
        with self.assertRaises(OwnPickRefused):
            self.add(co)
        self.assertEqual((count(self.conn, "SELECT count(*) FROM event"), count(self.conn, "SELECT count(*) FROM daily_list_entry")),
                         (before, 1))


class RequestedTest(OwnPickCase):
    def test_before_the_plan_it_records_the_request_and_writes_no_list(self):
        co = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        self.assertEqual(self.add(co, key="own:1"), OwnPick("requested", None, None, TUE))
        self.assertEqual(count(self.conn, "SELECT count(*) FROM daily_list"), 0)
        row = self.conn.execute("SELECT * FROM event WHERE kind = 'own_pick_requested'").fetchone()
        self.assertEqual((row["entity"], row["company_id"], row["actor"], row["idempotency_key"]),
                         ("company", co, "devanshu", "own:1"))
        self.assertEqual(own_pick_requests(self.conn, TUE, canonical_ids(self.conn)), [(co, None)])
        self.assertEqual(own_pick_requests(self.conn, date(2026, 11, 11), canonical_ids(self.conn)), [])

    def test_a_second_request_for_the_same_company_is_refused(self):
        co = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        self.add(co)
        with self.assertRaises(OwnPickRefused) as err:
            self.add(co)
        self.assertIn("already asked for", str(err.exception))

    def test_the_build_puts_requests_first_and_keeps_the_rest(self):
        mine = seed_scored_company(self.conn, "Zed", 1, [], with_posting=True)  # no lane, lowest score
        ranked = [seed_scored_company(self.conn, f"Co{i}", 90 - i, ["level_role"]) for i in range(6)]
        self.add(mine)
        result = build_plan(self.conn, today=TUE, read_boards=False)
        self.assertEqual(result.status, "built")
        rows = self.conn.execute("SELECT company_id FROM daily_list_entry ORDER BY rank").fetchall()
        got = [r[0] for r in rows]
        self.assertEqual(got[0], mine)
        self.assertTrue(set(got[1:]) <= set(ranked) and len(got) > 1)
        self.assertEqual(len(set(got)), len(got))
        job = json.loads(self.conn.execute("SELECT summary FROM job_run WHERE job = 'plan'").fetchone()[0])
        self.assertEqual(job["own_rows"], 1)

    def test_a_saturday_and_a_sunday_request_waits_for_monday(self):
        for day, name in ((SAT, "Sat Co"), (SUN, "Sun Co")):
            with self.subTest(day=day):
                co = seed_scored_company(self.conn, name, 80, ["level_role"])
                got = add_own_pick(self.conn, co, day, NOW)
                self.assertEqual((got.state, got.rank, got.entry_id, got.list_date), ("requested", None, None, MON))
                self.assertEqual(count(self.conn, "SELECT json_extract(detail, '$.list_date') FROM event "
                                       "WHERE kind = 'own_pick_requested' AND company_id = ?", co), "2026-11-16")
                self.assertIn(co, [c for c, _ in own_pick_requests(self.conn, MON, canonical_ids(self.conn))])
                self.assertEqual(own_pick_requests(self.conn, day, canonical_ids(self.conn)), [])

    def test_a_weekday_request_names_its_own_day(self):
        co = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        self.assertEqual(self.add(co).list_date, TUE)

    def test_the_monday_build_keeps_a_weekend_request_first(self):
        mine = seed_scored_company(self.conn, "Zed", 1, [], with_posting=True)
        for i in range(6):
            seed_scored_company(self.conn, f"Co{i}", 90 - i, ["level_role"])
        add_own_pick(self.conn, mine, SAT, "2026-11-14T15:14:00Z")
        self.assertEqual(build_plan(self.conn, today=SAT, read_boards=False).status, "weekend")
        self.assertEqual(build_plan(self.conn, today=MON, read_boards=False).status, "built")
        self.assertEqual(count(self.conn, "SELECT company_id FROM daily_list_entry WHERE rank = 1 "
                                          "AND list_date = '2026-11-16'"), mine)

    def test_a_second_weekend_request_for_the_same_company_is_refused(self):
        co = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        add_own_pick(self.conn, co, SAT, NOW)
        with self.assertRaises(OwnPickRefused):
            add_own_pick(self.conn, co, SUN, NOW)

    def test_a_forced_weekend_list_that_is_built_still_takes_the_row_at_once(self):
        co = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        self.conn.execute("INSERT INTO daily_list(list_date, built_at, capacity) VALUES ('2026-11-14', "
                          "'2026-11-14T13:00:00Z', 8)")
        got = add_own_pick(self.conn, co, SAT, NOW)
        self.assertEqual((got.state, got.list_date), ("added", SAT))

    def test_the_built_plan_counts_his_own_picks_in_its_rows(self):
        mine = seed_scored_company(self.conn, "Zed", 1, [], with_posting=True)
        for i in range(6):
            seed_scored_company(self.conn, f"Co{i}", 90 - i, ["level_role"])
        self.add(mine)
        build_plan(self.conn, today=TUE, read_boards=False)
        total = count(self.conn, "SELECT count(*) FROM daily_list_entry")
        summary = json.loads(count(self.conn, "SELECT summary FROM job_run WHERE job = 'plan'"))
        built = json.loads(count(self.conn, "SELECT detail FROM event WHERE kind = 'built'"))
        self.assertEqual((summary["rows"], built["rows"], summary["own_rows"]), (total, total, 1))

    def test_a_dry_run_writes_nothing(self):
        co = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        self.add(co)
        build_plan(self.conn, today=TUE, read_boards=False, dry_run=True)
        self.assertEqual(count(self.conn, "SELECT count(*) FROM daily_list_entry"), 0)

    def test_a_second_build_does_not_stop_the_request_from_counting_once(self):
        co = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        self.add(co)
        build_plan(self.conn, today=TUE, read_boards=False)
        self.assertEqual(build_plan(self.conn, today=TUE, read_boards=False).status, "already_built")
        self.assertEqual(count(self.conn, "SELECT count(*) FROM daily_list_entry WHERE company_id = ?", co), 1)


if __name__ == "__main__":
    unittest.main()
