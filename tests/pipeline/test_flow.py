"""The pipeline flow test every stream extends (#30 Task 6).

`test_flow_runs_every_registered_stage` builds a fresh harness, runs each registered stage in order, and after each
one calls the invariant its stream declared with `@invariant("<stage>")`. How a stream plugs in is stated once, in the
`tests/pipeline/harness.py` docstring. An invariant counts only the rows its own stage made or its own fixture
companies, never a global total, because earlier stages may have put other rows in the store.
"""
import importlib.util
import unittest
from types import SimpleNamespace
from unittest import mock

from outreach.rules.version import ruleset_version
from outreach.store import is_shared_key
from tests.pipeline import stages_rules
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
