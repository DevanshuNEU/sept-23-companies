"""Read one board into the store: the phases of read_board (issue #21, Task 11)."""
import dataclasses
import json
import unittest
from pathlib import Path
from unittest import mock

from outreach.crawl import ingest
from outreach.crawl.ats.greenhouse import GreenhouseAdapter
from outreach.crawl.base import JobRef, ReadResult, Validators, ids_fingerprint
from outreach.crawl.boardstate import load_board
from outreach.crawl.cadence import add_seconds
from outreach.crawl.config import load_crawl_config
from outreach.crawl.decisions import decide_postings
from outreach.crawl.ingest import ReadOutcome, read_board
from outreach.crawl.polite import HostTripped
from outreach.net import Response
from outreach.rules import ruleset_version
from outreach.store import body_sha256, start_job
from tests.fakes import FakeClient, status_response, text_response
from tests.storetest import fresh_store, seed_board, seed_company

FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
US = "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
JOBS = (FIXTURES / "greenhouse_jobs.json").read_bytes()
CHANGED = (FIXTURES / "greenhouse_changed.json").read_bytes()
EMPTY = (FIXTURES / "greenhouse_empty.json").read_bytes()
T0 = "2026-10-22T08:00:00Z"
SWE, SALES, DATA, BACKEND = "4012345001", "4012399001", "4020001001", "4031000001"
FLOOR = 21600


def at(days: float) -> str:
    return add_seconds(T0, int(days * 86400))


def ok(body=JOBS, etag='"A"'):
    return Response(200, "", body, {"etag": etag} if etag else {})


def board_json(edit) -> bytes:
    """The Task 4 fixture with `edit(jobs)` applied to its job list."""
    data = json.loads(JOBS)
    edit(data["jobs"])
    data["meta"]["total"] = len(data["jobs"])
    return json.dumps(data).encode("utf-8")


def many_jobs(n: int, company_name: str = "Acme Robotics") -> bytes:
    jobs = [{"id": 5000000 + i, "title": f"Account Executive {i}", "absolute_url": f"https://x.example/{i}",
             "location": {"name": "Remote"}, "company_name": company_name, "content": "&lt;p&gt;Sell.&lt;/p&gt;"}
            for i in range(n)]
    return json.dumps({"jobs": jobs, "meta": {"total": n}}).encode("utf-8")


class DetailAdapter:
    """A detail-request ATS: the list carries no body, `body` fetches one. Both assert no transaction is open."""
    ats = "workday"

    def __init__(self, conn, results):
        self.conn = conn
        self.results = list(results)
        self.body_calls = []
        self.texts = {}

    def list(self, token, validators):
        assert not self.conn.in_transaction, "a transaction is open during the list request"
        return self.results.pop(0)

    def body(self, token, job):
        assert not self.conn.in_transaction, "a transaction is open during a detail request"
        self.body_calls.append(job.native_id)
        return self.texts.get(job.native_id, f"Body of {job.native_id}. We sponsor visas.")


def detail_job(native_id, title, posted_on):
    return JobRef(native_id, title, f"https://acme.example/{native_id}", "Boston, MA", "US", posted_on, None)


def detail_ok(jobs):
    return ReadResult("ok", tuple(jobs), Validators("", ids_fingerprint(j.native_id for j in jobs)))


class IngestTestCase(unittest.TestCase):
    company_name = "Acme Robotics"

    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.company = seed_company(self.conn, self.company_name)
        self.board_id = seed_board(self.conn, self.company)
        self.cfg = load_crawl_config()
        self.clock = [T0]
        # The body's fetched_at and the decision's decided_at follow the read's `now`, so the refetch age and the
        # crash heal (a body newer than its decision) are tested on one clock.
        for target in ("outreach.crawl.bodies.utcnow", "outreach.rules.rebuild.utcnow"):
            patch = mock.patch(target, lambda: self.clock[0])
            patch.start()
            self.addCleanup(patch.stop)

    def read(self, answer, now=T0, *, board_id=None, adapter=None, cfg=None, combiner=None, url=US):
        """One read of the board with the real Greenhouse adapter (or `adapter`) at `now`."""
        client = FakeClient({url: answer})
        self.client = client
        self.clock[0] = now
        board = load_board(self.conn, board_id or self.board_id)
        run_id = start_job(self.conn, "read_boards")
        out = read_board(self.conn, board, adapter or GreenhouseAdapter(client), run_id=run_id, now=now,
                         cfg=cfg or self.cfg, combiner=combiner)
        self.assertEqual(client.unrouted, [])
        return out

    def posting(self, native_id, board="greenhouse:acme"):
        return self.conn.execute("SELECT * FROM posting WHERE source_key = ?", (f"{board}:{native_id}",)).fetchone()

    def pid(self, native_id, board="greenhouse:acme"):
        return self.posting(native_id, board)["posting_id"]

    def board(self, board_id=None):
        return self.conn.execute("SELECT * FROM board WHERE board_id = ?", (board_id or self.board_id,)).fetchone()

    def items(self, kind=None):
        sql = "SELECT * FROM review_item" + (" WHERE kind = ?" if kind else "") + " ORDER BY review_id"
        return self.conn.execute(sql, (kind,) if kind else ()).fetchall()

    def open_postings(self):
        return self.conn.execute("SELECT count(*) FROM posting WHERE board_id = ? AND closed_at IS NULL",
                                 (self.board_id,)).fetchone()[0]

    def count(self, table):
        return self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def decision(self, posting_id):
        return self.conn.execute("SELECT * FROM current_decision WHERE posting_id = ?", (posting_id,)).fetchone()

    def make_final(self, posting_id, reader="reader-1"):
        self.conn.execute("UPDATE decision SET provisional = 0, reader_version = ? WHERE posting_id = ?",
                          (reader, posting_id))

    def set_found_by(self, found_by, board_id=None):
        self.conn.execute("UPDATE board SET found_by = ? WHERE board_id = ?", (found_by, board_id or self.board_id))

    def add_lca(self, company_id=None):
        self.conn.execute(
            "INSERT INTO evidence(company_id, fact, value, confidence, source_kind, recorded_at) "
            "VALUES (?, 'lca', ?, 'confirmed', 'lca', ?)",
            (company_id or self.company, json.dumps({"fy": 2025, "software": 3, "jr": 1, "level1": 1,
                                                     "newemp_jr": 1}), T0))


class DoubtfulBoardTests(IngestTestCase):
    company_name = "Acme"

    def test_first_read_of_a_stapply_name_board_with_another_org_name_raises_the_doubtful_item(self):
        self.set_found_by("stapply_name")
        seen_at_first_write = []
        real_write = ingest.write_postings

        def watch(conn, *args, **kw):
            seen_at_first_write.append((self.count("review_item"), self.count("posting")))
            return real_write(conn, *args, **kw)

        with mock.patch.object(ingest, "write_postings", watch), \
                mock.patch.object(ingest, "decide_postings", side_effect=RuntimeError("crash after phase 3")):
            with self.assertRaises(RuntimeError):
                self.read(ok())
        self.assertEqual(seen_at_first_write, [(1, 0)])  # the item exists before any posting row does
        self.assertEqual(self.count("posting"), 3)
        item, = self.items()
        self.assertEqual(item["kind"], "flagged")
        self.assertEqual(item["board_id"], self.board_id)
        self.assertEqual(item["company_id"], self.company)
        self.assertEqual(item["raised_by"], "crawler")
        self.assertEqual(item["evidence_key"], f"board-doubtful:{self.board_id}")
        self.assertTrue(item["reason"].startswith("board match doubtful"), item["reason"])
        self.assertIn("Acme Robotics", item["reason"])
        self.assertIn('"Acme"', item["reason"])
        board = self.board()
        self.assertIsNone(board["last_read"])  # the board state is the last write: a crash leaves the first read
        self.assertEqual(board["etag"], "")
        self.assertEqual(self.count("board_read"), 0)

    def test_first_read_of_a_small_lca_company_with_over_300_jobs_raises_it(self):
        self.conn.execute("UPDATE company SET name = 'Acme Robotics' WHERE company_id = ?", (self.company,))
        self.set_found_by("stapply_name")
        self.add_lca()
        self.read(ok(many_jobs(301)))
        item, = self.items("flagged")
        self.assertTrue(item["reason"].startswith("board match doubtful"), item["reason"])
        self.assertIn("301", item["reason"])
        self.assertEqual(item["evidence_key"], f"board-doubtful:{self.board_id}")
        # 300 jobs is not over 300; and a company with no lca evidence is not held to the size rule.
        for name, token, lca, n in (("Beta", "beta", True, 300), ("Gamma", "gamma", False, 400)):
            with self.subTest(company=name):
                cid = seed_company(self.conn, name)
                bid = seed_board(self.conn, cid, "greenhouse", token)
                self.set_found_by("stapply_name", bid)
                if lca:
                    self.add_lca(cid)
                self.read(ok(many_jobs(n, name)), board_id=bid,
                          url=f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
                self.assertEqual(self.conn.execute("SELECT count(*) FROM review_item WHERE board_id = ?",
                                                   (bid,)).fetchone()[0], 0)

    def test_first_read_of_a_slug_probe_board_is_checked_like_a_name_only_board(self):
        # A guessed slug can belong to another company with the same name (Task 26a ruling).
        self.set_found_by("slug_probe")
        self.read(ok())
        item, = self.items("flagged")
        self.assertTrue(item["reason"].startswith("board match doubtful"), item["reason"])
        self.assertEqual(item["evidence_key"], f"board-doubtful:{self.board_id}")
        self.assertIn("Acme Robotics", item["reason"])
        # A matching org name raises nothing, and a second read is never a first read.
        cid = seed_company(self.conn, "Acme Robotics")
        bid = seed_board(self.conn, cid, "greenhouse", "zeta")
        self.set_found_by("slug_probe", bid)
        self.read(ok(), board_id=bid, url="https://boards-api.greenhouse.io/v1/boards/zeta/jobs?content=true")
        self.assertEqual(len(self.items("flagged")), 1)

    def test_a_host_matched_board_or_a_second_read_or_a_matching_name_raises_nothing(self):
        # A board found another way, with the same other org name: no item.
        for found_by in ("careers_page", "import", "hand"):
            with self.subTest(found_by=found_by):
                self.conn.execute("UPDATE board SET found_by = ?, last_read = NULL, last_read_at = NULL, "
                                  "streak_since = NULL, etag = '' WHERE board_id = ?", (found_by, self.board_id))
                self.read(ok())
                self.assertEqual(self.items(), [])
        # A name-only board whose org name is one of the company's names (an alias counts): no item, and its second
        # read is not a first read, so a different name then raises nothing either.
        cid = seed_company(self.conn, "Zeta")
        self.conn.execute("INSERT INTO company_alias(alias_key, company_id, alias, source, created_at) "
                          "VALUES ('acmerobotics', ?, 'Acme Robotics', 'dba', ?)", (cid, T0))
        bid = seed_board(self.conn, cid, "greenhouse", "zeta")
        self.set_found_by("stapply_name", bid)
        zeta = "https://boards-api.greenhouse.io/v1/boards/zeta/jobs?content=true"
        self.read(ok(), board_id=bid, url=zeta)
        other = board_json(lambda jobs: [j.update(company_name="Someone Else") for j in jobs])
        self.read(ok(other, '"B"'), at(1), board_id=bid, url=zeta)
        self.assertEqual(self.items(), [])

    def test_the_doubtful_item_is_raised_once(self):
        self.set_found_by("stapply_name")
        with mock.patch.object(ingest, "decide_postings", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                self.read(ok())
        self.conn.execute("UPDATE review_item SET state = 'resolved', resolution = 'marked_ok', resolved_at = ?",
                          (T0,))
        self.read(ok(), at(0.1))  # the crash left last_read NULL, so this is still the first read
        item, = self.items()
        self.assertEqual(item["state"], "resolved")  # resolved stays resolved
        self.assertEqual(self.board()["last_read"], "ok")

    def test_ok_then_error_then_ok_with_another_org_name_raises_nothing(self):
        self.conn.execute("UPDATE company SET name = 'Acme Robotics' WHERE company_id = ?", (self.company,))
        self.set_found_by("stapply_name")
        self.read(ok())
        self.read(status_response(500), at(1))
        other = board_json(lambda jobs: [j.update(company_name="Someone Else") for j in jobs])
        self.read(ok(other, '"B"'), at(2))
        self.assertEqual(self.items("flagged"), [])

    def test_an_empty_first_read_is_the_first_read_even_after_an_error(self):
        self.set_found_by("stapply_name")
        self.read(ok(EMPTY, None))  # no org name and no jobs: nothing to doubt, but the first read has happened
        self.read(status_response(500), at(1))
        self.conn.execute("DELETE FROM board_read")  # only the board row itself still says an empty read happened
        self.read(ok(), at(2))
        self.assertEqual(self.items("flagged"), [])

    def test_imported_postings_do_not_count_as_a_first_read(self):
        self.set_found_by("stapply_name")
        self.conn.execute(
            "INSERT INTO posting(company_id, board_id, source_key, title, location, first_seen_at, last_seen_at) "
            "VALUES (?, ?, 'greenhouse:acme:1', 'Software Engineer', '', ?, ?)", (self.company, self.board_id, T0, T0))
        self.read(status_response(500))
        self.read(ok(), at(1))
        item, = self.items("flagged")
        self.assertTrue(item["reason"].startswith("board match doubtful"))

    def test_the_size_rule_counts_distinct_jobs(self):
        self.conn.execute("UPDATE company SET name = 'Acme Robotics' WHERE company_id = ?", (self.company,))
        self.set_found_by("stapply_name")
        self.add_lca()
        jobs = [JobRef(str(i), f"Account Executive {i}", "", "", None, None, None) for i in range(300)]
        jobs += jobs[:5]  # a listing that repeats ids is still 300 jobs

        class Repeating:
            ats = "greenhouse"

            def list(self, token, validators):
                return ReadResult("ok", tuple(jobs), Validators('"A"', ""), board_name="Acme Robotics")

            def body(self, token, job):
                return None

        self.read(None, adapter=Repeating())
        self.assertEqual(self.items("flagged"), [])

    def test_a_first_read_that_errors_leaves_the_check_for_the_first_good_read(self):
        self.set_found_by("stapply_name")
        self.read(status_response(500))
        self.assertEqual(self.items("flagged"), [])
        self.read(ok(), at(1))
        item, = self.items("flagged")
        self.assertTrue(item["reason"].startswith("board match doubtful"))


class CombinerTests(IngestTestCase):
    def test_read_board_passes_the_combiner_to_decisions(self):
        # decide_and_store takes `combiner` only once #32 lands, so the call is checked against a stand-in.
        calls = []
        real = decide_postings.__globals__["decide_and_store"]

        def spy(conn, posting_id, **kw):
            calls.append(kw.get("combiner"))
            kw.pop("combiner", None)
            return real(conn, posting_id, **kw)

        combiner = object()
        with mock.patch.dict(decide_postings.__globals__, {"decide_and_store": spy}):
            self.read(ok(), combiner=combiner)
        self.assertEqual(calls, [combiner] * 3)


class GreenhouseFlowTests(IngestTestCase):
    def test_greenhouse_fixture_end_to_end(self):
        out = self.read(ok())
        self.assertEqual(out, ReadOutcome(board_id=self.board_id, result="ok", new=3, changed=0, reopened=0,
                                          closed=0, decided=3, bodies=1, interval_s=FLOOR))
        rows = self.conn.execute("SELECT * FROM posting WHERE board_id = ? ORDER BY source_key",
                                 (self.board_id,)).fetchall()
        self.assertEqual([r["source_key"] for r in rows], [f"greenhouse:acme:{i}" for i in (SWE, SALES, DATA)])
        swe = self.posting(SWE)
        self.assertEqual((swe["title"], swe["location"], swe["posted_on"], swe["first_seen_at"], swe["last_seen_at"]),
                         ("Software Engineer, New Grad", "San Francisco, CA", "2026-09-15", T0, T0))
        self.assertEqual(swe["company_id"], self.company)
        # A body only for the engineering title that carried one; none for sales, none for the empty content.
        bodies = self.conn.execute("SELECT posting_id, body, body_sha256 FROM posting_body").fetchall()
        self.assertEqual([b["posting_id"] for b in bodies], [swe["posting_id"]])
        self.assertIn("new grad", bodies[0]["body"])
        self.assertEqual(bodies[0]["body_sha256"], body_sha256(bodies[0]["body"]))
        decisions = self.conn.execute("SELECT posting_id, ruleset_version, lane, provisional FROM current_decision "
                                      "ORDER BY posting_id").fetchall()
        self.assertEqual(len(decisions), 3)
        self.assertEqual({d["ruleset_version"] for d in decisions}, {ruleset_version()})
        lanes = {d["posting_id"]: d["lane"] for d in decisions}
        self.assertEqual(lanes, {self.pid(SWE): "engineering", self.pid(SALES): "not_engineering",
                                 self.pid(DATA): "engineering"})
        self.assertEqual({d["provisional"] for d in decisions}, {1})
        board = self.board()
        self.assertEqual((board["last_read"], board["last_read_at"], board["etag"], board["error_streak"]),
                         ("ok", T0, '"A"', 0))
        self.assertEqual(board["next_read_at"], add_seconds(T0, FLOOR))
        self.assertEqual(board["change_interval_s"], FLOOR)
        read, = self.conn.execute("SELECT * FROM board_read").fetchall()
        self.assertEqual((read["result"], read["postings"], read["at"]), ("ok", 3, T0))
        self.assertEqual(self.count("read_queue"), 0)  # ruling 2: #32 owns every queue write
        self.assertFalse(self.conn.in_transaction)

    def test_the_crawler_writes_no_queue_rows(self):
        self.read(ok())
        self.read(ok(CHANGED, '"B"'), at(1))
        self.assertEqual(self.count("read_queue"), 0)

    def test_second_read_with_304_is_unchanged_and_touches_no_posting(self):
        self.read(ok())
        before = self.conn.execute("SELECT * FROM posting ORDER BY posting_id").fetchall()
        decisions = self.conn.execute("SELECT * FROM decision ORDER BY posting_id").fetchall()
        with mock.patch.object(ingest, "decide_postings", wraps=ingest.decide_postings) as decide:
            out = self.read(status_response(304), at(1))
        decide.assert_not_called()
        self.assertEqual(self.client.calls[0][2].get("If-None-Match"), '"A"')
        self.assertEqual(out.result, "unchanged")
        self.assertEqual((out.new, out.changed, out.closed, out.decided, out.bodies), (0, 0, 0, 0, 0))
        self.assertEqual([tuple(r) for r in self.conn.execute("SELECT * FROM posting ORDER BY posting_id")],
                         [tuple(r) for r in before])
        self.assertEqual([tuple(r) for r in self.conn.execute("SELECT * FROM decision ORDER BY posting_id")],
                         [tuple(r) for r in decisions])
        board = self.board()
        self.assertEqual((board["last_read"], board["etag"]), ("unchanged", '"A"'))
        self.assertEqual(board["next_read_at"], add_seconds(at(1), out.interval_s))
        self.assertEqual(self.conn.execute("SELECT postings FROM board_read WHERE result = 'unchanged'").fetchone()[0], 3)

    def test_a_changed_body_is_redecided_and_made_provisional(self):
        self.read(ok())
        swe = self.pid(SWE)
        self.make_final(swe)
        out = self.read(ok(CHANGED, '"B"'), at(1))
        self.assertEqual((out.result, out.new, out.changed, out.closed, out.bodies), ("ok", 1, 0, 1, 2))
        body = self.conn.execute("SELECT body, body_sha256 FROM posting_body WHERE posting_id = ?", (swe,)).fetchone()
        self.assertIn("We sponsor H-1B visas.", body["body"])
        self.assertEqual(body["body_sha256"], body_sha256(body["body"]))
        decision = self.decision(swe)
        self.assertEqual((decision["provisional"], decision["reader_version"]), (1, None))
        self.assertEqual(decision["sponsorship"], "yes")  # decided again on the new body
        self.assertEqual(self.posting(SALES)["close_reason"], "gone_from_board")
        self.assertIsNotNone(self.decision(self.pid(BACKEND)))
        self.assertEqual(self.board()["etag"], '"B"')
        self.assertEqual(self.count("read_queue"), 0)

    def test_a_whitespace_only_body_change_does_nothing(self):
        self.read(ok())
        swe = self.pid(SWE)
        self.make_final(swe)
        stored = self.conn.execute("SELECT * FROM posting_body WHERE posting_id = ?", (swe,)).fetchone()

        def spaced(jobs):
            jobs[0]["content"] = jobs[0]["content"].replace("&lt;p&gt;We are", "&lt;p&gt;\n\n   We   are  ")

        with mock.patch.object(ingest, "decide_postings", wraps=ingest.decide_postings) as decide:
            out = self.read(ok(board_json(spaced), '"B"'), at(1))
        self.assertEqual((out.result, out.bodies, out.decided, out.changed), ("ok", 0, 0, 0))
        decide.assert_not_called()
        self.assertEqual(tuple(self.conn.execute("SELECT * FROM posting_body WHERE posting_id = ?", (swe,)).fetchone()),
                         tuple(stored))
        decision = self.decision(swe)
        self.assertEqual((decision["provisional"], decision["reader_version"]), (0, "reader-1"))

    def test_undecided_posting_is_decided_on_the_next_ok_read(self):
        self.read(ok())
        data = self.pid(DATA)
        self.conn.execute("DELETE FROM decision WHERE posting_id = ?", (data,))
        out = self.read(ok(JOBS, '"B"'), at(1))
        self.assertEqual(out.decided, 1)
        self.assertIsNotNone(self.decision(data))

    def test_a_body_and_its_decision_are_one_unit(self):
        self.read(ok())
        swe = self.pid(SWE)
        old_body = self.conn.execute("SELECT body FROM posting_body WHERE posting_id = ?", (swe,)).fetchone()[0]
        with mock.patch.object(ingest, "decide_postings", side_effect=RuntimeError("crash in the decide")):
            with self.assertRaises(RuntimeError):
                self.read(ok(CHANGED, '"B"'), at(1))
        # The crash undid the body with its decision, so the stored body never runs ahead of the decision on it.
        self.assertEqual(self.conn.execute("SELECT body FROM posting_body WHERE posting_id = ?", (swe,)).fetchone()[0],
                         old_body)
        self.assertEqual(self.board()["etag"], '"A"')  # the crash kept the old validators: the board is listed again
        self.read(ok(CHANGED, '"B"'), at(1.1))
        self.assertIn("H-1B", self.conn.execute("SELECT body FROM posting_body WHERE posting_id = ?",
                                                (swe,)).fetchone()[0])
        self.assertEqual(self.decision(swe)["sponsorship"], "yes")

    def test_a_retitle_and_its_decision_are_one_unit(self):
        self.read(ok())
        sales = self.pid(SALES)

        def retitle(jobs):
            jobs[1]["title"] = "Software Engineer I"

        retitled = board_json(retitle)
        with mock.patch.object(ingest, "decide_postings", side_effect=RuntimeError("crash in the decide")):
            with self.assertRaises(RuntimeError):
                self.read(ok(retitled, '"B"'), at(1))
        self.assertEqual(self.posting(SALES)["title"], "Account Executive")  # undone with its decision
        self.read(ok(retitled, '"B"'), at(1.1))
        self.assertEqual(self.posting(SALES)["title"], "Software Engineer I")
        self.assertEqual(self.decision(sales)["lane"], "engineering")
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM posting_body WHERE posting_id = ?", (sales,)).fetchone())


class ClosingTests(IngestTestCase):
    def test_gone_posting_closes_after_ok_only(self):
        self.read(ok())
        for answer, kind in ((status_response(500), "error"), (status_response(304), "unchanged")):
            with self.subTest(read=kind):
                out = self.read(answer, at(1))
                self.assertEqual((out.result, out.closed), (kind, 0))
                self.assertEqual(self.open_postings(), 3)
        out = self.read(ok(CHANGED, '"B"'), at(2))
        self.assertEqual(out.closed, 1)
        sales = self.posting(SALES)
        self.assertEqual((sales["closed_at"], sales["close_reason"]), (at(2), "gone_from_board"))

    def test_error_never_closes_and_three_errors_raise_the_item(self):
        self.read(ok())
        html = text_response("<html><body>Just a moment...</body></html>", headers={"content-type": "text/html"})
        for i, answer in enumerate((html, status_response(500), html), start=1):
            out = self.read(answer, at(i))
            self.assertEqual((out.result, out.closed, out.interval_s), ("error", 0, None))
            self.assertEqual(self.open_postings(), 3)
            self.assertEqual(len(self.items("board_unreadable")), 1 if i == 3 else 0)
        board = self.board()
        self.assertEqual((board["last_read"], board["error_streak"], board["etag"]), ("error", 3, '"A"'))

    def test_first_empty_keeps_postings_second_closes_them(self):
        self.read(ok())
        out = self.read(ok(EMPTY, None), at(1))
        self.assertEqual((out.result, out.closed), ("empty", 0))
        self.assertEqual(self.open_postings(), 3)
        self.assertEqual(self.board()["empty_streak"], 1)
        self.read(ok(JOBS, '"B"'), at(2))  # an ok read in between resets the count
        self.assertEqual(self.board()["empty_streak"], 0)
        self.read(ok(EMPTY, None), at(3))
        self.assertEqual(self.open_postings(), 3)
        out = self.read(ok(EMPTY, None), at(4))
        self.assertEqual((out.result, out.closed), ("empty", 3))
        self.assertEqual(self.open_postings(), 0)
        reasons = {r[0] for r in self.conn.execute("SELECT close_reason FROM posting WHERE board_id = ?",
                                                    (self.board_id,))}
        self.assertEqual(reasons, {"board_empty_twice"})

    def test_empty_twice_then_the_same_board_returns_reopens_postings(self):
        self.read(ok(JOBS, '"A"'))
        self.read(ok(EMPTY, None), at(1))
        self.read(ok(EMPTY, None), at(2))
        self.assertEqual(self.open_postings(), 0)
        out = self.read(ok(JOBS, '"A"'), at(3))
        self.assertNotIn("If-None-Match", self.client.calls[0][2])  # the empty reads cleared the validators
        self.assertEqual((out.result, out.reopened, out.new), ("ok", 3, 0))
        self.assertEqual(self.open_postings(), 3)
        self.assertEqual({tuple(r) for r in self.conn.execute(
            "SELECT closed_at, close_reason FROM posting WHERE board_id = ?", (self.board_id,))}, {(None, None)})


class ErrorReadTests(IngestTestCase):
    def test_a_malformed_posting_makes_the_read_an_error_and_writes_no_posting(self):
        out = self.read(ok(board_json(lambda jobs: jobs[1].pop("title"))))
        self.assertEqual(out.result, "error")
        self.assertEqual(self.count("posting"), 0)
        self.assertEqual(self.count("decision"), 0)
        board = self.board()
        self.assertEqual((board["last_read"], board["error_streak"], board["etag"]), ("error", 1, ""))

    def test_adapter_exception_becomes_an_error_read(self):
        class Broken:
            ats = "greenhouse"

            def list(self, token, validators):
                raise KeyError("jobs")

            def body(self, token, job):
                raise AssertionError("no body after a failed list")

        out = self.read(None, adapter=Broken())
        self.assertEqual(out.result, "error")
        board = self.board()
        self.assertEqual((board["last_read"], board["error_streak"]), ("error", 1))
        self.assertIn("KeyError", board["last_error"])
        self.assertEqual(self.count("posting"), 0)

    def test_host_tripped_propagates_and_records_no_read(self):
        class Tripped:
            ats = "greenhouse"

            def list(self, token, validators):
                raise HostTripped("greenhouse.io")

            def body(self, token, job):
                return None

        with self.assertRaises(HostTripped):
            self.read(None, adapter=Tripped())
        board = self.board()
        self.assertEqual((board["last_read"], board["error_streak"]), (None, 0))
        self.assertEqual(self.count("board_read"), 0)

    def test_read_board_refuses_to_start_inside_a_transaction(self):
        self.conn.execute("BEGIN")
        try:
            with self.assertRaises(ValueError):
                read_board(self.conn, load_board(self.conn, self.board_id), GreenhouseAdapter(FakeClient({})),
                           run_id=start_job(self.conn, "read_boards"), now=T0, cfg=self.cfg)
        finally:
            self.conn.execute("ROLLBACK")


class DetailBackfillTests(IngestTestCase):
    def setUp(self):
        super().setUp()
        self.greenhouse_board = self.board_id
        self.board_id = seed_board(self.conn, self.company, "workday", "acme.wd5/Careers")
        self.jobs = [detail_job("e1", "Software Engineer I", "2026-10-20"),
                     detail_job("e2", "Backend Engineer I", "2026-10-19"),
                     detail_job("e3", "Software Engineer, New Grad", "2026-10-18"),
                     detail_job("s1", "Account Executive", "2026-10-21")]

    def key(self, native_id):
        return self.pid(native_id, "workday:acme.wd5/Careers")

    def has_body(self, native_id):
        return self.conn.execute("SELECT 1 FROM posting_body WHERE posting_id = ?",
                                 (self.key(native_id),)).fetchone() is not None

    def test_detail_backfill_fetches_bodies_for_new_engineering_postings_only(self):
        cfg = dataclasses.replace(self.cfg, detail_per_read=2)
        unchanged = ReadResult("unchanged", (), Validators("", ids_fingerprint(j.native_id for j in self.jobs)))
        adapter = DetailAdapter(self.conn, [detail_ok(self.jobs), unchanged, unchanged])
        out = self.read(None, adapter=adapter, cfg=cfg)
        self.assertEqual(adapter.body_calls, ["e1", "e2"])  # newest engineering first, at most two
        self.assertEqual((out.new, out.bodies), (4, 2))
        self.assertEqual(out.decided, 6)  # four on their titles, then two again on their bodies
        out = self.read(None, at(1), adapter=adapter, cfg=cfg)
        self.assertEqual(out.result, "unchanged")
        self.assertEqual(adapter.body_calls, ["e1", "e2", "e3"])  # the rest comes on the next read
        self.assertEqual((out.bodies, out.decided), (1, 1))
        self.read(None, at(2), adapter=adapter, cfg=cfg)
        self.assertEqual(adapter.body_calls, ["e1", "e2", "e3"])
        self.assertFalse(self.has_body("s1"))
        self.assertTrue(all(self.has_body(i) for i in ("e1", "e2", "e3")))
        self.assertEqual(self.decision(self.key("e1"))["sponsorship"], "yes")  # decided on the fetched body

    def test_a_level_fit_body_is_fetched_again_after_the_refetch_age(self):
        unchanged = ReadResult("unchanged", (), Validators("", ids_fingerprint(j.native_id for j in self.jobs)))
        adapter = DetailAdapter(self.conn, [detail_ok(self.jobs), unchanged, unchanged])
        self.read(None, adapter=adapter)
        self.assertEqual(sorted(adapter.body_calls), ["e1", "e2", "e3"])
        self.read(None, at(29), adapter=adapter)
        self.assertEqual(len(adapter.body_calls), 3)
        adapter.texts["e1"] = "Body of e1, rewritten. US citizenship is required."
        e1 = self.key("e1")
        self.make_final(e1)
        out = self.read(None, at(31), adapter=adapter)
        self.assertEqual(sorted(adapter.body_calls[3:]), ["e1", "e2", "e3"])
        self.assertEqual(out.bodies, 1)  # only e1's text changed
        decision = self.decision(e1)
        self.assertEqual((decision["citizenship"], decision["provisional"], decision["reader_version"]), (1, 1, None))

    def test_a_detail_ats_retitle_is_decided_and_gets_its_body_after_a_crash(self):
        adapter = DetailAdapter(self.conn, [detail_ok(self.jobs)])
        self.read(None, adapter=adapter)
        self.assertNotIn("s1", adapter.body_calls)
        retitled = [*self.jobs[:3], detail_job("s1", "Software Engineer I", "2026-10-21")]
        adapter.results = [detail_ok(retitled), detail_ok(retitled)]
        with mock.patch.object(ingest, "decide_postings", side_effect=RuntimeError("crash in the decide")):
            with self.assertRaises(RuntimeError):
                self.read(None, at(1), adapter=adapter)
        self.read(None, at(1.1), adapter=adapter)
        self.assertEqual(self.decision(self.key("s1"))["lane"], "engineering")
        self.assertIn("s1", adapter.body_calls)
        self.assertTrue(self.has_body("s1"))

    def test_a_host_trip_during_the_backfill_keeps_the_read(self):
        class TripsOnSecondBody(DetailAdapter):
            def body(self, token, job):
                if len(self.body_calls) == 1:
                    raise HostTripped("myworkdayjobs.com")
                return super().body(token, job)

        adapter = TripsOnSecondBody(self.conn, [detail_ok(self.jobs)])
        out = self.read(None, adapter=adapter)
        self.assertEqual((out.result, out.tripped, out.bodies), ("ok", "myworkdayjobs.com", 1))
        board = self.board()
        self.assertEqual(board["last_read"], "ok")
        self.assertEqual(board["ids_fingerprint"], ids_fingerprint(j.native_id for j in self.jobs))
        self.assertIsNotNone(board["next_read_at"])
        self.assertEqual(self.count("board_read"), 1)
        self.assertTrue(self.has_body("e1"))
        self.assertEqual(self.decision(self.key("e1"))["sponsorship"], "yes")  # the stored body was decided

    def test_no_transaction_is_open_during_any_request(self):
        def answer(method, url, headers, body):
            self.assertFalse(self.conn.in_transaction)
            return ok()

        out = self.read(answer, board_id=self.greenhouse_board)
        self.assertEqual(out.result, "ok")
        adapter = DetailAdapter(self.conn, [detail_ok(self.jobs)])  # its list and body assert the same
        self.read(None, adapter=adapter)
        self.assertEqual(len(adapter.body_calls), 3)


class CadenceTests(IngestTestCase):
    def test_new_engineering_posting_resets_the_interval_to_the_floor(self):
        self.read(ok())
        out = self.read(ok(JOBS, '"B"'), at(30))  # nothing new for 30 days: the interval grows
        self.assertEqual(out.interval_s, 30 * 86400 // 8)
        self.assertEqual(self.board()["next_read_at"], add_seconds(at(30), 30 * 86400 // 8))
        out = self.read(ok(CHANGED, '"C"'), at(31))  # a new engineering posting: first_seen_at is now
        self.assertEqual(out.interval_s, FLOOR)
        board = self.board()
        self.assertEqual((board["change_interval_s"], board["next_read_at"]), (FLOOR, add_seconds(at(31), FLOOR)))

    def test_error_sets_next_read_at_by_backoff(self):
        self.read(ok())
        for i, wait in enumerate((21600, 43200, 86400, 86400), start=1):
            out = self.read(status_response(500), at(i))
            board = self.board()
            self.assertEqual(out.interval_s, None)
            self.assertEqual(board["next_read_at"], add_seconds(at(i), wait))
            self.assertEqual(board["change_interval_s"], FLOOR)  # an error leaves the interval alone


if __name__ == "__main__":
    unittest.main()
