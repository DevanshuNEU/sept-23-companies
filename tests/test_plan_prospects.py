"""Task 9: prospects, carry-forward and the stored row explanation (#15)."""
import json
import unittest
from dataclasses import replace
from datetime import date

from outreach.plan.config import default_plan_config
from outreach.plan.outbound import load_outbound
from outreach.plan.prospects import (HANDOFF, WHY_KEYS, build_why, explanation, load_carried, load_postings,
                                     load_prospects)
from outreach.plan.select import Chosen
from outreach.ranking.common import canonical_ids
from outreach.ranking.scores import StoredScore, ranked_list
from outreach.store import transaction
from outreach.store.timefmt import utcnow
from tests.plan_seed import (TODAY, seed_daily_entry, seed_email_touch, seed_message, seed_outbound, seed_person,
                             seed_scored_company)
from tests.storetest import fresh_store

CFG = default_plan_config()
TZ = CFG.get("plan", "timezone")
MON, TUE = date(2026, 11, 9), date(2026, 11, 10)
FRI = date(2026, 11, 6)
EM = chr(0x2014)


def outbound(conn):
    return load_outbound(conn, canonical_ids(conn), tz=TZ)


def carried(conn, today):
    return load_carried(conn, today, CFG, outbound(conn))


def prospects(conn, today=TUE):
    return load_prospects(conn, ranked_list(conn, laned_only=True), canonical_ids(conn), today, CFG, outbound(conn))


class CarryTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()

    def test_carry_drops_done_rows(self):
        a = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        ada = seed_person(self.conn, a, "Ada")
        b = seed_scored_company(self.conn, "Bolt", 70, ["level_role"])
        bob = seed_person(self.conn, b, "Bob")
        c = seed_scored_company(self.conn, "Corp", 60, ["level_role"])
        seed_daily_entry(self.conn, MON, a, rank=1, person_id=ada)
        seed_daily_entry(self.conn, MON, b, rank=2)  # find someone
        seed_daily_entry(self.conn, MON, c, rank=3)  # find someone, stays open
        self.assertEqual(set(carried(self.conn, TUE)), {a, b, c})
        seed_outbound(self.conn, ada, a, "linkedin_request", MON)  # his request, logged on the row's day
        seed_outbound(self.conn, bob, b, "email", TUE)  # anyone at the company, after the row's day
        self.assertEqual(set(carried(self.conn, TUE)), {c})

    def test_a_request_before_the_rows_day_does_not_finish_it(self):
        a = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        ada = seed_person(self.conn, a, "Ada")
        seed_outbound(self.conn, ada, a, "linkedin_request", date(2026, 10, 1))
        seed_daily_entry(self.conn, MON, a, person_id=ada)
        self.assertEqual(set(carried(self.conn, TUE)), {a})

    def test_carry_keeps_unfinished_proposed_picked_and_backlog_rows(self):
        ids = [seed_scored_company(self.conn, f"Co{i}", 80 - i, ["level_role"]) for i in range(4)]
        for rank, (company, state) in enumerate(zip(ids, ("proposed", "picked", "backlog", "removed")), 1):
            seed_daily_entry(self.conn, MON, company, rank=rank, state=state)
        got = carried(self.conn, TUE)
        self.assertEqual(set(got), set(ids[:3]))
        self.assertTrue(all(row.carried_from == MON and row.first_proposed == MON for row in got.values()))

    def test_carry_reads_only_the_latest_earlier_plan(self):
        old = seed_scored_company(self.conn, "Old", 80, ["level_role"])
        recent = seed_scored_company(self.conn, "Recent", 70, ["level_role"])
        today_row = seed_scored_company(self.conn, "Today", 60, ["level_role"])
        seed_daily_entry(self.conn, date(2026, 10, 30), old)
        seed_daily_entry(self.conn, FRI, recent)
        seed_daily_entry(self.conn, MON, today_row)  # a plan for the day itself is not an earlier plan
        self.assertEqual(set(carried(self.conn, MON)), {recent})
        self.assertEqual(carried(self.conn, MON)[recent].carried_from, FRI)
        self.assertEqual(carried(self.conn, date(2026, 10, 30)), {})

    def test_an_empty_plan_in_between_does_not_drop_what_was_carried_before_it(self):
        old, kept = (seed_scored_company(self.conn, n, 80, ["level_role"]) for n in ("Old", "Kept"))
        day1, day2, day3 = date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)
        seed_daily_entry(self.conn, day1, kept, rank=1, state="backlog")
        seed_daily_entry(self.conn, day1, old, rank=2, why={"first_proposed": "2026-10-27"})
        self.conn.execute("INSERT INTO daily_list(list_date, built_at, capacity) VALUES (?, ?, 0)",
                          (day2.isoformat(), utcnow()))  # a zero-capacity plan: no entries
        got = carried(self.conn, day3)
        self.assertEqual(set(got), {kept})  # the row older than the roll limit is dropped
        self.assertEqual((got[kept].carried_from, got[kept].first_proposed), (day1, day1))
        self.assertEqual(set(carried(self.conn, day2)), {kept, old})  # the day before: still within the limit

    def test_a_plan_whose_rows_are_all_done_falls_back_to_the_one_before(self):
        a, b = (seed_scored_company(self.conn, n, 80, ["level_role"]) for n in ("A", "B"))
        pa = seed_person(self.conn, a, "Ada")
        seed_daily_entry(self.conn, date(2026, 11, 2), b, rank=1)
        seed_daily_entry(self.conn, date(2026, 11, 3), a, rank=1, person_id=pa)
        seed_outbound(self.conn, pa, a, "linkedin_request", date(2026, 11, 3))
        got = carried(self.conn, date(2026, 11, 4))
        self.assertEqual(set(got), {b})
        self.assertEqual(got[b].carried_from, date(2026, 11, 2))

    def test_carry_limit_is_weekdays_since_first_proposed(self):
        a = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        seed_daily_entry(self.conn, FRI, a, why={"first_proposed": "2026-11-02"})
        kept = carried(self.conn, MON)  # Nov 3 to Nov 9 holds 5 weekdays
        self.assertEqual(kept[a].first_proposed, date(2026, 11, 2))  # survives the second carry
        self.assertEqual(kept[a].carried_from, FRI)
        self.assertEqual(carried(self.conn, TUE), {})  # the sixth weekday

    def test_a_row_with_no_first_proposed_uses_its_plan_date(self):
        a = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        seed_daily_entry(self.conn, date(2026, 11, 2), a, why={"first_proposed": "not a date"})
        self.assertEqual(carried(self.conn, MON)[a].first_proposed, date(2026, 11, 2))

    def test_carried_row_for_a_now_ineligible_company_is_not_a_prospect(self):
        names = ["hidden", "closed", "removed", "paused", "nogo", "elsewhere", "fine"]
        ids = {n: seed_scored_company(self.conn, n.title(), 90 - i, ["level_role"],
                                      status={"closed": "closed", "removed": "removed", "paused": "working"}.get(
                                          n, "candidate"))
               for i, n in enumerate(names)}
        people = {n: seed_person(self.conn, ids[n], f"P {n}") for n in names}
        for rank, n in enumerate(names, 1):
            seed_daily_entry(self.conn, MON, ids[n], rank=rank, person_id=people[n])
        self.conn.execute("UPDATE company_score SET signals = json_set(signals, '$.hide', json('{\"kind\": \"x\"}')) "
                          "WHERE company_id = ?", (ids["hidden"],))
        self.conn.execute("UPDATE company SET paused_at = ? WHERE company_id = ?", (utcnow(), ids["paused"]))
        self.conn.execute("INSERT INTO no_go(company_id, reason, source, created_at) VALUES (?, 'said_no', "
                          "'devanshu', ?)", (ids["nogo"], utcnow()))
        seed_outbound(self.conn, people["elsewhere"], ids["elsewhere"], "linkedin_request", MON)
        got = {p.company_id: p for p in prospects(self.conn)}
        self.assertEqual(set(got) & {ids[n] for n in ("hidden", "closed", "removed", "paused", "nogo")}, set())
        self.assertIsNone(got[ids["elsewhere"]].carried)  # done elsewhere: not carried (and the company waits)
        self.assertEqual(got[ids["elsewhere"]].contact.status, "waiting")
        self.assertEqual(got[ids["fine"]].carried.person_id, people["fine"])


class ProspectTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()

    def test_prospects_follow_merges_and_skip_statuses(self):
        keep = {s: seed_scored_company(self.conn, s.title(), 90 - i, ["level_role"], status=s)
                for i, s in enumerate(("candidate", "backlog", "working", "closed", "removed"))}
        dup = seed_scored_company(self.conn, "Dup", 10, ["level_role"], status="removed")
        self.conn.execute("UPDATE company SET merged_into = ? WHERE company_id = ?", (keep["candidate"], dup))
        got = prospects(self.conn)
        self.assertEqual([p.company_id for p in got], [keep["candidate"], keep["backlog"], keep["working"]])
        self.assertEqual(len({p.company_id for p in got}), 3)

    def test_a_merged_away_company_with_a_person_gives_its_survivor_the_candidate(self):
        survivor = seed_scored_company(self.conn, "Survivor", 50, ["level_role"])
        dup = seed_scored_company(self.conn, "Dup", 60, ["level_role"], status="removed")
        self.conn.execute("UPDATE company SET merged_into = ? WHERE company_id = ?", (survivor, dup))
        pid = seed_person(self.conn, dup, "Dana", "engineer", sources=[("team", "https://acme.example/team", "Dana")])
        (p,) = prospects(self.conn)
        self.assertEqual((p.company_id, [c.person_id for c in p.candidates]), (survivor, [pid]))

    def test_position_is_one_based_in_ranked_order(self):
        ids = [seed_scored_company(self.conn, f"Co{i}", 90 - i, ["level_role"]) for i in range(3)]
        self.assertEqual([(p.company_id, p.position) for p in prospects(self.conn)],
                         [(ids[0], 1), (ids[1], 2), (ids[2], 3)])

    def test_small_needs_size_evidence(self):
        tiny = seed_scored_company(self.conn, "Tiny", 90, ["level_role"], size_high=10)
        big = seed_scored_company(self.conn, "Big", 80, ["level_role"], size_high=1000)
        unknown = seed_scored_company(self.conn, "Unknown", 70, ["level_role"])
        got = {p.company_id: p.small for p in prospects(self.conn)}
        self.assertEqual(got, {tiny: True, big: False, unknown: False})

    def test_scan_limit_bounds_the_work(self):
        with transaction(self.conn):
            for i in range(500):
                seed_scored_company(self.conn, f"Co{i:03}", 1000 - i, ["level_role"], with_posting=False)
        self.assertEqual(CFG.get("plan", "scan_limit"), 400)
        got = prospects(self.conn)
        self.assertEqual(len(got), 400)
        self.assertEqual(got[-1].position, 400)

    def test_a_queued_first_email_counts_as_contact_and_the_person_is_not_a_candidate(self):
        company = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        queued = seed_person(self.conn, company, "Quinn", "founder", email="quinn@acme.example",
                             sources=[("email", "https://acme.example/about", "quinn@acme.example")])
        other = seed_person(self.conn, company, "Olive", "engineer",
                            sources=[("team", "https://acme.example/team", "Olive")])
        seed_message(self.conn, seed_email_touch(self.conn, queued, None), 0, "drafted")
        (p,) = prospects(self.conn)
        self.assertEqual(p.contact.status, "waiting")
        self.assertEqual(p.contact.people_contacted, 1)
        self.assertEqual([c.person_id for c in p.candidates], [other])

    def test_a_company_without_scores_or_people_still_gets_a_prospect_with_no_candidates(self):
        company = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        (p,) = prospects(self.conn)
        self.assertEqual((p.company_id, p.candidates, p.carried), (company, (), None))

    def test_no_ranked_rows_means_no_prospects(self):
        self.assertEqual(prospects(self.conn), [])


class WhyTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.company = seed_scored_company(self.conn, "Acme", 80, ["level_role"])
        self.person = seed_person(self.conn, self.company, "Ada Lovelace", "engineer",
                                  sources=[("team", "https://acme.example/team", "Ada Lovelace, engineer")])
        self.conn.execute("UPDATE person SET title = ? WHERE person_id = ?", ("Staff Engineer", self.person))
        self.conn.execute(
            "UPDATE company_score SET signals = json_set(signals, '$.values', json(?)) WHERE company_id = ?",
            (json.dumps({"role_fit": {"points": 30.0, "reason": f"New grad role {EM} fits", "weight": 1.0},
                         "size": {"points": -5.0, "reason": "Mid size", "weight": 1.0},
                         "pay": {"points": 12.0, "reason": "Pays well", "weight": 1.0},
                         "age": {"points": 2.0, "reason": "Fresh", "weight": 1.0},
                         "zero": {"points": 0.0, "reason": "Nothing", "weight": 1.0}}), self.company))
        self.stored = ranked_list(self.conn, laned_only=True)[0]

    def build(self, chosen, *, posting=True, today=TUE, prospect=None):
        prospect = prospect or prospects(self.conn, today)[0]
        info = load_postings(self.conn, [self.stored.signals["best_posting_id"]], CFG).get(
            self.stored.signals["best_posting_id"]) if posting else None
        return build_why(prospect, chosen, self.stored, info, today=today, cfg=CFG, ranker_version="rk-test")

    @staticmethod
    def chosen(person=None, **kw):
        base = dict(company_id=1, channel="linkedin_request", person_id=person, group="role", list_group="level_role",
                    channel_reason="linkedin" if person else "find_someone", carried_from=None, first_proposed=None,
                    rotation_from=None)
        return Chosen(**{**base, **kw})

    def no_dashes(self, why):
        text = json.dumps(why, ensure_ascii=False)
        self.assertNotIn(chr(0x2013), text)
        self.assertNotIn(chr(0x2014), text)

    def test_why_has_exactly_the_documented_keys_and_no_dashes(self):
        person_row = self.build(self.chosen(self.person))
        self.assertEqual(set(person_row), set(WHY_KEYS))
        self.assertEqual(person_row["lane"], "level_role")
        self.assertEqual(person_row["person"]["person_id"], self.person)
        self.assertEqual(person_row["person"]["name"], "Ada Lovelace")
        self.assertEqual(person_row["person"]["fit"], "team")
        self.assertEqual(person_row["person"]["found_on"]["url"], "https://acme.example/team")
        self.assertEqual(person_row["person"]["found_on"]["label"], "acme.example")
        self.assertIsNone(person_row["find_someone"])
        self.assertEqual(person_row["first_proposed"], TUE.isoformat())
        self.assertEqual(person_row["score"], 80.0)
        self.assertEqual(person_row["ranker_version"], "rk-test")
        self.assertEqual(person_row["reason"], "Open new grad role: Software Engineer, posted 3 days ago")
        self.assertEqual([s["name"] for s in person_row["signals"]], ["role_fit", "pay", "size"])
        self.assertEqual(person_row["signals"][0], {"name": "role_fit", "reason": "New grad role - fits",
                                                    "points": 30.0})
        self.assertEqual(person_row["role"]["title"], "Software Engineer")
        self.assertEqual(person_row["role"]["age_days"], 0)
        self.assertEqual(person_row["reason_sources"], [{"label": "Posting", "url": person_row["role"]["url"]}])
        self.no_dashes(person_row)

        find_row = self.build(self.chosen())
        self.assertEqual(set(find_row), set(WHY_KEYS))
        self.no_dashes(find_row)

        carried_row = self.build(self.chosen(self.person, carried_from=MON, first_proposed=date(2026, 11, 3),
                                             rotation_from=77))
        self.assertEqual(set(carried_row), set(WHY_KEYS))
        self.assertEqual(carried_row["carried_from"], "2026-11-09")
        self.assertEqual(carried_row["first_proposed"], "2026-11-03")
        self.assertEqual(carried_row["rotation"], {"previous_person_id": 77})
        self.assertIsNone(person_row["rotation"])
        self.no_dashes(carried_row)
        self.assertEqual(json.loads(explanation(*self._args(), today=TUE, cfg=CFG, ranker_version="rk-test")), self.build(self.chosen(self.person)))

    def _args(self):
        pr = prospects(self.conn)[0]
        return (pr, self.chosen(self.person), self.stored, load_postings(
            self.conn, [self.stored.signals["best_posting_id"]], CFG)[self.stored.signals["best_posting_id"]])

    def test_find_someone_has_a_search_url_and_no_person(self):
        why = self.build(self.chosen())
        self.assertIsNone(why["person"])
        self.assertIsNone(why["draft_id"])
        self.assertEqual(why["find_someone"]["handoff"], HANDOFF)
        self.assertTrue(why["find_someone"]["search_url"].startswith(
            "https://www.linkedin.com/search/results/people/?keywords=Acme%20"))
        self.assertEqual(why["channel_reason"], "find_someone")

    def test_a_row_with_no_posting_has_no_role_and_no_sources(self):
        why = self.build(self.chosen(self.person), posting=False)
        self.assertIsNone(why["role"])
        self.assertEqual(why["reason_sources"], [])

    def test_a_stored_score_without_values_or_reasons_still_builds(self):
        bare = StoredScore(self.company, 1.0, ("level_role",), {"hide": None}, utcnow())
        pr = prospects(self.conn)[0]
        why = build_why(pr, self.chosen(), bare, None, today=TUE, cfg=CFG, ranker_version="rk-test")
        self.assertEqual((why["reason"], why["signals"], why["role"]), ("", [], None))
        self.assertEqual(set(why), set(WHY_KEYS))

    def test_the_posting_age_uses_the_posted_date_else_the_first_seen_date(self):
        posting = self.stored.signals["best_posting_id"]
        info = load_postings(self.conn, [posting], CFG)[posting]
        self.assertEqual(info.posted_on, TODAY)
        why = self.build(self.chosen(), today=date(2026, 11, 20))
        self.assertEqual(why["role"]["age_days"], 10)
        self.conn.execute("UPDATE posting SET posted_on = NULL WHERE posting_id = ?", (posting,))
        info = load_postings(self.conn, [posting], CFG)[posting]
        self.assertIsNone(info.posted_on)
        self.assertEqual(replace(info, posted_on=None).first_seen_on, info.first_seen_on)


if __name__ == "__main__":
    unittest.main()
