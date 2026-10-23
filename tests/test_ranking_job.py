"""The rank job (issue #6, Task 11; rulings 8 and 9, the 2026-10-22 ruling on stale postings and confidence).

The job is the only place ranking writes: company_score rows, rules no_go rows, the ranker's review items and one
weights_changed event per version change. A re-run on the same day changes nothing, a failed run changes nothing,
and a dry run writes nothing at all."""
import contextlib
import dataclasses
import io
import json
import re
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import outreach.crawl.stale as crawl_stale
from outreach.crawl.config import load_crawl_config
from outreach.crawl.stale import months_before
from outreach.ranking import job
from outreach.ranking.config import CONFIG_PATH, default_ranker_config, load_ranker_config
from outreach.ranking.confidence import LEVELS
from outreach.ranking.job import RankRun, compute_rank, run_rank, weights_text
from outreach.ranking.scores import hidden_list, latest_version, load_scores, previous_version, ranked_list
from outreach.ranking.version import RANKING_DIR, ranker_version
from outreach.rules import RulesetNotCurrent
from outreach.rules.version import ruleset_version
from outreach.store.timefmt import utcnow
from tests import ranking_store as rs
from tests.storetest import fresh_store, seed_company

TODAY = date(2026, 10, 22)
T1, T2, T3, T4, T5 = ("2026-10-22T0%d:00:00Z" % h for h in (1, 2, 3, 4, 5))
REFUSE = "We are unable to sponsor visas for this role."
SUMMARY_KEYS = {"version", "scored", "hidden", "no_go_written", "size_items", "conflict_items", "undecided_postings",
                "ignored_postings", "stale_postings"}


def ago(days: int, today: date = TODAY) -> str:
    return (today - timedelta(days=days)).isoformat()


def lca(fy: int, jr: int) -> str:
    return json.dumps({"fy": fy, "software": jr, "jr": jr, "level1": 0, "newemp_jr": 0})


class Base(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        # install made the code's own ruleset current, so the job's check passes; rs.decision writes under it.
        self.assertEqual(rs.current_ruleset(self.conn), ruleset_version())

    # seeding
    def company(self, name: str = "Acme", *, board: bool = True, **board_fields) -> tuple[int, int | None]:
        cid = seed_company(self.conn, name)
        return cid, (rs.board(self.conn, cid, **board_fields) if board else None)

    def role(self, cid: int, board_id: int | None, title: str = "Software Engineer, New Grad", *,
             posted_on: str | None = None, final: bool = False, **decision) -> int:
        pid = rs.posting(self.conn, cid, title, board_id=board_id,
                         posted_on=posted_on if posted_on is not None else ago(5))
        rs.decision(self.conn, pid, provisional=0 if final else 1, **decision)
        return pid

    def refusal(self, cid: int, board_id: int | None, *, final: bool = True, **kw) -> int:
        return self.role(cid, board_id, final=final, sponsorship="no", sponsorship_quote=REFUSE, **kw)

    def ev(self, cid: int, fact: str, value: str, *, observed_on: str = "2026-10-01", **kw) -> int:
        eid = rs.evidence(self.conn, cid, fact, value=value, **kw) if fact != "everify" else self.conn.execute(
            "INSERT INTO evidence(company_id, fact, value, confidence, source_kind, source_ref, observed_on, "
            "recorded_at) VALUES (?, 'everify', ?, ?, 'everify', 'https://e-verify.gov', ?, ?)",
            (cid, value, kw.get("confidence", "confirmed"), observed_on, utcnow())).lastrowid
        return eid

    # running
    def rank(self, **kw) -> RankRun:
        kw.setdefault("today", TODAY)
        return run_rank(self.conn, **kw)

    def at(self, stamp: str):
        return mock.patch.object(job, "utcnow", return_value=stamp)

    def versioned(self, version: str):
        return mock.patch.object(job, "ranker_version", return_value=version)

    # reading
    def rows(self, version: str | None = None) -> dict:
        return load_scores(self.conn, version or ranker_version())

    def row(self, cid: int, version: str | None = None):
        return self.rows(version)[cid]

    def all_rows(self, table: str) -> list[tuple]:
        return [tuple(r) for r in self.conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]

    def snapshot(self) -> dict[str, list[tuple]]:
        names = [r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {n: [tuple(r) for r in self.conn.execute(f"SELECT * FROM {n}")] for n in names}

    def jobs(self) -> list:
        return list(self.conn.execute("SELECT * FROM job_run WHERE job = 'rank' ORDER BY run_id"))

    def events(self, kind: str = "weights_changed") -> list:
        return list(self.conn.execute("SELECT * FROM event WHERE kind = ? ORDER BY event_id", (kind,)))

    def stored_rows(self, version: str | None = None) -> dict[int, tuple]:
        """score, lanes and signals of every row: what a same-day re-run must leave byte-identical."""
        return {r["company_id"]: (r["score"], r["lanes"], r["signals"]) for r in self.conn.execute(
            "SELECT company_id, score, lanes, signals FROM company_score WHERE ranker_version = ?",
            (version or ranker_version(),))}


class WriteTests(Base):
    def test_rank_writes_one_row_per_company_with_a_shared_scored_at_and_one_job_run(self):
        a, ab = self.company("Alpha")
        self.role(a, ab)
        b, _ = self.company("Beta", board=False)
        c, cb = self.company("Gamma")
        self.ev(c, "everify", "enrolled", confidence="confirmed")
        got = self.rank()
        rows = self.rows()
        self.assertEqual(sorted(rows), [a, b, c])
        self.assertEqual(len({r.scored_at for r in rows.values()}), 1)
        self.assertEqual(got.version, ranker_version())
        self.assertEqual(sorted(s.company_id for s in got.scored), [a, b, c])
        runs = self.jobs()
        self.assertEqual(len(runs), 1)
        self.assertEqual((runs[0]["status"], runs[0]["ruleset_version"]), ("ok", ruleset_version()))
        summary = json.loads(runs[0]["summary"])
        self.assertEqual(set(summary), SUMMARY_KEYS)
        self.assertEqual((summary["version"], summary["scored"]), (ranker_version(), 3))

    def test_empty_company_is_scored_and_listed(self):
        cid, _ = self.company("Empty", board=False)
        self.rank()
        row = self.row(cid)
        self.assertEqual(row.lanes, ())
        self.assertIsNone(row.signals["hide"])
        self.assertEqual([r.company_id for r in ranked_list(self.conn)], [cid])

    def test_all_hidden_company_is_scored_without_a_role_credit(self):
        cid, board = self.company()
        self.role(cid, board, citizenship=1)
        self.rank()
        row = self.row(cid)
        self.assertEqual(row.signals["values"]["live_role"]["value"], 0.0)
        self.assertEqual(row.lanes, ())
        self.assertIsNone(row.signals["hide"])
        self.assertIn(cid, [r.company_id for r in ranked_list(self.conn)])

    def test_final_refusal_writes_no_go_and_hides_but_a_provisional_one_only_hides(self):
        final_co, fb = self.company("Final")
        pid = self.refusal(final_co, fb, final=True)
        maybe_co, mb = self.company("Maybe")
        self.refusal(maybe_co, mb, final=False)
        got = self.rank()
        no_go = self.all_rows("no_go")
        self.assertEqual(len(no_go), 1)
        row = self.conn.execute("SELECT * FROM no_go").fetchone()
        self.assertEqual((row["company_id"], row["reason"], row["source"], row["source_ref"], row["quote"]),
                         (final_co, "no_sponsorship", "rules", str(pid), REFUSE))
        self.assertIsNotNone(self.row(final_co).signals["hide"])
        maybe = self.row(maybe_co).signals["hide"]
        self.assertTrue(maybe["provisional"])
        self.assertTrue(maybe["reason"].startswith("hidden, pending reader"))
        self.assertEqual(ranked_list(self.conn), [])
        self.assertEqual(sorted(r.company_id for r in hidden_list(self.conn)), [final_co, maybe_co])
        self.assertEqual(got.no_go_written, 1)
        self.assertEqual(got.hidden["provisional"], 1)
        self.assertEqual(sum(got.hidden.values()), 2)

    def test_a_lifted_no_go_is_never_written_again(self):
        cid, board = self.company()
        self.refusal(cid, board, final=True)
        rs.no_go(self.conn, cid, lifted=True)
        got = self.rank()
        self.assertEqual(got.no_go_written, 0)
        self.assertEqual(len(self.all_rows("no_go")), 1)
        self.assertIsNone(self.row(cid).signals["hide"])

    def test_doubtful_board_company_keeps_other_signals_and_credits_no_role(self):
        cid, board = self.company(found_by="stapply_name", last_read=None)
        self.role(cid, board)
        self.ev(cid, "lca", lca(2026, 12), source_kind="lca")
        got = self.rank()
        row = self.row(cid)
        values = row.signals["values"]
        self.assertEqual(values["live_role"]["points"], 0.0)
        self.assertGreater(values["lca_junior"]["points"], 0)
        self.assertIn("1 postings ignored: board match doubtful", row.signals["flags"])
        self.assertIn(cid, [r.company_id for r in ranked_list(self.conn)])
        self.assertEqual(got.ignored_postings, 1)

    def test_merged_away_company_has_no_row_and_the_survivor_has_its_facts(self):
        keep, _ = self.company("Keep", board=False)
        dup, dup_board = self.company("Dup")
        pid = self.role(dup, dup_board)
        self.rank()
        self.assertIn(dup, self.rows())  # before the merge it is its own company
        rs.merge(self.conn, dup, keep)
        self.rank()
        rows = self.rows()
        self.assertNotIn(dup, rows)  # the version's full set: the merged-away row is deleted
        self.assertEqual(rows[keep].lanes, ("level_role",))
        self.assertEqual(rows[keep].signals["best_posting_id"], pid)

    def test_closed_and_removed_companies_are_not_scored(self):
        live, _ = self.company("Live", board=False)
        closed, _ = self.company("Closed", board=False)
        removed, _ = self.company("Removed", board=False)
        self.rank()
        self.conn.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?",
                          (closed,))
        self.conn.execute("UPDATE company SET status = 'removed', remove_reason = 'junk' WHERE company_id = ?",
                          (removed,))
        got = self.rank()
        self.assertEqual(sorted(self.rows()), [live])
        self.assertEqual([s.company_id for s in got.scored], [live])

    def test_conflict_company_is_listed_unhidden_with_one_flagged_item(self):
        cid, board = self.company()
        self.refusal(cid, board, final=True)
        self.role(cid, board, sponsorship="yes", sponsorship_quote="We sponsor H-1B visas.")
        got = self.rank()
        row = self.row(cid)
        self.assertIsNone(row.signals["hide"])
        self.assertIn("postings disagree on sponsorship", row.signals["flags"])
        self.assertIn(cid, [r.company_id for r in ranked_list(self.conn)])
        items = list(self.conn.execute("SELECT * FROM review_item WHERE kind = 'flagged' AND raised_by = 'ranker'"))
        self.assertEqual([(i["company_id"], i["state"]) for i in items], [(cid, "open")])
        self.assertEqual(self.all_rows("no_go"), [])
        self.assertEqual(got.conflict_items, 1)

    def test_a_hidden_company_raises_no_conflict_item(self):
        cid, board = self.company()
        self.refusal(cid, board, final=True)
        self.role(cid, board, sponsorship="yes", sponsorship_quote="We sponsor H-1B visas.")
        rs.no_go(self.conn, cid, "said_no", created_at="2026-10-01T08:00:00Z")
        got = self.rank()
        self.assertEqual(self.row(cid).signals["hide"]["kind"], "no_go")
        self.assertEqual(got.conflict_items, 0)
        self.assertEqual(list(self.conn.execute("SELECT * FROM review_item WHERE raised_by = 'ranker'")), [])

    def test_summary_counts_undecided_postings(self):
        cid, board = self.company()
        rs.posting(self.conn, cid, board_id=board, posted_on=ago(3))  # open, never decided
        got = self.rank()
        self.assertEqual(got.undecided_postings, 1)
        self.assertEqual(json.loads(self.jobs()[0]["summary"])["undecided_postings"], 1)
        self.assertEqual(self.row(cid).signals["values"]["live_role"]["confidence"], "low")

    def test_today_defaults_to_the_utc_date(self):
        self.company(board=False)
        fixed = date(2026, 10, 25)
        with mock.patch.object(job, "_utc_today", return_value=fixed), \
                mock.patch.object(crawl_stale, "load_stale", wraps=crawl_stale.load_stale) as seen:
            run_rank(self.conn)
        self.assertEqual(seen.call_args.args[1], fixed)


class LockTests(Base):
    def test_compute_rank_holds_no_write_lock_while_it_reads(self):
        cid, board = self.company()
        self.role(cid, board)
        path = self.conn.execute("PRAGMA database_list").fetchone()["file"]
        other = sqlite3.connect(path, isolation_level=None, timeout=0)
        self.addCleanup(other.close)
        wrote = []

        def write_meanwhile(*args, **kw):
            other.execute("BEGIN IMMEDIATE")  # fails at once if the rank job held a write lock
            other.execute("UPDATE company SET status_note = 'touched' WHERE company_id = ?", (cid,))
            other.execute("COMMIT")
            wrote.append(True)
            return real(*args, **kw)

        real = job.load_facts
        with mock.patch.object(job, "load_facts", side_effect=write_meanwhile):
            scored, _data = compute_rank(self.conn, today=TODAY)
        self.assertEqual(wrote, [True])
        self.assertEqual([s.company_id for s in scored], [cid])
        self.assertFalse(self.conn.in_transaction)


class WindowTests(Base):
    """Writes that commit on another connection between the compute (a read snapshot) and the write transaction."""

    def during_compute(self, *statements: tuple[str, tuple]):
        path = self.conn.execute("PRAGMA database_list").fetchone()["file"]
        other = sqlite3.connect(path, isolation_level=None, timeout=0)
        self.addCleanup(other.close)
        real = job.load_hide_inputs

        def meanwhile(*args, **kw):
            for sql, params in statements:
                other.execute(sql, params)
            return real(*args, **kw)

        return mock.patch.object(job, "load_hide_inputs", side_effect=meanwhile)

    def test_a_merge_during_the_compute_never_re_hides_a_company_he_put_back(self):
        keep, _ = self.company("Keep", board=False)
        rs.no_go(self.conn, keep, lifted=True)  # he put Keep back on the list
        dup, dup_board = self.company("Dup")
        self.refusal(dup, dup_board, final=True)
        merge = ("UPDATE company SET status = 'removed', remove_reason = 'duplicate', merged_into = ? "
                 "WHERE company_id = ?", (keep, dup))
        with self.during_compute(merge), self.at(T1):
            got = self.rank()
        self.assertEqual(got.no_go_written, 0)
        self.assertEqual(len(self.all_rows("no_go")), 1)  # only his lifted row
        with self.at(T2):
            self.rank()
        self.assertIsNone(self.row(keep).signals["hide"])
        self.assertIn(keep, [r.company_id for r in ranked_list(self.conn)])

    def test_a_company_closed_during_the_compute_gets_no_no_go_row(self):
        cid, board = self.company()
        self.refusal(cid, board, final=True)
        close = ("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?", (cid,))
        with self.during_compute(close):
            got = self.rank()
        self.assertEqual(got.no_go_written, 0)
        self.assertEqual(self.all_rows("no_go"), [])

    def test_a_ruleset_change_during_the_compute_aborts_the_write(self):
        cid, board = self.company()
        self.refusal(cid, board, final=True)
        switch = (("UPDATE ruleset SET is_current = 0", ()),
                  ("INSERT INTO ruleset(ruleset_version, config_toml, created_at, is_current) "
                   "VALUES ('r-0000000000ff', '', ?, 1)", (utcnow(),)))
        with self.during_compute(*switch), self.assertRaises(RulesetNotCurrent):
            self.rank()
        self.assertEqual(self.all_rows("company_score"), [])
        self.assertEqual(self.all_rows("no_go"), [])
        self.assertEqual([j["status"] for j in self.jobs()], ["failed"])
        self.assertFalse(self.conn.in_transaction)


class RerunTests(Base):
    def seed_everything(self) -> dict[str, int]:
        final_co, fb = self.company("Final")
        self.refusal(final_co, fb, final=True)
        conflict_co, cb = self.company("Conflict")
        self.refusal(conflict_co, cb, final=True)
        self.role(conflict_co, cb, sponsorship="yes", sponsorship_quote="We sponsor H-1B visas.")
        guess_co, _ = self.company("Guess", board=False)
        self.ev(guess_co, "size", "400", num_low=400, confidence="guess")
        plain, pb = self.company("Plain")
        self.role(plain, pb)
        return {"final": final_co, "conflict": conflict_co, "guess": guess_co, "plain": plain}

    def test_rerun_with_the_same_version_changes_nothing(self):
        self.seed_everything()
        with self.at(T1):
            first = self.rank()
        self.assertEqual((first.no_go_written, first.size_items, first.conflict_items), (1, 1, 1))
        rows, no_go = self.stored_rows(), self.all_rows("no_go")
        items, events = self.all_rows("review_item"), self.all_rows("event")
        with self.at(T2):
            second = self.rank()
        self.assertEqual(self.stored_rows(), rows)
        self.assertEqual({r.scored_at for r in self.rows().values()}, {T2})  # only the upsert moves scored_at
        self.assertEqual(self.all_rows("no_go"), no_go)
        self.assertEqual(self.all_rows("review_item"), items)
        self.assertEqual(self.all_rows("event"), events)
        self.assertEqual((second.no_go_written, second.size_items, second.conflict_items), (0, 0, 0))
        self.assertEqual(second.hidden, first.hidden)
        self.assertEqual([j["status"] for j in self.jobs()], ["ok", "ok"])

    def test_a_failure_midway_leaves_the_previous_version_and_no_go_untouched(self):
        self.seed_everything()
        with self.versioned("rk-000000000001"), self.at(T1):
            self.rank()
        before = {t: self.all_rows(t) for t in ("company_score", "no_go", "review_item", "event")}
        late, lb = self.company("Late")
        self.refusal(late, lb, final=True)  # would write a second no_go row
        boom = RuntimeError("disk on fire")
        with self.versioned("rk-000000000002"), self.at(T2), \
                mock.patch.object(job, "sync_size_items", side_effect=boom):
            with self.assertRaises(RuntimeError) as caught:
                self.rank()
        self.assertIs(caught.exception, boom)
        for table, rows in before.items():
            self.assertEqual(self.all_rows(table), rows, table)
        self.assertEqual(self.rows("rk-000000000002"), {})
        last = self.jobs()[-1]
        self.assertEqual(last["status"], "failed")
        self.assertIn("disk on fire", last["error"])
        self.assertFalse(self.conn.in_transaction)

    def test_dry_run_writes_nothing(self):
        self.seed_everything()
        before = self.snapshot()
        got = self.rank(dry_run=True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.jobs(), [])
        self.assertEqual(len(got.scored), 4)
        self.assertEqual(got.no_go_written, 0)

    def test_a_stale_ruleset_raises_and_writes_no_scores(self):
        self.seed_everything()
        self.conn.execute("UPDATE ruleset SET is_current = 0")
        self.conn.execute("INSERT INTO ruleset(ruleset_version, config_toml, created_at, is_current) "
                          "VALUES ('r-0000000000ff', '', ?, 1)", (utcnow(),))
        with self.assertRaises(RulesetNotCurrent):
            self.rank()
        with self.assertRaises(RulesetNotCurrent):
            compute_rank(self.conn, today=TODAY)
        self.assertEqual(self.all_rows("company_score"), [])
        self.assertEqual(self.all_rows("no_go"), [])
        self.assertEqual([j["status"] for j in self.jobs()], ["failed"])

    def test_a_code_ruleset_the_store_never_built_closes_the_run_failed_with_no_ruleset(self):
        # job_run.ruleset_version is a key into ruleset, so a ruleset the store does not hold is recorded as NULL.
        self.seed_everything()
        with mock.patch.object(job, "ruleset_version", return_value="r-0000000000aa"), \
                self.assertRaises(RulesetNotCurrent):
            self.rank()
        runs = self.jobs()
        self.assertEqual([(j["status"], j["ruleset_version"]) for j in runs], [("failed", None)])
        self.assertEqual(self.all_rows("company_score"), [])
        self.assertEqual(self.all_rows("no_go"), [])

    def test_a_rerun_on_a_later_day_may_change_rows_but_the_same_day_never_does(self):
        cid, board = self.company()
        posted = months_before(TODAY, 12) + timedelta(days=1)  # 12 months less one day old on TODAY
        self.role(cid, board, posted_on=posted.isoformat())
        with self.at(T1):
            self.rank()
        same_day = self.stored_rows()
        with self.at(T2):
            self.rank()
        self.assertEqual(self.stored_rows(), same_day)
        live_points = json.loads(same_day[cid][2])["values"]["live_role"]["points"]
        self.assertGreater(live_points, 0)
        with self.at(T3):
            later = self.rank(today=TODAY + timedelta(days=2))
        row = self.row(cid)
        self.assertLess(row.signals["values"]["live_role"]["points"], live_points)
        self.assertEqual(row.lanes, ())
        self.assertEqual(later.stale_postings, 1)


class VersionTests(Base):
    def everify_40(self):
        text = CONFIG_PATH.read_text(encoding="utf-8")
        changed = re.sub(r"(?m)^everify = 12\.0$", "everify = 40.0", text)
        self.assertNotEqual(changed, text)
        folder = Path(tempfile.mkdtemp(prefix="ranker-cfg-"))
        (folder / "config.toml").write_text(changed, encoding="utf-8")
        return load_ranker_config(folder / "config.toml")

    def test_version_change_appends_one_weights_changed_event_with_the_changed_weights(self):
        self.company(board=False)
        with self.versioned("rk-aaaaaaaaaaaa"), self.at(T1):
            self.rank()
        with self.versioned("rk-bbbbbbbbbbbb"), self.at(T2):
            self.rank(cfg=self.everify_40())
        events = self.events()
        self.assertEqual(len(events), 1)
        e = events[0]
        run_id = self.jobs()[-1]["run_id"]
        self.assertEqual((e["entity"], e["actor"], e["idempotency_key"]),
                         ("score", "system", f"weights-changed:rk-aaaaaaaaaaaa:rk-bbbbbbbbbbbb:{run_id}"))
        detail = json.loads(e["detail"])
        self.assertEqual(detail, {"old_version": "rk-aaaaaaaaaaaa", "new_version": "rk-bbbbbbbbbbbb",
                                  "changed": {"everify": [12.0, 40.0]}, "text": "E-Verify weight 12 to 40"})

    def test_a_version_change_with_no_weight_change_says_so(self):
        self.company(board=False)
        with self.versioned("rk-aaaaaaaaaaaa"), self.at(T1):
            self.rank()
        with self.versioned("rk-bbbbbbbbbbbb"), self.at(T2):
            self.rank()
        detail = json.loads(self.events()[0]["detail"])
        self.assertEqual((detail["changed"], detail["text"]), ({}, "Ranker updated, no weight changed"))

    def test_switching_a_to_b_to_a_to_b_appends_an_event_for_every_switch(self):
        # A revert and a redo are new changes: the second A to B switch is not swallowed by the first one's key.
        self.company(board=False)
        a, b = "rk-aaaaaaaaaaaa", "rk-bbbbbbbbbbbb"
        for version, stamp in zip((a, b, a, b), (T1, T2, T3, T4)):
            with self.versioned(version), self.at(stamp):
                self.rank()
        runs = [j["run_id"] for j in self.jobs()]
        got = [(json.loads(e["detail"])["old_version"], json.loads(e["detail"])["new_version"], e["idempotency_key"])
               for e in self.events()]
        self.assertEqual(got, [(a, b, f"weights-changed:{a}:{b}:{runs[1]}"),
                               (b, a, f"weights-changed:{b}:{a}:{runs[2]}"),
                               (a, b, f"weights-changed:{a}:{b}:{runs[3]}")])

    def test_first_run_and_same_version_rerun_append_no_event(self):
        self.company(board=False)
        with self.at(T1):
            self.rank()
        with self.at(T2):
            self.rank()
        self.assertEqual(self.events(), [])

    def test_a_new_version_in_the_same_second_is_still_the_latest(self):
        # The old version sorts after the new one by text: a tie on scored_at must not leave the old one latest.
        self.company(board=False)
        with self.versioned("rk-ffffffffffff"), self.at(T1):
            self.rank()
        with self.versioned("rk-000000000000"), self.at(T1):
            self.rank()
        self.assertEqual(latest_version(self.conn), "rk-000000000000")
        self.assertEqual(previous_version(self.conn, "rk-000000000000"), "rk-ffffffffffff")
        self.assertEqual(len(self.events()), 1)

    def test_new_ranker_version_adds_rows_and_keeps_the_old_ones(self):
        cid, _ = self.company(board=False)
        with self.versioned("rk-111111111111"), self.at(T1):
            self.rank()
        with self.versioned("rk-222222222222"), self.at(T2):
            self.rank()
        self.assertEqual(sorted(self.rows("rk-111111111111")), [cid])
        self.assertEqual(sorted(self.rows("rk-222222222222")), [cid])
        self.assertEqual(latest_version(self.conn), "rk-222222222222")
        self.assertEqual(previous_version(self.conn, "rk-222222222222"), "rk-111111111111")

    def test_versions_beyond_three_are_pruned(self):
        self.company(board=False)
        versions = [f"rk-00000000000{i}" for i in range(1, 5)]
        for version, stamp in zip(versions, (T1, T2, T3, T4)):
            with self.versioned(version), self.at(stamp):
                self.rank()
        kept = [r[0] for r in self.conn.execute(
            "SELECT DISTINCT ranker_version FROM company_score ORDER BY ranker_version")]
        self.assertEqual(kept, versions[1:])

    def test_the_stale_months_value_moves_the_version_and_the_rows(self):
        cid, board = self.company()
        self.role(cid, board, posted_on=months_before(TODAY, 14).isoformat())
        twelve = ranker_version()
        with self.at(T1):
            self.rank()
        self.assertEqual(self.row(cid, twelve).lanes, ())
        eighteen_cfg = dataclasses.replace(load_crawl_config(), stale_after_months=18)
        with mock.patch.object(crawl_stale, "load_crawl_config", return_value=eighteen_cfg), \
                mock.patch("outreach.ranking.version.load_crawl_config", return_value=eighteen_cfg), self.at(T2):
            eighteen = ranker_version()
            got = self.rank()
        self.assertNotEqual(eighteen, twelve)
        self.assertEqual(got.version, eighteen)
        self.assertEqual(self.row(cid, eighteen).lanes, ("level_role",))
        self.assertEqual(self.row(cid, twelve).lanes, ())  # the old version's row is kept as it was


class WeightsTextTests(unittest.TestCase):
    def test_one_change_reads_as_one_plain_line(self):
        self.assertEqual(weights_text({"everify": [12.0, 40.0]}), "E-Verify weight 12 to 40")

    def test_labels_capitalize_and_space_other_names(self):
        self.assertEqual(weights_text({"live_role": [40.0, 42.5]}), "Live role weight 40 to 42.5")

    def test_a_signal_on_one_side_only_reads_none(self):
        self.assertEqual(weights_text({"new_thing": [None, 5.0]}), "New thing weight none to 5")

    def test_more_than_three_changes_are_cut_with_a_count(self):
        changed = {name: [1.0, 2.0] for name in ("a_one", "b_two", "c_three", "d_four", "e_five")}
        self.assertEqual(weights_text(changed),
                         "A one weight 1 to 2; B two weight 1 to 2; C three weight 1 to 2; and 2 more")

    def test_no_change(self):
        self.assertEqual(weights_text({}), "Ranker updated, no weight changed")


class StaleTests(Base):
    def test_compute_rank_reads_stale_from_the_crawler_module_once_and_passes_it_down(self):
        cid, board = self.company()
        self.role(cid, board, posted_on="2016-03-02")
        real = crawl_stale.load_stale
        returned = []
        calls = []

        def fake(conn, today, *, max_age_months=None, **kw):
            calls.append((today, max_age_months, kw))
            got = real(conn, today, max_age_months=max_age_months, **kw)
            returned.append(got)
            return got

        with mock.patch.object(crawl_stale, "load_stale", side_effect=fake), \
                mock.patch.object(job, "load_roles", wraps=job.load_roles) as roles, \
                mock.patch.object(job, "load_hide_inputs", wraps=job.load_hide_inputs) as hides:
            self.rank()
        self.assertEqual(calls, [(TODAY, None, {})])
        self.assertIs(roles.call_args.args[4], returned[0])
        self.assertIs(hides.call_args.args[3], returned[0])
        self.assertEqual(len(returned[0]), 1)
        cutoff = re.compile(r"months_before|posted_on\s*[<>]")
        for path in sorted(RANKING_DIR.rglob("*.py")):
            self.assertIsNone(cutoff.search(path.read_text(encoding="utf-8")), f"{path.name} derives staleness")

    def test_a_2016_posting_gives_no_role_credit_and_the_company_stays_listed(self):
        cid, board = self.company()
        pid = self.role(cid, board, posted_on="2016-03-02")
        self.ev(cid, "everify", "enrolled", confidence="confirmed")
        got = self.rank()
        row = self.row(cid)
        values = row.signals["values"]
        self.assertEqual(row.lanes, ())
        self.assertEqual(values["live_role"]["points"], 0.0)
        self.assertGreater(values["everify"]["points"], 0)
        self.assertIsNone(row.signals["hide"])
        self.assertIn(cid, [r.company_id for r in ranked_list(self.conn)])
        self.assertEqual([s["posting_id"] for s in row.signals["stale_postings"]], [pid])
        self.assertTrue(any("posted 2016-03-02, over 12 months" in f for f in row.signals["flags"]))
        self.assertEqual(got.stale_postings, 1)
        self.assertEqual(json.loads(self.jobs()[0]["summary"])["stale_postings"], 1)

    def test_a_failed_board_read_never_makes_the_role_stale(self):
        cid, board = self.company(last_read="error")
        rs.board_read(self.conn, board, "ok", at="2026-10-15T08:00:00Z")
        self.role(cid, board)
        got = self.rank()
        row = self.row(cid)
        live = row.signals["values"]["live_role"]
        self.assertEqual(row.signals["stale_postings"], [])
        self.assertGreater(live["points"], 0)
        self.assertEqual(live["confidence"], "medium")
        self.assertIn("last board read failed", live["basis"])
        self.assertEqual(got.stale_postings, 0)

    def test_a_stale_refusal_writes_no_no_go_and_hides_nothing(self):
        cid, board = self.company()
        self.refusal(cid, board, final=True, posted_on="2016-03-02")
        got = self.rank()
        self.assertEqual(self.all_rows("no_go"), [])
        self.assertIsNone(self.row(cid).signals["hide"])
        self.assertEqual(got.no_go_written, 0)

    def test_every_stored_row_has_a_rank_confidence_and_every_component_a_basis(self):
        empty, _ = self.company("Empty", board=False)
        a, ab = self.company("Alpha")
        self.role(a, ab)
        self.ev(a, "everify", "enrolled", confidence="confirmed")
        b, bb = self.company("Beta")
        self.role(b, bb, posted_on="2016-03-02")
        self.rank()
        for cid, row in self.rows().items():
            self.assertIn(row.signals["confidence"]["level"], LEVELS, cid)
            self.assertTrue(row.signals["confidence"]["basis"].strip(), cid)
            self.assertTrue(row.signals["rank_reason"]["line"].strip(), cid)
            for name, part in row.signals["values"].items():
                self.assertIn(part["confidence"], LEVELS, (cid, name))
                self.assertTrue(part["basis"].strip(), (cid, name))
        nothing = self.row(empty).signals["confidence"]
        self.assertEqual((nothing["level"], nothing["basis"]), ("low", "no signal has evidence"))


class CommandTests(Base):
    def test_rank_dry_run_prints_the_version_counts_and_top_rows_and_writes_nothing(self):
        from outreach.ranking.__main__ import main
        cid, board = self.company("Alpha")
        self.role(cid, board)
        path = self.conn.execute("PRAGMA database_list").fetchone()["file"]
        before = self.snapshot()
        out = io.StringIO()
        with mock.patch.dict("os.environ", {"OUTREACH_DB": path}), contextlib.redirect_stdout(out):
            code = main(["rank", "--dry-run", "--today", TODAY.isoformat()])
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn(ranker_version(), text)
        self.assertIn("Alpha", text)
        self.assertIn("dry run", text)
        self.assertEqual(self.snapshot(), before)
        for dash in (chr(0x2013), chr(0x2014)):
            self.assertNotIn(dash, text)


if __name__ == "__main__":
    unittest.main()
