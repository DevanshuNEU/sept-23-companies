import unittest

import sqlite3

from outreach.mcp.guard import Grant, open_guarded
from outreach.review import ReviewError, raise_item
from outreach.review.items import (Raised, clear_item, decided_before, get_item, resolve_item, settle_siblings,
                                   unit)
from outreach.store.timefmt import utcnow
from tests.review_seed import seed_item, seed_person
from tests.storetest import fresh_store, seed_board, seed_company, seed_posting

EN_DASH, EM_DASH = chr(0x2013), chr(0x2014)


class ItemsTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.company = seed_company(self.conn)

    def raise_(self, **kw):
        args = dict(kind="size_unknown", company_id=self.company, reason="Nobody has recorded its size.",
                    evidence_key="no-size", raised_by="daily_list")
        args.update(kw)
        return raise_item(self.conn, **args)

    def count(self, table="review_item", where="1"):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]

    def events(self, kind="raised"):
        return self.conn.execute("SELECT * FROM event WHERE entity = 'review_item' AND kind = ? ORDER BY event_id",
                                 (kind,)).fetchall()

    def test_raise_once_per_evidence(self):
        first = self.raise_()
        second = self.raise_()
        self.assertEqual(first, Raised(first.review_id, True, False, "open"))
        self.assertEqual(second, Raised(first.review_id, False, False, "open"))
        self.assertEqual(self.count(), 1)
        self.assertEqual(len(self.events()), 1)
        event = self.events()[0]
        self.assertEqual((event["entity_id"], event["company_id"], event["idempotency_key"]),
                         (first.review_id, self.company, f"review-raised:{first.review_id}"))

    def test_new_evidence_key_raises_a_new_item(self):
        first = self.raise_()
        second = self.raise_(evidence_key="no-size-2")
        self.assertTrue(second.created)
        self.assertNotEqual(first.review_id, second.review_id)
        self.assertEqual(self.count(), 2)

    def test_the_index_columns_make_the_identity(self):
        post_a = seed_posting(self.conn, self.company, "A")
        post_b = seed_posting(self.conn, self.company, "B")
        a = self.raise_(kind="flagged", posting_id=post_a, raised_by="claude")
        b = self.raise_(kind="flagged", posting_id=post_b, raised_by="claude")
        again = self.raise_(kind="flagged", posting_id=post_a, raised_by="claude")
        self.assertEqual((a.created, b.created, again.created), (True, True, False))
        self.assertEqual(again.review_id, a.review_id)

    def test_resolved_item_stays_resolved_when_raised_again(self):
        first = self.raise_()
        resolve_item(self.conn, first.review_id, resolution="marked_ok", note="fine", revision=1)
        again = self.raise_()
        self.assertEqual(again, Raised(first.review_id, False, False, "resolved"))
        self.assertEqual(self.count(), 1)
        self.assertEqual(get_item(self.conn, first.review_id)["resolution"], "marked_ok")

    def test_cleared_item_comes_back_when_the_condition_returns(self):
        first = self.raise_()
        self.assertTrue(clear_item(self.conn, first.review_id, "the size arrived"))
        before = get_item(self.conn, first.review_id)
        again = self.raise_(reason="Nobody has recorded its size, still.")
        self.assertEqual(again, Raised(first.review_id, False, True, "open"))
        row = get_item(self.conn, first.review_id)
        self.assertEqual((row["state"], row["resolution"], row["resolved_at"], row["resolution_note"]),
                         ("open", None, None, ""))
        self.assertEqual(row["reason"], "Nobody has recorded its size, still.")
        self.assertEqual(row["revision"], before["revision"] + 1)
        self.assertEqual(self.count(), 1)

    def test_a_reopened_item_that_is_open_is_left_alone(self):
        first = self.raise_()
        changes = self.conn.total_changes
        again = self.raise_(reason="A different sentence.")
        self.assertEqual(again, Raised(first.review_id, False, False, "open"))
        self.assertEqual(self.conn.total_changes, changes)
        self.assertEqual(get_item(self.conn, first.review_id)["revision"], 1)

    def test_reopen_false_leaves_a_cleared_item_cleared(self):
        first = self.raise_()
        clear_item(self.conn, first.review_id, "gone")
        changes = self.conn.total_changes
        again = self.raise_(reopen=False, reason="Back again.")
        self.assertEqual(again, Raised(first.review_id, False, False, "resolved"))
        self.assertEqual(self.conn.total_changes, changes)
        self.assertEqual(get_item(self.conn, first.review_id)["resolution"], "cleared")

    def test_actor_is_recorded_on_the_raised_event(self):
        claude = self.raise_(kind="flagged", evidence_key="k1", raised_by="claude", actor="claude", reopen=False)
        default = self.raise_(evidence_key="k2")
        imported = self.raise_(kind="flagged", evidence_key="import:k3", raised_by="import", actor="import")
        actors = {e["entity_id"]: e["actor"] for e in self.events()}
        self.assertEqual(actors, {claude.review_id: "claude", default.review_id: "system",
                                  imported.review_id: "import"})
        self.assertEqual(get_item(self.conn, imported.review_id)["raised_by"], "import")

    def test_created_at_is_the_given_now(self):
        made = self.raise_(now="2026-10-22T08:04:00Z")
        self.assertEqual(get_item(self.conn, made.review_id)["created_at"], "2026-10-22T08:04:00Z")
        self.assertEqual(self.events()[0]["at"], "2026-10-22T08:04:00Z")

    def test_a_check_failure_is_not_swallowed(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.raise_(kind="sponsorship_unclear", raised_by="rules")
        with self.assertRaises(sqlite3.IntegrityError):
            self.raise_(kind="board_empty", raised_by="crawler")
        with self.assertRaises(sqlite3.IntegrityError):
            self.raise_(kind="not_a_kind")
        with self.assertRaises(sqlite3.IntegrityError):
            self.raise_(raised_by="nobody")
        self.assertEqual((self.count(), self.count("event")), (0, 0))
        self.assertFalse(self.conn.in_transaction)

    def test_reason_with_a_dash_is_refused(self):
        for dash in (EN_DASH, EM_DASH):
            with self.assertRaises(ValueError):
                self.raise_(reason=f"Size unknown {dash} look it up")
        for blank in ("", "   ", "\n"):
            with self.assertRaises(ValueError):
                self.raise_(reason=blank)
        self.assertEqual(self.count(), 0)

    def test_it_accepts_the_row_every_existing_caller_writes(self):
        """The shapes the ranker, identity, the crawler, sources and the MCP insert today."""
        co = self.company
        posting = seed_posting(self.conn, co, "Engineer")
        board = seed_board(self.conn, co)
        person = seed_person(self.conn, co)
        shapes = [
            ("size_guess", "ranker", "17", {}),
            ("size_check", "ranker", "many_open_roles", {}),
            ("size_check", "source", "shared-domain:acme.example", {}),
            ("flagged", "ranker", "sponsorship-conflict:abc", {"posting_id": posting}),
            ("merge_candidate", "source", "42", {}),
            ("board_unreadable", "crawler", "2026-10-22T08:04:00Z", {"board_id": board}),
            ("board_empty", "crawler", "2026-10-22T08:04:00Z", {"board_id": board}),
            ("flagged", "crawler", f"board-doubtful:{board}", {"board_id": board}),
            ("flagged", "crawler", "host-blocked:greenhouse:2026-10-22T08:04:00Z", {"board_id": board}),
            ("flagged", "crawler", "careers-unreadable:1:deadbeef", {}),
            ("flagged", "crawler", "no-board-no-careers", {}),
            ("careers_changed", "crawler", "0123456789abcdef", {}),
            ("domain_missing", "crawler", "no-usable-domain", {}),
            ("flagged", "claude", "claude:note", {"posting_id": posting, "person_id": person}),
            ("flagged", "claude", f"claude-no-go:{person}:said_no", {"person_id": person}),
            ("flagged", "import", "import:ruled-out", {}),
        ]
        for kind, raised_by, key, ids in shapes:
            actor = raised_by if raised_by in ("claude", "import") else "system"
            made = raise_item(self.conn, kind=kind, company_id=co, reason=f"A {kind} reason.", evidence_key=key,
                              raised_by=raised_by, actor=actor, reopen=raised_by != "claude", **ids)
            self.assertTrue(made.created, (kind, key))
            row = get_item(self.conn, made.review_id)
            self.assertEqual((row["kind"], row["raised_by"], row["evidence_key"], row["board_id"],
                              row["posting_id"], row["person_id"]),
                             (kind, raised_by, key, ids.get("board_id"), ids.get("posting_id"), ids.get("person_id")))
        self.assertEqual(self.count(), len(shapes))
        self.assertEqual(len(self.events()), len(shapes))

    def test_reopen_false_runs_under_the_mcp_write_guard(self):
        """Claude's connection may insert a review item and an event and update nothing: that is all reopen=False does."""
        path = self.conn.execute("PRAGMA database_list").fetchone()["file"]
        guarded = open_guarded({"review_item": Grant(insert=True), "event": Grant(insert=True)}, path=path)
        self.addCleanup(guarded.close)
        kw = dict(kind="flagged", company_id=self.company, reason="Check this one.", evidence_key="claude:one",
                  raised_by="claude", actor="claude", reopen=False)
        made = raise_item(guarded, **kw)
        again = raise_item(guarded, **kw)
        self.assertEqual((made.created, again.created, again.review_id), (True, False, made.review_id))
        self.assertEqual([e["actor"] for e in self.events()], ["claude"])
        clear_item(self.conn, made.review_id, "gone")
        self.assertEqual(raise_item(guarded, **kw).state, "resolved")
        with self.assertRaises(sqlite3.DatabaseError):  # reopen=True would UPDATE: the guard refuses it
            raise_item(guarded, **{**kw, "reopen": True})

    def test_decided_before_sees_his_decisions_and_ignores_cleared(self):
        posting = seed_posting(self.conn, self.company, "Engineer")
        a = self.raise_(kind="sponsorship_unclear", raised_by="rules", posting_id=posting, evidence_key="q1")
        self.assertIsNone(decided_before(self.conn, kind="sponsorship_unclear", company_id=self.company,
                                         evidence_key="q1"))
        resolve_item(self.conn, a.review_id, resolution="marked_ok", note="", revision=1)
        found = decided_before(self.conn, kind="sponsorship_unclear", company_id=self.company, evidence_key="q1")
        self.assertEqual(found["review_id"], a.review_id)
        # Another posting with the same wording is the same question.
        self.assertIsNotNone(decided_before(self.conn, kind="sponsorship_unclear", company_id=self.company,
                                            evidence_key="q1"))
        # Other kind, company or key: nothing.
        self.assertIsNone(decided_before(self.conn, kind="flagged", company_id=self.company, evidence_key="q1"))
        self.assertIsNone(decided_before(self.conn, kind="sponsorship_unclear", company_id=seed_company(self.conn),
                                         evidence_key="q1"))
        self.assertIsNone(decided_before(self.conn, kind="sponsorship_unclear", company_id=self.company,
                                         evidence_key="q2"))
        for resolution in ("marked_no_go", "field_edited"):
            item = self.raise_(evidence_key=f"k-{resolution}")
            resolve_item(self.conn, item.review_id, resolution=resolution, note="", revision=1)
            self.assertEqual(decided_before(self.conn, kind="size_unknown", company_id=self.company,
                                            evidence_key=f"k-{resolution}")["resolution"], resolution)
        cleared = self.raise_(evidence_key="was-cleared")
        clear_item(self.conn, cleared.review_id, "gone")
        self.assertIsNone(decided_before(self.conn, kind="size_unknown", company_id=self.company,
                                         evidence_key="was-cleared"))
        still_open = self.raise_(evidence_key="still-open")
        self.assertIsNone(decided_before(self.conn, kind="size_unknown", company_id=self.company,
                                         evidence_key="still-open"))
        self.assertEqual(still_open.state, "open")

    def test_resolve_item_checks_revision_and_state(self):
        made = self.raise_()
        with self.assertRaises(ReviewError) as stale:
            resolve_item(self.conn, made.review_id, resolution="marked_ok", note="", revision=7)
        self.assertEqual(stale.exception.code, "conflict")
        self.assertEqual(stale.exception.message, "This item changed since you loaded it.")
        self.assertEqual(get_item(self.conn, made.review_id)["state"], "open")
        self.assertEqual(resolve_item(self.conn, made.review_id, resolution="field_edited", note="45, LinkedIn",
                                      revision=1), 2)
        row = get_item(self.conn, made.review_id)
        self.assertEqual((row["state"], row["resolution"], row["resolution_note"], row["revision"]),
                         ("resolved", "field_edited", "45, LinkedIn", 2))
        self.assertIsNotNone(row["resolved_at"])
        with self.assertRaises(ReviewError) as twice:
            resolve_item(self.conn, made.review_id, resolution="marked_ok", note="", revision=2)
        self.assertEqual((twice.exception.code, twice.exception.message),
                         ("not_allowed", "This item is already settled."))
        with self.assertRaises(ReviewError) as missing:
            resolve_item(self.conn, 9999, resolution="marked_ok", note="", revision=1)
        self.assertEqual(missing.exception.code, "not_found")
        with self.assertRaises(ReviewError) as bad:
            resolve_item(self.conn, made.review_id, resolution="cleared", note="", revision=2)
        self.assertEqual(bad.exception.code, "bad_request")
        with self.assertRaises(ReviewError) as nope:
            get_item(self.conn, 9999)
        self.assertEqual(nope.exception.code, "not_found")

    def test_clear_item_clears_an_open_item_once(self):
        made = self.raise_()
        self.assertTrue(clear_item(self.conn, made.review_id, "the size arrived"))
        row = get_item(self.conn, made.review_id)
        self.assertEqual((row["state"], row["resolution"], row["resolution_note"], row["revision"]),
                         ("resolved", "cleared", "the size arrived", 2))
        self.assertFalse(clear_item(self.conn, made.review_id, "again"))
        self.assertFalse(clear_item(self.conn, 9999, "no such item"))
        events = self.events("cleared")
        self.assertEqual([(e["actor"], e["idempotency_key"]) for e in events],
                         [("system", f"review-cleared:{made.review_id}:2")])

    def test_unit_joins_a_callers_transaction_and_rolls_back_only_itself(self):
        self.conn.execute("BEGIN IMMEDIATE")
        before = seed_company(self.conn, "Kept")
        with self.assertRaises(RuntimeError):
            with unit(self.conn):
                seed_company(self.conn, "Lost")
                raise RuntimeError("boom")
        self.assertTrue(self.conn.in_transaction)
        with unit(self.conn):
            seed_company(self.conn, "Joined")
        self.conn.execute("COMMIT")
        names = {r[0] for r in self.conn.execute("SELECT name FROM company")}
        self.assertEqual(names, {"Acme", "Kept", "Joined"})
        self.assertIsNotNone(before)

    def test_unit_alone_is_one_transaction(self):
        with self.assertRaises(RuntimeError):
            with unit(self.conn):
                self.assertTrue(self.conn.in_transaction)
                seed_company(self.conn, "Lost")
                raise RuntimeError("boom")
        self.assertFalse(self.conn.in_transaction)
        with unit(self.conn):
            seed_company(self.conn, "Kept")
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual({r[0] for r in self.conn.execute("SELECT name FROM company")}, {"Acme", "Kept"})

    def test_raise_item_joins_a_callers_transaction(self):
        self.conn.execute("BEGIN IMMEDIATE")
        made = self.raise_()
        self.assertTrue(self.conn.in_transaction)
        self.conn.execute("ROLLBACK")
        self.assertEqual((self.count(), self.count("event")), (0, 0))
        self.assertTrue(made.created)

    def test_settle_siblings_clears_the_other_open_items_of_one_company_only(self):
        keep = seed_item(self.conn, "size_unknown", self.company)
        other_a = seed_item(self.conn, "domain_missing", self.company)
        other_b = seed_item(self.conn, "flagged", self.company)
        settled = seed_item(self.conn, "size_check", self.company)
        resolve_item(self.conn, settled, resolution="marked_ok", note="", revision=1)
        elsewhere = seed_item(self.conn, "size_unknown", seed_company(self.conn, "Other"))
        done = settle_siblings(self.conn, self.company, except_id=keep, note="a no-go settles the rest")
        self.assertEqual(set(done), {other_a, other_b})
        self.assertIsInstance(done, tuple)
        self.assertEqual(get_item(self.conn, keep)["state"], "open")
        self.assertEqual(get_item(self.conn, elsewhere)["state"], "open")
        self.assertEqual(get_item(self.conn, settled)["resolution"], "marked_ok")
        for review_id in done:
            row = get_item(self.conn, review_id)
            self.assertEqual((row["resolution"], row["resolution_note"]), ("cleared", "a no-go settles the rest"))

    def test_settle_siblings_can_be_limited_to_kinds(self):
        keep = seed_item(self.conn, "size_unknown", self.company)
        size = seed_item(self.conn, "size_check", self.company)
        domain = seed_item(self.conn, "domain_missing", self.company)
        done = settle_siblings(self.conn, self.company, kinds=("size_check", "size_guess"), except_id=keep,
                               note="a size was set")
        self.assertEqual(done, (size,))
        self.assertEqual(get_item(self.conn, domain)["state"], "open")
        self.assertEqual(settle_siblings(self.conn, self.company, kinds=(), except_id=keep, note="x"), ())


if __name__ == "__main__":
    unittest.main()
