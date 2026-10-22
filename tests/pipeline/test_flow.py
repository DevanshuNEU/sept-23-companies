"""The pipeline flow test every stream extends (#30 Task 6).

`test_flow_runs_every_registered_stage` builds a fresh harness, runs each registered stage in order, and after each
one calls the invariant its stream declared with `@invariant("<stage>")`. How a stream plugs in is stated once, in the
`tests/pipeline/harness.py` docstring. An invariant counts only the rows its own stage made or its own fixture
companies, never a global total, because earlier stages may have put other rows in the store.
"""
import importlib.util
import json
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from outreach.crawl.stale import Stale, load_stale
from outreach.net import FetchError
from outreach.rules.version import ruleset_version
from outreach.store import is_shared_key
from outreach.store.writes import body_sha256
from tests.fakes import json_response, status_response, text_response
from tests.pipeline import stages_crawl, stages_rules
import tests.pipeline.harness as harness_module
from tests.pipeline.harness import STAGE_NAMES, PipelineHarness
from tests.storetest import seed_company, seed_posting

INVARIANTS = {}


def invariant(stage):
    """Register the check that runs right after `stage`, on the harness (`check(h)`)."""
    def add(fn):
        if stage not in STAGE_NAMES:
            raise ValueError(f"{stage!r} is not a pipeline stage; stages: {STAGE_NAMES}")
        if stage in INVARIANTS:
            raise ValueError(f"stage {stage!r} already has an invariant: {INVARIANTS[stage].__name__}")
        INVARIANTS[stage] = fn
        return fn
    return add


SOURCES_FIXTURE = ("Clearpath Inc", "Greenfield AI Inc", "Kestrel Robotics Inc")


def _fixture_companies(h):
    """The fixture companies the sources stages made: {name: company_id}, by the `lca` source link."""
    rows = h.conn.execute("SELECT c.name, c.company_id FROM company c JOIN company_source s USING (company_id) "
                          "WHERE s.source_name = 'lca'").fetchall()
    return {r["name"]: r["company_id"] for r in rows}


@invariant("discover")
def sources_discover_made_the_three_fixture_companies(h):
    ids = _fixture_companies(h)
    assert sorted(ids) == sorted(SOURCES_FIXTURE), f"discover made {sorted(ids)}"
    marks = ",".join("?" * len(ids))
    fein = h.conn.execute(f"SELECT company_id, alias_key FROM company_alias WHERE source = 'fein' "
                          f"AND company_id IN ({marks})", tuple(ids.values())).fetchall()
    assert [(r["company_id"], r["alias_key"]) for r in fein] == [(ids["Kestrel Robotics Inc"], "123456780")], \
        "only the own-FEIN employer gets a fein alias; the two sharing one get none"
    assert is_shared_key(h.conn, "fein", "333333333"), "the shared FEIN is recorded as shared"
    for name, company_id in ids.items():
        n = h.conn.execute("SELECT count(*) FROM evidence WHERE company_id = ? AND fact = 'lca'",
                           (company_id,)).fetchone()[0]
        assert n == 1, f"{name} has {n} lca evidence rows, not 1"


@invariant("resolve")
def sources_resolve_merged_nothing(h):
    ids = _fixture_companies(h)
    assert len(ids) == 3
    merged = h.conn.execute("SELECT count(*) FROM company WHERE merged_into IS NOT NULL").fetchone()[0]
    assert merged == 0, "resolve merged a company"
    items = h.conn.execute("SELECT count(*) FROM review_item WHERE kind = 'merge_candidate'").fetchone()[0]
    assert items == 0, "the shared FEIN raised a merge_candidate item"


@invariant("find_boards")
def sources_find_boards_made_the_one_board(h):
    rows = h.conn.execute("SELECT * FROM board").fetchall()
    assert len(rows) == 1, f"find_boards made {len(rows)} boards, not 1"
    (board,) = rows
    assert board["ats"] == "greenhouse" and board["token"] == "kestrelrobotics" == board["token"].lower()
    assert board["company_id"] == _fixture_companies(h)["Kestrel Robotics Inc"]
    alias = h.conn.execute("SELECT company_id, source FROM company_alias WHERE alias_key = ?",
                           ("greenhouse:" + board["token"],)).fetchone()
    assert alias is not None and tuple(alias) == (board["company_id"], "ats")
    assert (board["last_read"], board["next_read_at"], board["active"]) == (None, None, 1), "the board is due now"


@invariant("read_boards")
def crawl_read_the_one_board(h):
    """Only the `kestrelrobotics` board's own postings are counted, whatever other streams put in the store."""
    board = h.conn.execute("SELECT * FROM board WHERE ats = 'greenhouse' AND token = ?",
                           (stages_crawl.TOKEN,)).fetchone()
    assert board is not None, "no kestrelrobotics board to read"
    assert (board["last_read"], board["error_streak"], board["empty_streak"], board["active"]) == ("ok", 0, 0, 1)
    assert board["next_read_at"] is not None, "the first read schedules the next one"
    reads = h.conn.execute("SELECT result, postings FROM board_read WHERE board_id = ?",
                           (board["board_id"],)).fetchall()
    assert [tuple(r) for r in reads] == [("ok", 2)], f"board_read rows: {[tuple(r) for r in reads]}"
    rows = h.conn.execute("SELECT * FROM posting WHERE board_id = ? ORDER BY posting_id",
                          (board["board_id"],)).fetchall()
    assert [r["title"] for r in rows] == ["Software Engineer, New Grad", "Account Executive"], \
        f"the board's postings are {[r['title'] for r in rows]}"
    assert all(r["closed_at"] is None for r in rows), "a first read closes nothing"
    ids = [r["posting_id"] for r in rows]
    marks = ",".join("?" * len(ids))
    lanes = {r["posting_id"]: (r["lane"], r["provisional"], r["reader_version"]) for r in h.conn.execute(
        f"SELECT posting_id, lane, provisional, reader_version FROM current_decision WHERE posting_id IN ({marks})",
        ids)}
    assert lanes == {ids[0]: ("engineering", 1, None), ids[1]: ("not_engineering", 1, None)}, \
        f"decisions {lanes}: each posting has a provisional current decision, with no reader version"
    stored = h.conn.execute("SELECT body, body_sha256 FROM posting_body WHERE posting_id = ?", (ids[0],)).fetchone()
    assert stored is not None and stored["body_sha256"] == body_sha256(stored["body"]), "the body's hash is stored"
    assert "warehouse robots" in stored["body"] and "<p>" not in stored["body"], "the body is plain text"
    queued = h.conn.execute(f"SELECT count(*) FROM read_queue WHERE posting_id IN ({marks})", ids).fetchone()[0]
    assert queued == 0, "the crawler writes no read_queue row (the reader's sweep owns the queue)"
    assert not load_stale(h.conn, date.fromisoformat(h.crawl.last_tick_at()[:10]), company_ids=[board["company_id"]]), \
        "a fresh 2026 posting that the board lists is not stale"
    job = h.conn.execute("SELECT status FROM job_run WHERE job = 'read_boards' ORDER BY run_id DESC").fetchone()
    assert job is not None and job[0] == "ok", "the tick finished ok"


@invariant("rules")
def rules_made_the_current_ruleset(h):
    row = h.conn.execute("SELECT ruleset_version FROM ruleset WHERE is_current = 1").fetchone()
    assert row is not None and row[0] == ruleset_version(), "the rules stage leaves the code's ruleset current"
    last = h.conn.execute("SELECT status FROM job_run WHERE job = 'rebuild' ORDER BY run_id DESC").fetchone()
    assert last is not None and last[0] == "ok", "the rules stage's rebuild finished ok"


class HarnessCase(unittest.TestCase):
    def harness(self, **kwargs):
        h = PipelineHarness(**kwargs)
        self.addCleanup(h.close)
        return h


class SourcesFlowTests(HarnessCase):
    def test_sources_flow_invariants(self):
        h = self.harness()
        self.assertEqual([n for n, _ in h.stages][:3], ["discover", "resolve", "find_boards"])
        for name in ("discover", "resolve", "find_boards"):
            self.assertIn(name, INVARIANTS)
        for name, run in h.stages[:3]:
            run()
            INVARIANTS[name](h)  # after each stage, not only at the end

    def test_the_sources_stages_run_on_the_fixtures_with_no_network(self):
        h = self.harness()
        with mock.patch("socket.socket", side_effect=AssertionError("the sources stages opened a socket")):
            h.run(through="find_boards")
        statuses = {r["job"]: r["status"] for r in h.conn.execute("SELECT job, status FROM job_run")}
        self.assertEqual((statuses["discover"], statuses["resolve"], statuses["find_boards"]), ("ok", "ok", "ok"))


def _snapshot(h):
    return [tuple(r) for r in h.conn.execute(
        "SELECT posting_id, title, last_seen_at, closed_at FROM posting ORDER BY posting_id")]


def _stale_today(h, board_id):
    company = h.conn.execute("SELECT company_id FROM board WHERE board_id = ?", (board_id,)).fetchone()[0]
    return load_stale(h.conn, date.fromisoformat(h.crawl.last_tick_at()[:10]), company_ids=[company])


class CrawlFlowTests(HarnessCase):
    """The crawler's stage, through the sources stages that run before it. Each test runs the stages up to and
    including `read_boards`, then asks the stage for more ticks (`h.crawl.run(answer)`, a day apart)."""

    def flow(self):
        h = self.harness()
        self.assertEqual([n for n, _ in h.stages][:4], ["discover", "resolve", "find_boards", "read_boards"])
        for name, run in h.stages:
            run()
            if name in INVARIANTS:
                INVARIANTS[name](h)
            if name == "read_boards":
                break
        return h

    def rows(self, h):
        return h.conn.execute("SELECT * FROM posting WHERE board_id = ? ORDER BY posting_id",
                              (h.crawl.board_id(),)).fetchall()

    def board(self, h):
        return h.conn.execute("SELECT * FROM board WHERE board_id = ?", (h.crawl.board_id(),)).fetchone()

    def last_result(self, h):
        return h.conn.execute("SELECT result FROM board_read WHERE board_id = ? ORDER BY rowid DESC",
                              (h.crawl.board_id(),)).fetchone()[0]

    def test_read_boards_reads_the_board_the_sources_stage_made_and_creates_none(self):
        h = self.flow()
        boards = h.conn.execute("SELECT board_id, token FROM board").fetchall()
        self.assertEqual([r["token"] for r in boards], ["kestrelrobotics"])
        self.assertEqual(h.crawl.client.requests, [stages_crawl.list_url()])

    def test_read_boards_seeds_its_own_board_only_when_there_is_none(self):
        h = self.harness(register_streams=False)
        stages_crawl.register(h)
        self.assertEqual([n for n, _ in h.stages], ["read_boards"])
        self.assertEqual(h.conn.execute("SELECT count(*) FROM board").fetchone()[0], 0)
        h.run()
        self.assertEqual(h.conn.execute("SELECT count(*) FROM board").fetchone()[0], 1)
        INVARIANTS["read_boards"](h)
        h.run()
        self.assertEqual(h.conn.execute("SELECT count(*) FROM board").fetchone()[0], 1)

    def test_the_read_boards_stage_runs_on_the_fixture_with_no_network(self):
        h = self.harness()
        with mock.patch("socket.socket", side_effect=AssertionError("the read_boards stage opened a socket")):
            h.run(through="read_boards")
        self.assertEqual(h.crawl.client.unrouted, [])

    def test_flow_read_boards_second_read_is_unchanged_and_writes_no_posting_rows(self):
        h = self.flow()
        before = _snapshot(h)
        self.assertEqual(len(before), 2)
        h.crawl.run()
        self.assertEqual(self.last_result(h), "unchanged")
        self.assertEqual(h.crawl.client.calls[0][2].get("If-None-Match"), stages_crawl.ETAG)
        self.assertEqual(_snapshot(h), before)
        self.assertEqual(self.board(h)["last_read"], "unchanged")

    def test_flow_read_boards_writes_no_read_queue_row_and_a_changed_body_is_provisional_again(self):
        h = self.flow()
        engineering = self.rows(h)[0]["posting_id"]
        self.assertEqual(h.conn.execute("SELECT count(*) FROM read_queue").fetchone()[0], 0)
        before = h.conn.execute("SELECT body_sha256 FROM posting_body WHERE posting_id = ?",
                                (engineering,)).fetchone()[0]
        changed = json.loads(h.crawl.body)
        changed["jobs"][0]["content"] += "&lt;p&gt;We also run a paid summer cohort.&lt;/p&gt;"
        h.conn.execute("UPDATE decision SET provisional = 0, reader_version = 'flow-test' WHERE posting_id = ?",
                       (engineering,))  # a verified reading had decided it
        h.crawl.run(json_response(changed, etag='"flow-2"'))
        after = h.conn.execute("SELECT body_sha256 FROM posting_body WHERE posting_id = ?",
                               (engineering,)).fetchone()[0]
        self.assertNotEqual(after, before)
        decision = h.conn.execute("SELECT provisional, reader_version FROM current_decision WHERE posting_id = ?",
                                  (engineering,)).fetchone()
        self.assertEqual(tuple(decision), (1, None))
        self.assertEqual(h.conn.execute("SELECT count(*) FROM read_queue").fetchone()[0], 0)

    def test_flow_read_boards_error_never_closes(self):
        h = self.flow()
        answers = (status_response(500), status_response(403), FetchError("network", "boom"),
                   text_response("<html>Just a moment...</html>", headers={"content-type": "text/html"}))
        for i, answer in enumerate(answers, start=1):
            with self.subTest(answer=type(answer).__name__, i=i):
                h.crawl.run(answer)
                self.assertEqual(self.last_result(h), "error")
                self.assertTrue(all(r["closed_at"] is None for r in self.rows(h)))
                self.assertEqual(self.board(h)["last_read"], "error")
                self.assertEqual(self.board(h)["error_streak"], i)
                self.assertEqual(h.conn.execute("SELECT count(*) FROM read_queue").fetchone()[0], 0)
                self.assertEqual(_stale_today(h, h.crawl.board_id()), {})

    def test_flow_read_boards_a_2016_posting_is_stale_and_stays_open(self):
        h = self.harness()
        h.run(through="find_boards")
        fixture = Path(__file__).resolve().parent.parent / "fixtures" / "crawl" / "greenhouse_2016.json"
        old = json.loads(fixture.read_text(encoding="utf-8"))["jobs"][0]
        listed = json.loads(stages_crawl.fixture_jobs(h))
        listed["jobs"].append(old)
        listed["meta"]["total"] = len(listed["jobs"])  # the adapter refuses a list that disagrees with meta.total
        h.crawl.run(json_response(listed, etag='"flow-old"'))
        self.assertEqual(self.last_result(h), "ok")
        rows = self.rows(h)
        self.assertEqual(len(rows), 3)
        two016 = [r for r in rows if r["posted_on"] == "2016-03-02"]
        self.assertEqual(len(two016), 1)
        posting = two016[0]
        self.assertIsNotNone(h.conn.execute("SELECT 1 FROM current_decision WHERE posting_id = ?",
                                            (posting["posting_id"],)).fetchone())
        self.assertEqual(_stale_today(h, h.crawl.board_id()),
                         {posting["posting_id"]: Stale(("old",), "posted 2016-03-02, over 12 months")})
        self.assertTrue(all(r["closed_at"] is None for r in rows))

    def test_flow_read_boards_a_first_empty_read_makes_postings_stale_and_the_second_closes_them(self):
        h = self.flow()
        first_read_day = h.crawl.last_tick_at()[:10]
        h.crawl.run(h.crawl.empty())
        self.assertEqual(self.last_result(h), "empty")
        rows = self.rows(h)
        self.assertTrue(all(r["closed_at"] is None for r in rows))
        stale = _stale_today(h, h.crawl.board_id())
        self.assertEqual(sorted(stale), sorted(r["posting_id"] for r in rows))
        reason = f"not on the board at its latest read (last listed {first_read_day} or later)"
        self.assertEqual({s.reason for s in stale.values()}, {reason})
        self.assertEqual({s.kinds for s in stale.values()}, {("not_on_board",)})
        h.crawl.run(h.crawl.empty())
        rows = self.rows(h)
        self.assertEqual({r["close_reason"] for r in rows}, {"board_empty_twice"})
        self.assertTrue(all(r["closed_at"] is not None for r in rows))
        self.assertEqual(_stale_today(h, h.crawl.board_id()), {})


class FlowTests(HarnessCase):
    def test_flow_runs_every_registered_stage(self):
        h = self.harness()
        names = [name for name, _ in h.stages]
        self.assertEqual(names, sorted(names, key=STAGE_NAMES.index), "stages register in pipeline order")
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("rules", names)
        self.run_flow(h)
        self.assert_store_migrated(h)

    def test_a_flow_with_no_stages_registered_still_has_a_migrated_store(self):
        h = self.harness(register_streams=False)
        self.assertEqual(h.stages, [])
        h.run()
        self.assert_store_migrated(h)

    def run_flow(self, h):
        """Run each stage and then its invariant, and stop at the first failure so later stages do not cascade."""
        for name, run in h.stages:
            try:
                run()
                if name in INVARIANTS:
                    INVARIANTS[name](h)
            except Exception as err:
                self.fail(f"stage {name!r} failed, later stages were not run: {type(err).__name__}: {err}")

    def test_the_flow_stops_at_the_first_failed_stage(self):
        h = self.harness(register_streams=False)
        ran = []

        def boom():
            raise RuntimeError("broke")

        h.stages += [("discover", lambda: ran.append("discover")), ("resolve", boom),
                     ("find_boards", lambda: ran.append("find_boards"))]
        with mock.patch.dict(INVARIANTS, clear=True), self.assertRaises(AssertionError) as caught:  # fake stages
            self.run_flow(h)
        self.assertEqual(ran, ["discover"])
        self.assertIn("'resolve'", str(caught.exception))

    def assert_store_migrated(self, h):
        tables = {r[0] for r in h.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for needed in ("company", "posting", "decision", "read_queue", "posting_reading", "work_tier",
                       "company_score", "outreach_touch"):
            self.assertIn(needed, tables)
        self.assertEqual(h.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_every_invariant_names_a_stage(self):
        self.assertTrue(set(INVARIANTS) <= set(STAGE_NAMES))

    def test_run_through_stops_after_the_named_stage(self):
        h = self.harness(register_streams=False)
        ran = []
        h.stages += [("discover", lambda: ran.append("discover")), ("rules", lambda: ran.append("rules")),
                     ("plan", lambda: ran.append("plan"))]
        h.run(through="rules")
        self.assertEqual(ran, ["discover", "rules"])
        with self.assertRaises(ValueError):
            h.run(through="rank")

    def test_fixture_resolves_a_file_and_refuses_a_missing_one(self):
        h = self.harness(register_streams=False)
        for name in ("lca_extract.csv", "board_list.json", "posting_body.txt"):
            self.assertTrue(h.fixture(name).is_file(), name)
        with self.assertRaises(FileNotFoundError):
            h.fixture("nope.json")

    def test_harnesses_do_not_share_a_store(self):
        a, b = self.harness(register_streams=False), self.harness(register_streams=False)
        seed_company(a.conn, "Only In A")
        self.assertEqual(b.conn.execute("SELECT count(*) FROM company").fetchone()[0], 0)


class RegistrationTests(HarnessCase):
    def test_streams_and_their_stages_are_the_agreed_map(self):
        self.assertEqual(harness_module.STREAM_STAGES, {
            "sources": ("discover", "resolve", "find_boards"), "crawl": ("read_boards",),
            "reader": ("read_postings",), "rules": ("rules",), "ranking": ("rank",), "plan": ("plan",)})
        flat = [n for names in harness_module.STREAM_STAGES.values() for n in names]
        self.assertEqual(tuple(flat), STAGE_NAMES)

    def test_a_missing_stream_module_is_skipped_but_a_broken_one_is_not(self):
        with mock.patch.object(harness_module, "STREAM_STAGES", {"rules": ("rules",), "ghost": ("plan",)}):
            h = self.harness()
        self.assertEqual([n for n, _ in h.stages], ["rules"])
        with mock.patch.object(harness_module.importlib, "import_module",
                               side_effect=ModuleNotFoundError("No module named 'zzz'", name="zzz")):
            with self.assertRaises(ModuleNotFoundError):
                PipelineHarness()

    def build_with(self, stream_stages, *modules):
        with mock.patch.object(harness_module, "STREAM_STAGES", stream_stages), \
                mock.patch.object(harness_module.importlib, "import_module", side_effect=list(modules)):
            return PipelineHarness()

    def test_a_stream_may_only_register_its_own_stage_names_in_order(self):
        def adds(name):
            return SimpleNamespace(register=lambda h: h.stages.append((name, lambda: None)))

        two = {"rules": ("rules",), "ghost": ("plan", "rank")}
        for bad in ("discover", "tidy_up", "rules", "read_boards"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.build_with(two, stages_rules, adds(bad))
        h = self.build_with(two, stages_rules, adds("plan"))
        self.assertEqual([n for n, _ in h.stages], ["rules", "plan"])
        h.close()

    def test_crawl_cannot_register_rank_and_a_stream_cannot_register_a_name_it_does_not_own(self):
        crawl_registers_rank = SimpleNamespace(register=lambda h: h.stages.append(("rank", lambda: None)))
        with self.assertRaises(ValueError) as caught:
            self.build_with({"crawl": ("read_boards",)}, crawl_registers_rank)
        self.assertIn("crawl", str(caught.exception))

    def test_stages_in_the_wrong_order_across_streams_are_refused(self):
        late = SimpleNamespace(register=lambda h: h.stages.append(("plan", lambda: None)))
        early = SimpleNamespace(register=lambda h: h.stages.append(("rank", lambda: None)))
        with self.assertRaises(ValueError):
            self.build_with({"plan": ("plan",), "ranking": ("rank",)}, late, early)


COMBINE_EXISTS = importlib.util.find_spec("outreach.reader.combine") is not None


class RulesStageTests(HarnessCase):
    def test_rules_stage_registers_one_stage_named_rules(self):
        h = self.harness(register_streams=False)
        stages_rules.register(h)
        self.assertEqual([n for n, _ in h.stages], ["rules"])

    def test_rules_stage_decides_every_open_posting(self):
        h = self.harness(register_streams=False)
        stages_rules.register(h)
        company = seed_company(h.conn, "Kestrel Robotics")
        posting = seed_posting(h.conn, company, "Software Engineer, New Grad", "We sponsor work visas.", "Boston, MA")
        h.run()
        row = h.conn.execute(
            "SELECT d.provisional FROM decision d JOIN ruleset r ON r.ruleset_version = d.ruleset_version "
            "AND r.is_current = 1 WHERE d.posting_id = ?", (posting,)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 1)

    @unittest.skipIf(COMBINE_EXISTS, "the reader exists: the combiner path is the one that runs")
    def test_rules_stage_calls_rebuild_alone_while_the_reader_is_missing(self):
        h = self.harness(register_streams=False)
        stages_rules.register(h)
        with mock.patch.object(stages_rules, "rebuild") as rebuild:
            h.run()
        rebuild.assert_called_once_with(h.conn)

    def test_rules_stage_passes_the_combiner_then_applies_all_when_the_reader_exists(self):
        calls = []
        combiner = object()
        fake = SimpleNamespace(reader_combiner=lambda conn: calls.append(("combiner", conn)) or combiner,
                               apply_all=lambda conn: calls.append(("apply_all", conn)))
        h = self.harness(register_streams=False)
        stages_rules.register(h)
        with mock.patch.dict("sys.modules", {"outreach.reader.combine": fake}), \
                mock.patch.object(stages_rules, "rebuild",
                                  side_effect=lambda conn, **kw: calls.append(("rebuild", kw.get("combiner")))):
            h.run()
        self.assertEqual(calls, [("combiner", h.conn), ("rebuild", combiner), ("apply_all", h.conn)])

    @unittest.skipUnless(COMBINE_EXISTS, "needs outreach.reader.combine (#32 Task 4)")
    def test_rules_stage_keeps_reader_decisions(self):
        # Written against #32's Task 4 and Task 2 names (apply_reading, ReaderConfig); #32 adjusts the seeding
        # lines here if the merged names differ. A decision made final by a stored verified reading is still
        # final after the stage.
        import dataclasses
        import json
        from outreach.reader import combine
        from outreach.reader.config import load_reader_config
        from outreach.reader.types import PostingReading
        from outreach.store.writes import body_sha256
        from tests.test_contracts import reading_json

        h = self.harness(register_streams=False)
        stages_rules.register(h)
        body = h.fixture("posting_body.txt").read_text(encoding="utf-8")
        company = seed_company(h.conn, "Kestrel Robotics")
        posting = seed_posting(h.conn, company, "Software Engineer, New Grad", body, "Boston, MA")
        h.run()
        raw = reading_json()
        raw.update(posting_id=posting, body_sha256=body_sha256(body))
        reading = PostingReading.from_json(raw)
        h.conn.execute(
            "INSERT INTO posting_reading(posting_id, body_sha256, reader_version, read_at, fields, verified) "
            "VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), ?, 1)",
            (posting, reading.body_sha256, reading.reader_version, json.dumps(reading.to_json())))
        cfg = dataclasses.replace(load_reader_config(), promoted_version=reading.reader_version)
        combine.apply_reading(h.conn, posting, cfg=cfg)
        before = h.conn.execute("SELECT provisional, reader_version FROM current_decision WHERE posting_id = ?",
                                (posting,)).fetchone()
        self.assertEqual(before["provisional"], 0)
        with mock.patch.object(combine, "load_reader_config", return_value=cfg, create=True):
            h.run()
        after = h.conn.execute("SELECT provisional, reader_version FROM current_decision WHERE posting_id = ?",
                               (posting,)).fetchone()
        self.assertEqual(tuple(after), tuple(before))


if __name__ == "__main__":
    unittest.main()
