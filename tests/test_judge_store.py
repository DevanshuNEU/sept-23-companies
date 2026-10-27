"""Judgments stored as events and read back in one set-based query (Part of #6 and #15)."""
import json
import unittest
from datetime import date

from outreach.judge.store import cache_key, pages_hash, posting_key, record_judgment
from outreach.ranking.common import canonical_ids
from outreach.ranking.judged import judge_line, load_judgments
from tests.storetest import fresh_store, seed_company

FIELDS = {"fit": 72, "builds": "Builds email inboxes for AI agents.", "domains": ["ai_agents"],
          "why": "Agent infrastructure is his work.", "risk": "No E-Verify statement.", "level_ok": "yes",
          "quotes": ["Acme builds email inboxes"], "evidence": [{"page": "home", "url": "https://a.example",
                                                                "quote": "Acme builds email inboxes"}], "thin": False}
PAGES = [{"kind": "home", "url": "https://a.example", "status": "ok", "text": "Acme builds email inboxes for agents."}]


class StoreAndRead(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()

    def record(self, cid, at, **over):
        return record_judgment(self.conn, cid, {**FIELDS, **over}, version="jv-1", key="k", posting="p",
                               pages=PAGES, at=at)

    def test_one_event_and_nothing_else(self):
        cid = seed_company(self.conn, "Acme")
        tables = ("evidence", "no_go", "decision", "touch", "linkedin_draft", "company_score", "review_item")
        before = {t: self.conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}
        self.record(cid, "2026-10-20T06:10:00Z")
        row = self.conn.execute("SELECT * FROM event WHERE kind = 'judged'").fetchone()
        self.assertEqual(("company", cid, cid, "claude"), (row["entity"], row["entity_id"], row["company_id"],
                                                          row["actor"]))
        detail = json.loads(row["detail"])
        self.assertEqual("judge", detail["by"])
        self.assertEqual("Judge: builds email inboxes for AI agents. Fit 72. Risk: no E-Verify statement.",
                         detail["text"])
        self.assertEqual(PAGES, detail["pages"])
        self.assertEqual(before, {t: self.conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables})

    def test_an_unusable_judgment_is_never_written(self):
        cid = seed_company(self.conn, "Acme")
        with self.assertRaises(ValueError):
            self.record(cid, None, fit=140)
        self.assertEqual(0, self.conn.execute("SELECT count(*) FROM event WHERE kind = 'judged'").fetchone()[0])

    def test_the_newest_judgment_of_each_company_wins(self):
        a, b = seed_company(self.conn, "A"), seed_company(self.conn, "B")
        self.record(a, "2026-10-10T06:10:00Z", fit=40)
        self.record(a, "2026-10-20T06:10:00Z", fit=80)
        self.record(b, "2026-10-15T06:10:00Z", fit=55)
        got = load_judgments(self.conn, canonical_ids(self.conn))
        self.assertEqual({a: 80, b: 55}, {cid: j.fit for cid, j in got.items()})
        self.assertEqual((), got[a].pages, "ranking never reads the page text")
        self.assertEqual(PAGES, list(load_judgments(self.conn, {}, with_pages=True)[a].pages))

    def test_a_merged_duplicate_counts_for_its_survivor(self):
        keep, dup = seed_company(self.conn, "Keep"), seed_company(self.conn, "Dup")
        self.record(keep, "2026-10-10T06:10:00Z", fit=40)
        self.record(dup, "2026-10-12T06:10:00Z", fit=90)
        self.conn.execute("UPDATE company SET merged_into = ?, status = 'removed', remove_reason = 'merged' "
                          "WHERE company_id = ?", (keep, dup))
        got = load_judgments(self.conn, canonical_ids(self.conn))
        self.assertEqual([keep], list(got))
        self.assertEqual(90, got[keep].fit)

    def test_old_judgments_are_left_out_by_age(self):
        cid = seed_company(self.conn, "Acme")
        self.record(cid, "2026-10-01T06:10:00Z")
        canon = canonical_ids(self.conn)
        self.assertIn(cid, load_judgments(self.conn, canon, today=date(2026, 10, 31), max_age_days=30))
        self.assertNotIn(cid, load_judgments(self.conn, canon, today=date(2026, 11, 1), max_age_days=30))

    def test_a_broken_row_is_skipped_not_trusted(self):
        cid = seed_company(self.conn, "Acme")
        self.conn.execute("INSERT INTO event(at, recorded_at, entity, entity_id, company_id, kind, actor, detail) "
                          "VALUES ('2026-10-20T06:10:00Z', '2026-10-20T06:10:00Z', 'company', ?, ?, 'judged', "
                          "'claude', ?)", (cid, cid, json.dumps({**FIELDS, "fit": "high"})))
        self.assertEqual({}, load_judgments(self.conn, canonical_ids(self.conn)))

    def test_the_line_says_not_judged_yet_with_no_judgment(self):
        self.assertEqual("Judge: not judged yet.", judge_line(None))

    def test_the_keys_change_with_what_was_read(self):
        self.assertEqual("", posting_key(None))
        self.assertNotEqual(posting_key({"title": "A", "url": "u"}), posting_key({"title": "B", "url": "u"}))
        other = [{**PAGES[0], "text": "Acme builds something else."}]
        self.assertNotEqual(pages_hash(PAGES), pages_hash(other))
        self.assertEqual(pages_hash(PAGES), pages_hash(list(reversed(PAGES))))
        self.assertNotEqual(cache_key("jv-1", "p", "h"), cache_key("jv-2", "p", "h"))


if __name__ == "__main__":
    unittest.main()
