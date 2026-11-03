"""The pipeline scorecard (#153, Task 3): coverage, level recall, lead, detection lag and the gap list.

A fresh store and rows written by hand: nothing here reaches the network or the live store.
"""
import contextlib
import io
import itertools
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from outreach.install import install
from outreach.pipeline import scorecard
from outreach.pipeline import __main__ as pipeline_cli
from outreach.pipeline.scorecard import GapCompany
from outreach.pipeline.urls import board_key
from outreach.store import add_alias, connect
from tests.ranking_store import decision
from tests.storetest import fresh_store, seed_board, seed_company

NOW = "2026-11-03T12:00:00Z"
RECENT = "2026-10-25T00:00:00Z"   # inside the 30 day window
OLD = "2026-09-20T00:00:00Z"      # outside it
SEEN = "2026-10-26T10:00:00Z"     # when the feed first saw a listing

_ids = itertools.count(1)


def ashby(token, native):
    return f"https://jobs.ashbyhq.com/{token}/{native}"


def feed(conn, url, *, company="Acme", posted_hint=RECENT, first_seen=SEEN, active=1, backfill=0):
    """A feed_listing row as the poller writes it: the board key read from the link."""
    key = board_key(url)
    ats, token, native = (key.ats, key.token, key.native_id) if key else (None, None, None)
    n = next(_ids)
    return conn.execute(
        "INSERT INTO feed_listing(source, listing_id, url, ats, token, native_id, company_name, title, posted_hint, "
        "first_seen_at, last_seen_at, active, backfill) VALUES ('simplify', ?, ?, ?, ?, ?, ?, 'Software Engineer', "
        "?, ?, ?, ?, ?)", (f"L{n}", url, ats, token, native, company, posted_hint, first_seen, first_seen, active,
                           backfill)).lastrowid


def store_posting(conn, source_key, *, company_id=None, board_id=None, first_seen="2026-10-20T00:00:00Z",
                  posted_on=None, level=None):
    """A posting with this source key. `level` is None for no decision, else a dict of decision fields."""
    if company_id is None:
        company_id = seed_company(conn, f"Seeded {next(_ids)}")
    if board_id is None:  # the board the key names, made when the store has none
        ats, token, _ = source_key.split(":", 2)
        row = conn.execute("SELECT board_id FROM board WHERE ats = ? AND token = ?", (ats, token)).fetchone()
        board_id = row[0] if row else seed_board(conn, company_id, ats, token)
    pid = conn.execute(
        "INSERT INTO posting(company_id, board_id, source_key, title, posted_on, first_seen_at, last_seen_at) "
        "VALUES (?, ?, ?, 'Software Engineer', ?, ?, ?)",
        (company_id, board_id, source_key, posted_on, first_seen, first_seen)).lastrowid
    if level is not None:
        decision(conn, pid, **level)
    return pid


class ScorecardCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)


class ComputeTests(ScorecardCase):
    def test_scorecard_on_seeded_store(self):
        conn = self.conn
        acme = seed_company(conn, "Acme")
        seed_board(conn, acme, "ashby", "acme")
        levels = [dict(level_tier="new_grad", level_ok=1), dict(level_tier="swe1", level_ok=1),
                  dict(level_tier="not_stated"), dict(level_tier="senior")]
        for i, level in enumerate(levels, 1):  # four covered rows: 2 level_ok, 1 not_stated, 1 senior
            feed(conn, ashby("acme", f"a{i}"))
            store_posting(conn, f"ashby:acme:a{i}", company_id=acme, level=level)
        seed_company(conn, "Beta Labs, Inc.")  # Knock knows the company but has no board for the listing
        gamma = seed_company(conn, "Gamma")
        seed_board(conn, gamma, "lever", "gamma")
        for i in range(3):
            feed(conn, ashby("beta", f"b{i}"), company="Beta Labs")
        for i in range(2):
            feed(conn, f"https://jobs.lever.co/gamma/g{i}", company="Gamma")
        feed(conn, "https://careers.delta.example/jobs/9", company="Delta")  # no board key at all
        feed(conn, ashby("acme", "old"), posted_hint=OLD)    # outside the window
        feed(conn, ashby("acme", "gone"), active=0)           # left the feed
        feed(conn, ashby("acme", "nohint"), posted_hint=None)

        card = scorecard.compute(conn, NOW)

        self.assertEqual(card.simplify_listings, 10)
        self.assertEqual(card.simplify_coverage, 0.4)
        self.assertEqual(card.level_recall, 0.75)
        self.assertIsNone(card.date_truth)
        self.assertIsNone(card.small_company_coverage)
        self.assertEqual(card.gap_top, [
            GapCompany("Beta Labs", "ashby", 3, True, False),
            GapCompany("Gamma", "lever", 2, True, True),
            GapCompany("Delta", None, 1, False, False),
        ])

    def test_postings_with_no_decision_count_as_not_ok(self):
        feed(self.conn, ashby("acme", "a1"))
        feed(self.conn, ashby("acme", "a2"))
        store_posting(self.conn, "ashby:acme:a1", level=dict(level_tier="open"))  # open counts
        store_posting(self.conn, "ashby:acme:a2")                                  # no decision yet
        card = scorecard.compute(self.conn, NOW)
        self.assertEqual((card.simplify_coverage, card.level_recall), (1.0, 0.5))

    def test_a_decision_under_an_old_ruleset_is_not_current(self):
        feed(self.conn, ashby("acme", "a1"))
        store_posting(self.conn, "ashby:acme:a1", level=dict(level_tier="new_grad", level_ok=1))
        self.conn.execute("UPDATE ruleset SET is_current = 0")
        self.conn.execute("INSERT INTO ruleset(ruleset_version, config_toml, created_at, is_current) "
                          "VALUES ('r-0000000000b2', '', ?, 1)", (NOW,))
        self.assertEqual(scorecard.compute(self.conn, NOW).level_recall, 0.0)

    def test_case_kept_keys_match(self):
        # Workable shortcodes and Workday paths are stored in the case the board gave; the link has the same case.
        feed(self.conn, "https://apply.workable.com/arkham/j/852BD150EE")
        feed(self.conn, "https://acme.wd5.myworkdayjobs.com/External/job/Seattle-WA/Software-Engineer_R-1001")
        store_posting(self.conn, "workable:arkham:852BD150EE")
        store_posting(self.conn, "workday:acme.wd5/External:/job/Seattle-WA/Software-Engineer_R-1001")
        self.assertEqual(scorecard.compute(self.conn, NOW).simplify_coverage, 1.0)

    def test_workday_link_with_another_site_case_matches_the_stored_board(self):
        # The board keeps the site segment's case (`External`); a link may spell it `external`.
        company = seed_company(self.conn, "Acme")
        seed_board(self.conn, company, "workday", "acme.wd5/External")
        store_posting(self.conn, "workday:acme.wd5/External:/job/Seattle-WA/Software-Engineer_R-1001",
                      company_id=company)
        feed(self.conn, "https://acme.wd5.myworkdayjobs.com/external/job/Seattle-WA/Software-Engineer_R-1001")
        self.assertEqual(scorecard.compute(self.conn, NOW).simplify_coverage, 1.0)

    def test_has_board_ignores_the_case_of_the_token(self):
        company = seed_company(self.conn, "Acme")
        seed_board(self.conn, company, "workday", "acme.wd5/External")
        feed(self.conn, "https://acme.wd5.myworkdayjobs.com/external/job/Seattle-WA/Other_R-2")  # not stored yet
        (gap,) = scorecard.compute(self.conn, NOW).gap_top
        self.assertEqual((gap.ats, gap.knock_has_board), ("workday", True))

    def test_duplicate_greenhouse_number_takes_the_earliest_first_sight(self):
        feed(self.conn, "https://careers.acme.com/open?gh_jid=777")  # the feed first saw it on 2026-10-26
        store_posting(self.conn, "greenhouse:aaa:777", first_seen="2026-10-27T00:00:00Z")  # first key, seen later
        store_posting(self.conn, "greenhouse:bbb:777", first_seen="2026-10-20T00:00:00Z")
        self.assertEqual(scorecard.compute(self.conn, NOW).lead_over_simplify, 1.0)

    def test_gh_jid_row_matches_by_native_id(self):
        feed(self.conn, "https://careers.acme.com/open?gh_jid=4012345")
        feed(self.conn, "https://careers.acme.com/open?gh_jid=12345")  # 412345 and 9912345 are other jobs
        store_posting(self.conn, "greenhouse:acme:4012345", level=dict(level_ok=1))
        store_posting(self.conn, "greenhouse:acme:412345")
        store_posting(self.conn, "greenhouse:other:9912345")
        store_posting(self.conn, "lever:acme:12345")  # not a Greenhouse key
        card = scorecard.compute(self.conn, NOW)
        self.assertEqual(card.simplify_coverage, 0.5)
        self.assertEqual(card.level_recall, 1.0)

    def test_backfill_rows_do_not_count_toward_lead(self):
        conn = self.conn
        for name, first_seen, backfill in (("early", "2026-10-24T00:00:00Z", 0),   # Knock first: a lead
                                           ("late", "2026-10-28T00:00:00Z", 0),    # after the feed: no lead
                                           ("bf", "2026-10-24T00:00:00Z", 1)):     # backfill: left out
            feed(conn, ashby("acme", name), backfill=backfill)
            store_posting(conn, f"ashby:acme:{name}", first_seen=first_seen)
        card = scorecard.compute(conn, NOW)
        self.assertEqual(card.simplify_coverage, 1.0)  # a backfill row still counts as covered
        self.assertEqual(card.lead_over_simplify, 0.5)

    def test_only_backfill_rows_give_no_lead(self):
        feed(self.conn, ashby("acme", "bf"), backfill=1)
        store_posting(self.conn, "ashby:acme:bf")
        self.assertIsNone(scorecard.compute(self.conn, NOW).lead_over_simplify)

    def test_empty_ratios_are_none(self):
        card = scorecard.compute(self.conn, NOW)
        self.assertEqual(card.simplify_listings, 0)
        self.assertIsNone(card.simplify_coverage)
        self.assertIsNone(card.level_recall)
        self.assertIsNone(card.lead_over_simplify)
        self.assertIsNone(card.detection_p95_hours)
        self.assertEqual(card.gap_top, [])
        feed(self.conn, ashby("nobody", "x1"))  # listings, but none covered: coverage is a real 0
        card = scorecard.compute(self.conn, NOW)
        self.assertEqual(card.simplify_coverage, 0.0)
        self.assertIsNone(card.level_recall)
        self.assertIsNone(card.lead_over_simplify)

    def test_company_is_known_by_an_alias_and_another_token_is_not_its_board(self):
        company = seed_company(self.conn, "Initech Systems")
        add_alias(self.conn, company, "Initech Software", "name")
        seed_board(self.conn, company, "ashby", "initech")
        feed(self.conn, ashby("initech", "x1"), company="Initech Software")
        feed(self.conn, ashby("initech-eu", "x2"), company="Other Co")
        gap = {g.company_name: g for g in scorecard.compute(self.conn, NOW).gap_top}
        self.assertEqual(gap["Initech Software"], GapCompany("Initech Software", "ashby", 1, True, True))
        self.assertEqual(gap["Other Co"], GapCompany("Other Co", "ashby", 1, False, False))

    def test_gap_top_is_the_top_15_by_listings(self):
        for c in range(17):
            for i in range(c + 1):
                feed(self.conn, ashby(f"co{c}", f"j{i}"), company=f"Company {c:02d}")
        gap = scorecard.compute(self.conn, NOW).gap_top
        self.assertEqual(len(gap), 15)
        self.assertEqual([g.listings for g in gap], list(range(17, 2, -1)))
        self.assertEqual(gap[0].company_name, "Company 16")


class DetectionTests(ScorecardCase):
    def seed(self, ats, token, *, created, posted_on, hours, n):
        company = seed_company(self.conn, f"{ats} {token}")
        bid = self.conn.execute("INSERT INTO board(company_id, ats, token, created_at) VALUES (?, ?, ?, ?)",
                                (company, ats, token, created)).lastrowid
        midnight = datetime.strptime(posted_on, "%Y-%m-%d")
        seen = (midnight + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        store_posting(self.conn, f"{ats}:{token}:{n}", company_id=company, board_id=bid, first_seen=seen,
                      posted_on=posted_on)

    def test_detection_p95_on_watched_boards_only(self):
        old = "2026-10-01T00:00:00Z"
        # Twenty counted postings, 1 to 20 hours after midnight, over the three ATSs that give a posted date.
        for hours in range(1, 21):
            ats = ("greenhouse", "ashby", "lever")[hours % 3]
            self.seed(ats, f"watched{hours}", created=old, posted_on="2026-11-01", hours=hours, n=hours)
        # Left out: a board made 1 day before the date, one made exactly 2 days before, another ATS, a stale date.
        self.seed("ashby", "fresh1", created="2026-10-31T00:00:00Z", posted_on="2026-11-01", hours=500, n=1)
        self.seed("lever", "fresh2", created="2026-10-30T00:00:00Z", posted_on="2026-11-01", hours=500, n=2)
        self.seed("workable", "other", created=old, posted_on="2026-11-01", hours=500, n=3)
        self.seed("greenhouse", "stale", created=old, posted_on="2026-10-20", hours=500, n=4)
        card = scorecard.compute(self.conn, NOW)
        self.assertEqual(card.detection_p95_hours, 19.0)  # nearest rank: the 19th of 20

    def test_detection_p95_is_none_without_postings(self):
        self.seed("ashby", "stale", created="2026-10-01T00:00:00Z", posted_on="2026-10-20", hours=5, n=1)
        self.assertIsNone(scorecard.compute(self.conn, NOW).detection_p95_hours)


class MatchQueryTests(ScorecardCase):
    def test_key_query_searches_the_unique_index(self):
        sql = scorecard.POSTING_BY_KEY_SQL.format(marks="?, ?, ?")
        plan = " ".join(r["detail"] for r in self.conn.execute("EXPLAIN QUERY PLAN " + sql, ("a", "b", "c")))
        self.assertIn("SEARCH p USING", plan)
        self.assertNotIn("SCAN p", plan)


class RecordTests(ScorecardCase):
    def events(self):
        return self.conn.execute("SELECT * FROM event WHERE kind = 'pipeline_scorecard' ORDER BY event_id").fetchall()

    def test_record_once_a_day(self):
        feed(self.conn, ashby("acme", "a1"))
        store_posting(self.conn, "ashby:acme:a1")
        card = scorecard.compute(self.conn, NOW)
        scorecard.record(self.conn, card, NOW)
        scorecard.record(self.conn, card, "2026-11-03T20:00:00Z")  # the same New York day: nothing new
        (event,) = self.events()
        self.assertEqual((event["entity"], event["actor"], event["idempotency_key"]),
                         ("score", "system", "scorecard:2026-11-03"))
        detail = json.loads(event["detail"])
        self.assertEqual((detail["simplify_listings"], detail["simplify_coverage"]), (1, 1.0))
        self.assertIsNone(detail["date_truth"])

    def test_record_keys_by_the_new_york_day(self):
        card = scorecard.compute(self.conn, NOW)
        scorecard.record(self.conn, card, "2026-11-03T02:00:00Z")  # 9 pm on the 2nd in New York
        scorecard.record(self.conn, card, "2026-11-03T12:00:00Z")  # the 3rd
        self.assertEqual([e["idempotency_key"] for e in self.events()], ["scorecard:2026-11-02", "scorecard:2026-11-03"])


class CliTests(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp(prefix="outreach-pipeline-cli-"))
        self.addCleanup(shutil.rmtree, folder, True)
        self.db = str(folder / "outreach.db")
        install(self.db)
        env = mock.patch.dict(os.environ, {"OUTREACH_DB": self.db})
        env.start()
        self.addCleanup(env.stop)
        clock = mock.patch.object(pipeline_cli, "utcnow", lambda: NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.conn = connect(self.db)
        self.addCleanup(self.conn.close)
        feed(self.conn, ashby("acme", "a1"))
        feed(self.conn, ashby("acme", "a2"))
        store_posting(self.conn, "ashby:acme:a1", level=dict(level_ok=1))

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pipeline_cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def events(self):
        return self.conn.execute("SELECT count(*) FROM event WHERE kind = 'pipeline_scorecard'").fetchone()[0]

    def test_cli_json(self):
        code, out, err = self.run_cli("scorecard", "--json")
        self.assertEqual((code, err), (0, ""))
        card = json.loads(out)
        self.assertEqual((card["simplify_listings"], card["simplify_coverage"], card["level_recall"]), (2, 0.5, 1.0))
        self.assertEqual(card["gap_top"], [{"company_name": "Acme", "ats": "ashby", "listings": 1,
                                            "knock_knows_company": False, "knock_has_board": True}])  # a1's board
        self.assertEqual(self.events(), 0)  # printing stores nothing

    def test_cli_record_stores_the_card(self):
        code, out, _ = self.run_cli("scorecard", "--record")
        self.assertEqual(code, 0)
        self.assertIn("50%", out)
        self.assertEqual(self.events(), 1)
        self.run_cli("scorecard", "--record")
        self.assertEqual(self.events(), 1)

    def test_cli_text_names_every_number(self):
        _, out, _ = self.run_cli("scorecard")
        for label in ("Coverage", "Level recall", "Lead", "Detection p95", "Gap"):
            self.assertIn(label, out)
        for dash in ("—", "–"):
            self.assertNotIn(dash, out)


if __name__ == "__main__":
    unittest.main()
