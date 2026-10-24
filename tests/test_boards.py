"""Board references and add_board (#31 Task 10)."""
import json
import sqlite3
import unittest
from unittest import mock

from outreach.sources.boards import BoardRef, add_board, normalize_token
from tests.storetest import fresh_store, seed_company

KW = dict(via="careers_page", matched_on="https://acme.example/careers", found_by="careers_page")


class AddBoardTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.acme = seed_company(self.conn, "Acme")
        self.other = seed_company(self.conn, "Other Co")

    def boards(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM board ORDER BY board_id")]

    def events(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM event WHERE entity = 'board' ORDER BY event_id")]

    def items(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM review_item ORDER BY review_id")]

    def counts(self):
        return tuple(self.conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                     for t in ("board", "company_alias", "event", "review_item"))

    def test_add_board_inserts_row_alias_and_event(self):
        self.assertEqual(add_board(self.conn, self.acme, BoardRef("greenhouse", "acme"), **KW), "added")
        (board,) = self.boards()
        self.assertEqual((board["company_id"], board["ats"], board["token"], board["active"]),
                         (self.acme, "greenhouse", "acme", 1))
        alias = self.conn.execute("SELECT * FROM company_alias").fetchone()
        self.assertEqual((alias["company_id"], alias["alias"]), (self.acme, "greenhouse:acme"))
        (event,) = self.events()
        self.assertEqual((event["entity_id"], event["company_id"], event["kind"], event["actor"],
                          event["idempotency_key"]),
                         (board["board_id"], self.acme, "found", "crawler", "board-found:greenhouse:acme"))
        self.assertEqual(json.loads(event["detail"]),
                         {"via": "careers_page", "matched_on": "https://acme.example/careers"})

    def test_add_board_stamps_the_given_now(self):
        at = "2026-11-10T15:14:00Z"
        add_board(self.conn, self.acme, BoardRef("greenhouse", "acme"), now=at, **KW)
        (board,) = self.boards()
        (event,) = self.events()
        self.assertEqual((board["created_at"], event["at"]), (at, at))

    def test_add_board_event_actor_defaults_to_crawler_and_can_be_given(self):
        add_board(self.conn, self.acme, BoardRef("greenhouse", "acme"), **KW)
        add_board(self.conn, self.acme, BoardRef("lever", "acme"), actor="devanshu", **{**KW, "found_by": "hand"})
        self.assertEqual([e["actor"] for e in self.events()], ["crawler", "devanshu"])

    def test_add_board_records_found_by(self):
        for i, found_by in enumerate(("stapply_name", "slug_probe", "careers_page", "import", "hand")):
            with self.subTest(found_by=found_by):
                kw = dict(KW, found_by=found_by)
                self.assertEqual(add_board(self.conn, self.acme, BoardRef("lever", f"co{i}"), **kw), "added")
                got = self.conn.execute("SELECT found_by FROM board WHERE token = ?", (f"co{i}",)).fetchone()[0]
                self.assertEqual(got, found_by)

    def test_a_found_by_outside_the_five_raises_before_any_write(self):
        before = self.counts()
        for bad in ("", "crawler", "stapply", "Hand", None):
            with self.subTest(found_by=bad):
                with self.assertRaises(ValueError):
                    add_board(self.conn, self.acme, BoardRef("greenhouse", "acme"), **dict(KW, found_by=bad))
        self.assertEqual(self.counts(), before)

    def test_add_board_twice_is_exists_and_writes_nothing(self):
        ref = BoardRef("greenhouse", "acme")
        add_board(self.conn, self.acme, ref, **KW)
        before = self.counts()
        snapshot = (self.boards(), self.events())
        self.assertEqual(add_board(self.conn, self.acme, ref, **dict(KW, found_by="hand")), "exists")
        self.assertEqual(self.counts(), before)
        self.assertEqual((self.boards(), self.events()), snapshot)

    def test_board_owned_by_another_company_is_conflict_and_one_item(self):
        ref = BoardRef("greenhouse", "acme")
        add_board(self.conn, self.acme, ref, **KW)
        self.assertEqual(add_board(self.conn, self.other, ref, **KW), "conflict")
        # the board never moves
        (board,) = self.boards()
        self.assertEqual(board["company_id"], self.acme)
        (item,) = self.items()
        self.assertEqual((item["kind"], item["company_id"], item["evidence_key"], item["raised_by"], item["state"]),
                         ("merge_candidate", self.acme, str(self.other), "source", "open"))
        self.assertIn("same_board", item["reason"])
        self.assertIn("strong", item["reason"])
        self.assertIn("greenhouse", item["reason"])
        # a re-run raises nothing new, in either order
        before = self.counts()
        self.assertEqual(add_board(self.conn, self.other, ref, **KW), "conflict")
        self.assertEqual(add_board(self.conn, self.acme, ref, **KW), "exists")
        self.assertEqual(self.counts(), before)

    def test_conflict_adds_no_event_or_alias_for_the_second_company(self):
        ref = BoardRef("greenhouse", "acme")
        add_board(self.conn, self.acme, ref, **KW)
        add_board(self.conn, self.other, ref, **KW)
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM company_alias WHERE company_id = ?",
                                           (self.other,)).fetchone()[0], 0)

    def test_an_ats_alias_owned_by_another_company_is_a_conflict_not_an_error(self):
        # an ats alias with no board row behind it (an import that named the board)
        self.conn.execute("INSERT INTO company_alias(alias_key, company_id, alias, source, created_at) "
                          "VALUES ('lever:acme', ?, 'lever:acme', 'ats', '2026-10-21T00:00:00Z')", (self.other,))
        self.assertEqual(add_board(self.conn, self.acme, BoardRef("lever", "acme"), **KW), "conflict")
        self.assertEqual(self.boards(), [])
        self.assertEqual(self.events(), [])
        (item,) = self.items()
        self.assertEqual((item["company_id"], item["evidence_key"]), (self.acme, str(self.other)))

    def put_alias(self, key, company, alias=None):
        self.conn.execute("INSERT INTO company_alias(alias_key, company_id, alias, source, created_at) "
                          "VALUES (?, ?, ?, 'ats', '2026-10-21T00:00:00Z')", (key, company, alias or key))

    def merge(self, loser, winner):
        self.conn.execute("UPDATE company SET merged_into = ?, status = 'removed', remove_reason = 'merged' "
                          "WHERE company_id = ?", (winner, loser))

    def test_own_alias_with_no_board_becomes_a_board(self):
        self.put_alias("lever:acme", self.acme)
        self.assertEqual(add_board(self.conn, self.acme, BoardRef("lever", "acme"), **KW), "added")
        (board,) = self.boards()
        self.assertEqual((board["company_id"], board["ats"], board["token"]), (self.acme, "lever", "acme"))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM company_alias").fetchone()[0], 1)
        (event,) = self.events()
        self.assertEqual((event["kind"], event["idempotency_key"]), ("found", "board-found:lever:acme"))
        self.assertEqual(self.items(), [])

    def test_alias_of_a_company_merged_into_this_one_becomes_a_board(self):
        self.put_alias("lever:acme", self.other)
        self.merge(self.other, self.acme)
        self.assertEqual(add_board(self.conn, self.acme, BoardRef("lever", "acme"), **KW), "added")
        (board,) = self.boards()
        self.assertEqual(board["company_id"], self.acme)
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.items(), [])

    def test_alias_of_the_survivor_of_this_companys_merge_becomes_a_board(self):
        self.put_alias("lever:acme", self.acme)
        self.merge(self.other, self.acme)
        self.assertEqual(add_board(self.conn, self.other, BoardRef("lever", "acme"), **KW), "added")
        self.assertEqual(self.items(), [])

    def test_workday_alias_of_another_company_is_a_conflict_whatever_the_case(self):
        # add_alias stores the key lower-cased, so the lookup must too
        add_board(self.conn, self.other, BoardRef("workday", "riverside.wd5/External"), **KW)
        self.conn.execute("DELETE FROM board")  # leave the alias with no board behind it
        self.assertEqual(add_board(self.conn, self.acme, BoardRef("workday", "Riverside.WD5/External"), **KW),
                         "conflict")
        self.assertEqual(self.boards(), [])
        (item,) = self.items()
        self.assertEqual((item["company_id"], item["evidence_key"]), (self.acme, str(self.other)))

    def test_workday_board_of_another_company_is_a_conflict_whatever_the_case(self):
        add_board(self.conn, self.other, BoardRef("workday", "riverside.wd5/External"), **KW)
        self.assertEqual(add_board(self.conn, self.acme, BoardRef("workday", "RIVERSIDE.wd5/External"), **KW),
                         "conflict")
        self.assertEqual(len(self.boards()), 1)

    def test_a_board_of_a_company_merged_into_this_one_exists(self):
        ref = BoardRef("greenhouse", "acme")
        add_board(self.conn, self.other, ref, **KW)
        self.conn.execute("UPDATE company SET merged_into = ?, status = 'removed', remove_reason = 'merged' "
                          "WHERE company_id = ?", (self.acme, self.other))
        self.assertEqual(add_board(self.conn, self.acme, ref, **KW), "exists")
        self.assertEqual(self.items(), [])

    def test_the_same_token_under_another_ats_is_another_board(self):
        add_board(self.conn, self.acme, BoardRef("greenhouse", "acme"), **KW)
        self.assertEqual(add_board(self.conn, self.other, BoardRef("lever", "acme"), **KW), "added")
        self.assertEqual(self.items(), [])

    def test_unsupported_ats_raises_value_error(self):
        for ats in ("nonsense", "oracle", "wellfound", "jobvite", "Greenhouse", ""):
            with self.subTest(ats=ats):
                with self.assertRaises(ValueError):
                    BoardRef(ats, "acme")
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_a_ref_forced_past_the_constructor_is_still_refused(self):
        ref = BoardRef("greenhouse", "acme")
        object.__setattr__(ref, "ats", "jobvite")
        with self.assertRaises(ValueError):
            add_board(self.conn, self.acme, ref, **KW)
        object.__setattr__(ref, "ats", "greenhouse")
        object.__setattr__(ref, "token", "Not Valid%")
        with self.assertRaises(ValueError):
            add_board(self.conn, self.acme, ref, **KW)
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_tokens_are_lower_cased_and_validated(self):
        self.assertEqual(BoardRef("greenhouse", "Acme").token, "acme")
        self.assertEqual(normalize_token("lever", " ACME-Co "), "acme-co")
        self.assertEqual(normalize_token("workday", "Riverside.WD5/External"), "riverside.wd5/External")
        self.assertEqual(normalize_token("oracle_orc", "EBWG.fa.us2.oraclecloud.com/CX"),
                         "ebwg.fa.us2.oraclecloud.com/CX")
        self.assertEqual(normalize_token("icims", "Careers-Acme.icims.com"), "careers-acme.icims.com")
        for ats, token in (("workday", "riverside/External"), ("workday", "riverside.wd5"),
                           ("workday", "riverside.wd5/"), ("oracle_orc", "ebwg.example.com/CX"),
                           ("oracle_orc", "ebwg.fa.us2.oraclecloud.com"), ("icims", "acme.example.com"),
                           ("greenhouse", ""), ("greenhouse", "a b"), ("greenhouse", "ac%me"),
                           ("greenhouse", "acme/jobs"), ("ashby", "-acme")):
            with self.subTest(ats=ats, token=token):
                with self.assertRaises(ValueError):
                    normalize_token(ats, token)
        add_board(self.conn, self.acme, BoardRef("greenhouse", "Acme"), **KW)
        self.assertEqual(self.boards()[0]["token"], "acme")

    def test_alias_uses_source_ats_and_key_ats_colon_token(self):
        add_board(self.conn, self.acme, BoardRef("workday", "Riverside.wd5/External"), **KW)
        row = self.conn.execute("SELECT * FROM company_alias").fetchone()
        self.assertEqual((row["source"], row["alias_key"], row["alias"]),
                         ("ats", "workday:riverside.wd5/external", "workday:riverside.wd5/External"))
        add_board(self.conn, self.acme, BoardRef("greenhouse", "Acme"), **KW)
        keys = [r[0] for r in self.conn.execute("SELECT alias_key FROM company_alias ORDER BY alias_key")]
        self.assertIn("greenhouse:acme", keys)
        self.assertEqual(set(r[0] for r in self.conn.execute("SELECT source FROM company_alias")), {"ats"})

    def test_a_failed_write_leaves_nothing_behind(self):
        # a company that does not exist: the foreign key refuses the row, and no alias or event is left
        with self.assertRaises(sqlite3.IntegrityError):
            add_board(self.conn, 9999, BoardRef("greenhouse", "ghost"), **KW)
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_inside_the_callers_transaction_it_joins_and_rolls_back_with_it(self):
        self.conn.execute("BEGIN IMMEDIATE")
        add_board(self.conn, self.acme, BoardRef("greenhouse", "acme"), **KW)
        self.conn.execute("ROLLBACK")
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_a_failure_inside_the_callers_transaction_keeps_the_callers_earlier_work(self):
        self.conn.execute("BEGIN IMMEDIATE")
        self.conn.execute("INSERT INTO company(name, status_at, created_at) VALUES "
                          "('Early', '2026-10-21T00:00:00Z', '2026-10-21T00:00:00Z')")
        with self.assertRaises(sqlite3.IntegrityError):
            add_board(self.conn, 9999, BoardRef("greenhouse", "ghost"), **KW)
        self.assertTrue(self.conn.in_transaction)
        self.conn.execute("COMMIT")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM company WHERE name = 'Early'").fetchone()[0], 1)
        self.assertEqual(self.counts(), (0, 0, 0, 0))

    def test_a_late_failure_undoes_the_board_and_alias_but_not_the_callers_work(self):
        self.conn.execute("BEGIN IMMEDIATE")
        self.conn.execute("INSERT INTO company(name, status_at, created_at) VALUES "
                          "('Early', '2026-10-21T00:00:00Z', '2026-10-21T00:00:00Z')")
        with mock.patch("outreach.sources.boards.append_event", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                add_board(self.conn, self.acme, BoardRef("greenhouse", "acme"), **KW)
        self.assertTrue(self.conn.in_transaction)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM board").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM company_alias").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM company WHERE name = 'Early'").fetchone()[0], 1)
        # the savepoint is released: the same call works afterwards, in the same transaction
        self.assertEqual(add_board(self.conn, self.acme, BoardRef("greenhouse", "acme"), **KW), "added")
        self.conn.execute("COMMIT")
        self.assertEqual(self.counts(), (1, 1, 1, 0))

    def test_exists_never_upgrades_found_by(self):
        ref = BoardRef("greenhouse", "acme")
        add_board(self.conn, self.acme, ref, **dict(KW, found_by="stapply_name"))
        self.assertEqual(add_board(self.conn, self.acme, ref, **dict(KW, found_by="hand")), "exists")
        self.assertEqual(self.boards()[0]["found_by"], "stapply_name")


if __name__ == "__main__":
    unittest.main()
