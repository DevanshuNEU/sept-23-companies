"""Task 12: who the morning prepare step works on (#15). Scratch store, no network, no clock."""
import json
import unittest
from datetime import date, timedelta

from outreach.plan.config import default_plan_config
from outreach.plan.prepare_targets import Target, record_attempt, select_prepare_targets, target_count
from outreach.ranking.common import canonical_ids
from outreach.store.timefmt import utcnow
from tests.plan_seed import TODAY, seed_outbound, seed_person, seed_scored_company
from tests.storetest import fresh_store

CFG = default_plan_config()
TEAM = ("team", "https://acme.example/team", "an engineer on the team")


def ago(n: int) -> date:
    return TODAY - timedelta(days=n)


def at_noon(day: date) -> str:
    return f"{day.isoformat()}T16:00:00Z"  # 11:00 or 12:00 in New York


class TargetsCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)

    def targets(self, count=50, today=TODAY):
        return select_prepare_targets(self.conn, canonical_ids(self.conn), today, CFG, count=count)

    def ids(self, **kw):
        return [t.company_id for t in self.targets(**kw)]


class CountTest(unittest.TestCase):
    def test_count_is_twice_capacity_rounded_up(self):
        self.assertEqual(target_count(7, CFG), 14)
        self.assertEqual(target_count(0, CFG), 0)
        self.assertEqual(target_count(1, CFG), 2)
        self.assertEqual(target_count(-3, CFG), 0)


class SelectTest(TargetsCase):
    def test_targets_are_top_companies_without_a_person_in_rank_order(self):
        ids = [seed_scored_company(self.conn, f"Co{i:02}", 90 - i, ["level_role"]) for i in range(6)]
        seed_person(self.conn, ids[1], "Ada Lovelace", "engineer", sources=[TEAM])
        got = self.targets()
        self.assertEqual([t.company_id for t in got], [ids[0], ids[2], ids[3], ids[4], ids[5]])
        self.assertTrue(all(isinstance(t, Target) for t in got))
        self.assertEqual(self.ids(count=2), [ids[0], ids[2]])
        self.assertEqual(self.ids(count=0), [])
        first = got[0]
        self.assertEqual((first.name, first.lane), ("Co00", "level_role"))
        self.assertEqual(first.role["title"], "Software Engineer")
        self.assertEqual(set(first.role), {"posting_id", "title", "url", "age_days"})
        self.assertEqual(first.role["age_days"], 0)
        self.assertEqual(first.reasons, ("Open new grad role: Software Engineer, posted 3 days ago",))
        self.assertIsNone(first.post)
        self.assertEqual(first.known_people, ())
        self.assertIsNone(first.domain)

    def test_company_with_an_uncontacted_candidate_is_not_a_target(self):
        a = seed_scored_company(self.conn, "Acme", 90, ["level_role"])
        b = seed_scored_company(self.conn, "Bolt", 80, ["level_role"])
        seed_person(self.conn, a, "Ada Lovelace", "engineer", sources=[TEAM])
        self.assertEqual(self.ids(), [b])

    def test_contacted_candidate_does_not_count_so_the_company_can_be_a_target_when_stale(self):
        a = seed_scored_company(self.conn, "Acme", 90, ["level_role"])
        p = seed_person(self.conn, a, "Ada Lovelace", "engineer", sources=[TEAM])
        seed_outbound(self.conn, p, a, "linkedin_request", ago(20))  # no answer for 20 days: stale
        got = self.targets()
        self.assertEqual([t.company_id for t in got], [a])
        self.assertEqual(got[0].known_people, ("Ada Lovelace",))

    def test_waiting_engaged_and_laneless_companies_are_skipped(self):
        waiting = seed_scored_company(self.conn, "Waiting", 95, ["level_role"])
        engaged = seed_scored_company(self.conn, "Engaged", 94, ["level_role"])
        laneless = seed_scored_company(self.conn, "Laneless", 93, [])
        removed = seed_scored_company(self.conn, "Removed", 92, ["level_role"], status="removed")
        free = seed_scored_company(self.conn, "Free", 10, ["reason_no_role"])
        w = seed_person(self.conn, waiting, "Wai Ting", "engineer")
        seed_outbound(self.conn, w, waiting, "linkedin_request", ago(5))
        e = seed_person(self.conn, engaged, "En Gaged", "engineer")
        seed_outbound(self.conn, e, engaged, "linkedin_request", ago(40), accepted_on=ago(39), replied_on=ago(38))
        got = self.ids()
        self.assertEqual(got, [free])
        self.assertNotIn(laneless, got)
        self.assertNotIn(removed, got)

    def test_zero_result_attempt_is_not_retried_for_seven_days_but_is_on_day_eight(self):
        a = seed_scored_company(self.conn, "Acme", 90, ["level_role"])
        b = seed_scored_company(self.conn, "Bolt", 80, ["level_role"])
        record_attempt(self.conn, a, found=0, run_id=1, version="pv-test", at=at_noon(ago(6)))
        self.assertEqual(self.ids(), [b])  # day 7 counting the attempt's day as day 1
        self.assertEqual(self.ids(today=ago(6)), [b])  # the same day
        self.assertEqual(self.ids(today=TODAY + timedelta(days=1)), [a, b])  # day 8
        record_attempt(self.conn, b, found=0, run_id=2, version="pv-test", at=at_noon(ago(7)))
        self.assertEqual(self.ids(), [b])  # Bolt's attempt is on its day 8 today; Acme's is not

    def test_an_attempt_that_found_people_removes_the_company_from_targets_naturally(self):
        a = seed_scored_company(self.conn, "Acme", 90, ["level_role"])
        b = seed_scored_company(self.conn, "Bolt", 80, ["level_role"])
        record_attempt(self.conn, b, found=1, run_id=1, version="pv-test", at=at_noon(ago(1)))
        self.assertEqual(self.ids(), [a, b])  # an attempt that found someone never blocks by itself
        seed_person(self.conn, b, "Bo Lt", "engineer", sources=[TEAM])
        self.assertEqual(self.ids(), [a])  # the person it found does

    def test_payload_carries_the_hn_post_and_known_names(self):
        a = seed_scored_company(self.conn, "Acme", 90, ["level_role"])
        now = utcnow()
        self.conn.execute(
            "INSERT INTO evidence(company_id, fact, value, confidence, source_kind, recorded_at) "
            "VALUES (?, 'domain', 'acme.example', 'confirmed', 'website', ?)", (a, now))
        self.conn.execute("INSERT INTO source(source_name, version) VALUES ('hn', 'v1') ON CONFLICT DO NOTHING")
        long_text = "We are hiring. Email jobs@acme.example. " + "x" * 2000
        for url, posted, asks, text in (
                ("https://news.ycombinator.com/item?id=1", at_noon(ago(10)), 1, long_text),
                ("https://news.ycombinator.com/item?id=2", at_noon(ago(2)), 0, "quiet post"),
                ("https://news.ycombinator.com/item?id=3", at_noon(ago(200)), 1, "too old")):
            self.conn.execute("INSERT INTO company_post(company_id, source_name, url, posted_at, text, asks_email) "
                              "VALUES (?, 'hn', ?, ?, ?, ?)", (a, url, posted, text, asks))
        p = seed_person(self.conn, a, "Ada Lovelace", "engineer")
        seed_outbound(self.conn, p, a, "linkedin_request", ago(20))
        q = seed_person(self.conn, a, "Grace Hopper", "manager")
        seed_outbound(self.conn, q, a, "linkedin_request", ago(30))
        (t,) = self.targets()
        self.assertEqual(t.domain, "acme.example")
        self.assertEqual(t.post["url"], "https://news.ycombinator.com/item?id=1")
        self.assertEqual(len(t.post["text"]), 1500)
        self.assertTrue(t.post["text"].startswith("We are hiring."))
        self.assertEqual(t.known_people, ("Ada Lovelace", "Grace Hopper"))


class RecordTest(TargetsCase):
    def test_record_attempt_writes_one_event_per_run_and_company(self):
        a = seed_scored_company(self.conn, "Acme", 90, ["level_role"])
        record_attempt(self.conn, a, found=2, run_id=7, version="pv-abc")
        record_attempt(self.conn, a, found=2, run_id=7, version="pv-abc")  # the same run: no second row
        record_attempt(self.conn, a, found=0, run_id=8, version="pv-abc")
        rows = self.conn.execute("SELECT entity, entity_id, company_id, kind, actor, detail FROM event "
                                 "WHERE kind = 'prepare_attempt' ORDER BY event_id").fetchall()
        self.assertEqual([(r["entity"], r["entity_id"], r["company_id"], r["actor"]) for r in rows],
                         [("company", a, a, "claude")] * 2)
        self.assertEqual([json.loads(r["detail"]) for r in rows],
                         [{"found": 2, "run_id": 7, "version": "pv-abc"}, {"found": 0, "run_id": 8, "version": "pv-abc"}])


if __name__ == "__main__":
    unittest.main()
