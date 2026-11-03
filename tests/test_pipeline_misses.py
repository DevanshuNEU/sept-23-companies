"""The miss log (#153, Task 4): why a link Devanshu pasted was not on his list, one first cause per link.

A fresh store and rows written by hand: nothing here reaches the network or the live store.
"""
import json
import unittest

from outreach.pipeline import misses
from outreach.pipeline.misses import CAUSES, CAUSE_TEXT, diagnose, log_miss
from tests.ranking_store import evidence
from tests.storetest import fresh_store, seed_board, seed_company
from tests.test_pipeline_scorecard import store_posting

NOW = "2026-11-03T12:00:00Z"
LATER = "2026-11-04T12:00:00Z"


def ashby(token, native):
    return f"https://jobs.ashbyhq.com/{token}/{native}"


class MissCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)


class DiagnoseTests(MissCase):
    def test_diagnose_each_cause(self):
        conn = self.conn
        acme = seed_company(conn, "Acme")
        board = seed_board(conn, acme, "ashby", "acme")
        found = store_posting(conn, "ashby:acme:ok-1", company_id=acme, board_id=board,
                              level=dict(level_tier="new_grad", level_ok=1))
        senior = store_posting(conn, "ashby:acme:senior-1", company_id=acme, board_id=board,
                               level=dict(level_tier="senior"))
        sales = store_posting(conn, "ashby:acme:sales-1", company_id=acme, board_id=board,
                              level=dict(lane="not_engineering"))
        citizen = store_posting(conn, "ashby:acme:citizen-1", company_id=acme, board_id=board,
                                level=dict(level_tier="new_grad", level_ok=1, citizenship=1))
        abroad = store_posting(conn, "ashby:acme:abroad-1", company_id=acme, board_id=board,
                               level=dict(level_tier="new_grad", level_ok=1, us="no"))
        seed_company(conn, "Bolt Robotics")  # a company with no board
        cases = [
            ("https://example.com/careers/42", "unknown_site", {}),
            (ashby("acme", "ok-1"), "found", dict(posting_id=found, company_id=acme, board_id=board)),
            (ashby("acme", "senior-1"), "read_wrong_level", dict(posting_id=senior, company_id=acme, board_id=board)),
            (ashby("acme", "sales-1"), "read_not_tech", dict(posting_id=sales, company_id=acme, board_id=board)),
            (ashby("acme", "citizen-1"), "hidden_visa", dict(posting_id=citizen, company_id=acme, board_id=board)),
            (ashby("acme", "abroad-1"), "not_us", dict(posting_id=abroad, company_id=acme, board_id=board)),
            (ashby("acme", "never-seen"), "not_read_yet", dict(company_id=acme, board_id=board)),
            ("https://jobs.ashbyhq.com/acme", "not_read_yet", dict(company_id=acme, board_id=board)),
            ("https://jobs.lever.co/boltrobotics/abc-123", "no_board", dict(company_id=conn.execute(
                "SELECT company_id FROM company WHERE name = 'Bolt Robotics'").fetchone()[0])),
            (ashby("nobody-we-know", "x-1"), "no_company", {}),
        ]
        for url, cause, ids in cases:
            with self.subTest(url=url):
                got = diagnose(conn, url)
                self.assertEqual(got.cause, cause)
                self.assertEqual((got.posting_id, got.company_id, got.board_id),
                                 (ids.get("posting_id"), ids.get("company_id"), ids.get("board_id")))
                self.assertIsInstance(got.detail, dict)
        self.assertEqual({c for _, c, _ in cases} | {"wrong_date"}, set(CAUSES))

    def test_found_means_the_job_passes_the_rules(self):
        conn = self.conn
        board = seed_board(conn, seed_company(conn, "Acme"), "ashby", "acme")
        variants = {"ok": {}, "abroad": dict(us="no"), "nospons": dict(sponsorship="no"),
                    "citizen": dict(citizenship=1), "student": dict(eligibility="excluded"),
                    "sales": dict(lane="not_engineering")}
        for name, extra in variants.items():
            store_posting(conn, f"ashby:acme:{name}", board_id=board,
                          level=dict(level_tier="new_grad", level_ok=1, **extra))
        for name in variants:
            got = diagnose(conn, ashby("acme", name))
            self.assertEqual(got.cause == "found", got.detail["passes"], name)
        self.assertEqual(diagnose(conn, ashby("acme", "ok")).cause, "found")

    def test_a_job_that_fails_for_two_reasons_keeps_the_specific_cause_over_not_us(self):
        conn = self.conn
        board = seed_board(conn, seed_company(conn, "Acme"), "ashby", "acme")
        store_posting(conn, "ashby:acme:both", board_id=board,
                      level=dict(level_tier="new_grad", level_ok=1, us="no", citizenship=1))
        self.assertEqual(diagnose(conn, ashby("acme", "both")).cause, "hidden_visa")

    def test_not_stated_and_open_levels_are_found(self):
        conn = self.conn
        board = seed_board(conn, seed_company(conn, "Acme"), "ashby", "acme")
        store_posting(conn, "ashby:acme:ns", board_id=board, level=dict(level_tier="not_stated"))
        store_posting(conn, "ashby:acme:open", board_id=board, level=dict(level_tier="open", level_ok=1))
        self.assertEqual(diagnose(conn, ashby("acme", "ns")).cause, "found")
        self.assertEqual(diagnose(conn, ashby("acme", "open")).cause, "found")

    def test_no_sponsorship_is_a_flag_so_the_job_is_found(self):
        conn = self.conn
        board = seed_board(conn, seed_company(conn, "Acme"), "ashby", "acme")
        store_posting(conn, "ashby:acme:nospons", board_id=board,
                      level=dict(level_tier="new_grad", level_ok=1, sponsorship="no"))
        got = diagnose(conn, ashby("acme", "nospons"))
        self.assertEqual((got.cause, got.detail["passes"], got.detail["sponsorship"]), ("found", True, "no"))

    def test_postings_the_rules_hide_for_other_reasons(self):
        conn = self.conn
        board = seed_board(conn, seed_company(conn, "Acme"), "ashby", "acme")
        store_posting(conn, "ashby:acme:nospons", board_id=board,
                      level=dict(level_tier="new_grad", level_ok=1, sponsorship="no"))
        store_posting(conn, "ashby:acme:abroad", board_id=board,
                      level=dict(level_tier="new_grad", level_ok=1, us="no"))
        store_posting(conn, "ashby:acme:student", board_id=board,
                      level=dict(level_tier="new_grad", level_ok=1, eligibility="excluded"))
        self.assertEqual(diagnose(conn, ashby("acme", "student")).cause, "read_wrong_level")
        abroad = diagnose(conn, ashby("acme", "abroad"))
        self.assertEqual((abroad.cause, abroad.detail["passes"], abroad.detail["us"]), ("not_us", False, "no"))

    def test_a_posting_with_no_decision_is_not_read_yet(self):
        conn = self.conn
        board = seed_board(conn, seed_company(conn, "Acme"), "ashby", "acme")
        pid = store_posting(conn, "ashby:acme:raw", board_id=board)
        got = diagnose(conn, ashby("acme", "raw"))
        self.assertEqual((got.cause, got.posting_id), ("not_read_yet", pid))

    def test_a_decision_under_an_old_ruleset_is_ignored(self):
        conn = self.conn
        board = seed_board(conn, seed_company(conn, "Acme"), "ashby", "acme")
        pid = store_posting(conn, "ashby:acme:old", board_id=board, level=dict(level_tier="new_grad", level_ok=1))
        conn.execute("INSERT INTO ruleset(ruleset_version, config_toml, created_at, is_current) VALUES ('r-0123456789ab', '', ?, 0)",
                     (NOW,))
        conn.execute("UPDATE ruleset SET is_current = 0")
        conn.execute("UPDATE ruleset SET is_current = 1 WHERE ruleset_version = 'r-0123456789ab'")
        self.assertEqual(diagnose(conn, ashby("acme", "old")).cause, "not_read_yet")
        self.assertEqual(pid, diagnose(conn, ashby("acme", "old")).posting_id)

    def test_a_closed_posting_is_still_matched(self):
        conn = self.conn
        board = seed_board(conn, seed_company(conn, "Acme"), "ashby", "acme")
        pid = store_posting(conn, "ashby:acme:gone", board_id=board, level=dict(level_tier="new_grad", level_ok=1))
        conn.execute("UPDATE posting SET closed_at = ?, close_reason = 'gone_from_board' WHERE posting_id = ?", (NOW, pid))
        got = diagnose(conn, ashby("acme", "gone"))
        self.assertEqual((got.cause, got.posting_id), ("found", pid))
        self.assertEqual(got.detail["closed_at"], NOW)

    def test_an_open_posting_has_a_null_closed_at_in_its_detail(self):
        conn = self.conn
        board = seed_board(conn, seed_company(conn, "Acme"), "ashby", "acme")
        store_posting(conn, "ashby:acme:open", board_id=board, level=dict(level_tier="new_grad", level_ok=1))
        got = diagnose(conn, ashby("acme", "open"))
        self.assertIn("closed_at", got.detail)
        self.assertIsNone(got.detail["closed_at"])

    def test_the_link_is_matched_in_any_letter_case(self):
        conn = self.conn
        board = seed_board(conn, seed_company(conn, "Acme"), "ashby", "acme")
        pid = store_posting(conn, "ashby:acme:abc-def", board_id=board, level=dict(level_tier="new_grad", level_ok=1))
        got = diagnose(conn, "https://jobs.ashbyhq.com/ACME/ABC-DEF")
        self.assertEqual((got.cause, got.posting_id), ("found", pid))

    def test_workday_site_case_differs_from_the_link(self):
        conn = self.conn
        acme = seed_company(conn, "Acme")
        board = seed_board(conn, acme, "workday", "acme.wd5/External")
        key = "workday:acme.wd5/External:/job/Boston/Engineer_R-1"
        pid = store_posting(conn, key, company_id=acme, board_id=board, level=dict(level_tier="new_grad", level_ok=1))
        got = diagnose(conn, "https://acme.wd5.myworkdayjobs.com/external/job/Boston/Engineer_R-1")
        self.assertEqual((got.cause, got.posting_id, got.board_id), ("found", pid, board))
        # a job the board does not hold: the board is found by its case-insensitive token
        other = diagnose(conn, "https://acme.wd5.myworkdayjobs.com/external/job/Boston/Other_R-2")
        self.assertEqual((other.cause, other.board_id), ("not_read_yet", board))

    def test_a_bare_greenhouse_job_id_on_a_company_site_matches_by_native_id(self):
        conn = self.conn
        board = seed_board(conn, seed_company(conn, "Acme"), "greenhouse", "acme")
        pid = store_posting(conn, "greenhouse:acme:7654321", board_id=board,
                            level=dict(level_tier="new_grad", level_ok=1))
        got = diagnose(conn, "https://acme.example/careers/open?gh_jid=7654321")
        self.assertEqual((got.cause, got.posting_id), ("found", pid))

    def test_a_bare_greenhouse_job_id_nobody_holds_falls_to_the_domain(self):
        conn = self.conn
        acme = seed_company(conn, "Acme")
        evidence(conn, acme, "domain", value="acme.example", confidence="confirmed", source_kind="website")
        got = diagnose(conn, "https://careers.acme.example/open?gh_jid=111")
        self.assertEqual((got.cause, got.company_id), ("no_board", acme))
        self.assertEqual(diagnose(conn, "https://careers.other.example/open?gh_jid=111").cause, "no_company")

    def test_no_board_by_company_name_from_the_token(self):
        conn = self.conn
        cid = seed_company(conn, "Retell AI, Inc.")
        got = diagnose(conn, "https://boards.greenhouse.io/retellai/jobs/123")
        self.assertEqual((got.cause, got.company_id), ("no_board", cid))

    def test_a_merged_company_answers_as_the_company_it_was_merged_into(self):
        conn = self.conn
        keep = seed_company(conn, "Acme")
        gone = seed_company(conn, "Acme Labs")
        conn.execute("UPDATE company SET status = 'removed', remove_reason = 'duplicate', merged_into = ? "
                     "WHERE company_id = ?", (keep, gone))
        got = diagnose(conn, "https://jobs.lever.co/acmelabs/abc")
        self.assertEqual((got.cause, got.company_id), ("no_board", keep))

    def test_detail_names_the_link_and_the_facts_behind_the_cause(self):
        conn = self.conn
        board = seed_board(conn, seed_company(conn, "Acme"), "ashby", "acme")
        store_posting(conn, "ashby:acme:senior-1", board_id=board, level=dict(level_tier="senior"))
        detail = diagnose(conn, ashby("acme", "senior-1")).detail
        self.assertEqual((detail["ats"], detail["token"], detail["native_id"]), ("ashby", "acme", "senior-1"))
        self.assertEqual((detail["lane"], detail["level_tier"], detail["level_ok"]), ("engineering", "senior", False))
        self.assertEqual(diagnose(conn, "not a link").detail, {"reason": "The link names no job board Knock reads."})


class LogMissTests(MissCase):
    def test_unknown_site_is_stored(self):
        miss = log_miss(self.conn, "https://example.com/careers/42", "from a friend", NOW)
        row = self.conn.execute("SELECT * FROM miss WHERE miss_id = ?", (miss.miss_id,)).fetchone()
        self.assertEqual((row["url"], row["note"], row["pasted_at"], row["cause"], row["resolved_at"]),
                         ("https://example.com/careers/42", "from a friend", NOW, "unknown_site", None))
        self.assertEqual((row["posting_id"], row["company_id"], row["board_id"]), (None, None, None))
        self.assertEqual(json.loads(row["detail"]), miss.detail)
        self.assertEqual(miss.cause, "unknown_site")

    def test_the_cause_and_ids_are_stored(self):
        conn = self.conn
        acme = seed_company(conn, "Acme")
        board = seed_board(conn, acme, "ashby", "acme")
        pid = store_posting(conn, "ashby:acme:ok-1", company_id=acme, board_id=board,
                            level=dict(level_tier="new_grad", level_ok=1))
        miss = log_miss(conn, ashby("acme", "ok-1"), "", NOW)
        row = conn.execute("SELECT cause, posting_id, company_id, board_id FROM miss").fetchone()
        self.assertEqual(tuple(row), ("found", pid, acme, board))
        self.assertEqual((miss.posting_id, miss.company_id, miss.board_id), (pid, acme, board))

    def test_same_url_twice_keeps_one_row(self):
        first = log_miss(self.conn, "https://jobs.lever.co/zed/abc", "first", NOW)
        second = log_miss(self.conn, "  https://jobs.lever.co/zed/abc  ", "second", LATER)
        self.assertEqual(second.miss_id, first.miss_id)
        self.assertEqual((second.note, second.pasted_at), ("first", NOW))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM miss").fetchone()[0], 1)

    def test_a_second_paste_is_not_diagnosed_again(self):
        url = "https://jobs.lever.co/zed/abc"
        first = log_miss(self.conn, url, "", NOW)
        self.assertEqual(first.cause, "no_company")
        seed_company(self.conn, "Zed")
        self.assertEqual(log_miss(self.conn, url, "", LATER).cause, "no_company")

    def test_every_cause_has_plain_words(self):
        self.assertEqual(set(CAUSE_TEXT), set(CAUSES))
        for text in CAUSE_TEXT.values():
            self.assertNotRegex(text, "[\u2013\u2014]")
            self.assertTrue(text.endswith("."))

    def test_the_miss_table_schema_matches_the_causes(self):
        sql = self.conn.execute("SELECT sql FROM sqlite_master WHERE name = 'miss'").fetchone()[0]
        for cause in CAUSES:
            self.assertIn(f"'{cause}'", sql)
        self.assertIs(misses.CAUSES, CAUSES)


if __name__ == "__main__":
    unittest.main()
