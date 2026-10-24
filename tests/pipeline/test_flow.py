"""The pipeline flow test every stream extends (#30 Task 6).

`test_flow_runs_every_registered_stage` builds a fresh harness, runs each registered stage in order, and after each
one calls the invariant its stream declared with `@invariant("<stage>")`. How a stream plugs in is stated once, in the
`tests/pipeline/harness.py` docstring. An invariant counts only the rows its own stage made or its own fixture
companies, never a global total, because earlier stages may have put other rows in the store.
"""
import importlib.util
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from outreach.crawl.stale import Stale, load_stale
from outreach.net import FetchError
from outreach.ranking.confidence import LEVELS
from outreach.ranking.config import default_ranker_config
from outreach.ranking.job import run_rank
from outreach.ranking.scores import hidden_list, latest_version, load_scores, ranked_list
from outreach.rules.version import ruleset_version
from outreach.store import is_shared_key
from outreach.store.writes import body_sha256
from tests.fakes import json_response, status_response, text_response
from outreach.reader import combine as reader_combine
from outreach.reader.queue import TIER_PROVISIONAL_HIDE, next_batch, release
from outreach.reader.version import reader_version
from outreach.rules import passes
from outreach.rules.rebuild import stored_decision
from tests.pipeline import stages_crawl, stages_ranking, stages_reader, stages_rules, stages_sending
import tests.pipeline.harness as harness_module
from tests.pipeline.harness import STAGE_NAMES, PipelineHarness
from tests.ranking_seed import EMPLOYERS, POSTINGS, seed_ranking_fixture
from tests.storetest import fresh_store, seed_company, seed_posting

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
    crawl_left_the_switched_off_boards_alone(h)


def crawl_left_the_switched_off_boards_alone(h):
    """#46: a board on a switched-off ATS keeps its state through a tick. Part of the `read_boards` invariant."""
    off = h.crawl.off_board_ids()
    assert {"smartrecruiters", "pinpoint"} <= set(h.crawl.cfg.ats_off), "the flow runs with both readers off"
    if _fixture_companies(h).get(stages_crawl.OFF_COMPANY):
        ats = {r[0] for r in h.conn.execute(f"SELECT ats FROM board WHERE board_id IN ({','.join('?' * len(off))})",
                                            off)}
        assert ats == set(h.crawl.cfg.ats_off), f"a switched-off board on every switched-off ATS, got {sorted(ats)}"
    assert h.crawl.off_snapshot() == h.crawl.off_before, "the tick changed a board on a switched-off ATS"
    assert h.crawl.report.switched_off == len(off), \
        f"the tick counts {h.crawl.report.switched_off} switched-off boards, not {len(off)}"
    asked = [u for u in h.crawl.client.requests if "smartrecruiters" in u or "pinpointhq" in u]
    assert not asked, f"the tick asked a switched-off host: {asked}"


def _reader_posting(h):
    """The hide posting the reader stage seeded, with its current decision and its queue and reading rows."""
    pid = h.reader.posting_id
    decision = h.conn.execute("SELECT * FROM current_decision WHERE posting_id = ?", (pid,)).fetchone()
    queue = h.conn.execute("SELECT * FROM read_queue WHERE posting_id = ?", (pid,)).fetchall()
    readings = h.conn.execute("SELECT * FROM posting_reading WHERE posting_id = ?", (pid,)).fetchall()
    items = h.conn.execute("SELECT * FROM review_item WHERE kind = 'reader_disagrees' AND posting_id = ?",
                           (pid,)).fetchall()
    return SimpleNamespace(id=pid, decision=decision, queue=queue, readings=readings, items=items)


@invariant("read_postings")
def reader_read_the_hide_posting_and_with_no_promoted_version_nothing_is_final(h):
    """Counts only the postings the stage decided. The hide posting is queued (as a provisional hide, first in
    line) and read once by the fake runner; with no promoted version every decision the stage made stays
    provisional with no reader version, so the rules' hide stays a provisional hide and nothing is raised."""
    assert h.reader.runner == h.reader.fake and h.reader.calls >= 1, "the stage reads with its fake runner"
    r = _reader_posting(h)
    assert len(r.queue) == 1 and r.queue[0]["priority"] // 1_000_000 == TIER_PROVISIONAL_HIDE, \
        f"the hide posting is queued once as a provisional hide: {[tuple(q) for q in r.queue]}"
    job = h.conn.execute("SELECT status FROM job_run WHERE job = 'read_postings' ORDER BY run_id DESC").fetchone()
    assert job is not None and job[0] == "ok", "the night batch finished ok"
    if h.reader.cfg.promoted_version:
        return  # a test promoted a version: its own asserts say what is final
    marks = ",".join("?" * len(h.reader.decided))
    final = h.conn.execute(f"SELECT posting_id FROM current_decision WHERE posting_id IN ({marks}) "
                           f"AND (provisional = 0 OR reader_version IS NOT NULL)", h.reader.decided).fetchall()
    assert not final, f"no promoted version, yet final decisions for {[f[0] for f in final]}"
    items = h.conn.execute(f"SELECT count(*) FROM review_item WHERE kind = 'reader_disagrees' AND posting_id IN "
                           f"({marks})", h.reader.decided).fetchone()[0]
    assert items == 0, "no promoted version raises nothing"
    assert not passes(stored_decision(r.decision)), "the rules' hide stays a provisional hide"


@invariant("rules")
def rules_made_the_current_ruleset(h):
    row = h.conn.execute("SELECT ruleset_version FROM ruleset WHERE is_current = 1").fetchone()
    assert row is not None and row[0] == ruleset_version(), "the rules stage leaves the code's ruleset current"
    last = h.conn.execute("SELECT status FROM job_run WHERE job = 'rebuild' ORDER BY run_id DESC").fetchone()
    assert last is not None and last[0] == "ok", "the rules stage's rebuild finished ok"


def _ranking_rows(h):
    """The fixture companies' stored scores by key (a merged-away duplicate has none), in the latest version."""
    ids = h.ranking.ids
    scores = load_scores(h.conn, latest_version(h.conn))
    return {key: scores[cid] for key, cid in ids.items() if cid in scores}


@invariant("rank")
def ranking_puts_the_sponsoring_new_grad_employer_first_and_keeps_every_rule(h):
    """Counts only the fixture companies (A to U), never a global total: the earlier stages put other rows in the
    store. A live new-grad posting that says it sponsors ranks first and its reasons name both; a final refusal is
    the one rules no-go; a stale posting is never the best posting; every row says how sure it is."""
    ids = h.ranking.ids
    rows = _ranking_rows(h)
    assert "L" not in rows and set(rows) == set(ids) - {"L"}, "every fixture company but the merged one is scored"
    first = ranked_list(h.conn)[0]
    assert first.company_id == ids["A"], f"company {first.company_id} ranks first, not A ({ids['A']})"
    reasons = " | ".join(first.signals["reasons"]).lower()
    assert "new grad" in reasons and "sponsors" in reasons, f"A's reasons do not name both: {reasons}"
    marks = ",".join("?" * len(ids))
    rules_rows = h.conn.execute(f"SELECT company_id FROM no_go WHERE source = 'rules' AND company_id IN ({marks})",
                                tuple(ids.values())).fetchall()
    assert [r[0] for r in rules_rows] == [ids["E"]], f"rules no-go rows: {[r[0] for r in rules_rows]}"
    for key, row in rows.items():
        stale = {b["posting_id"] for b in row.signals["stale_postings"]}
        assert row.signals["best_posting_id"] not in stale, f"{key}: a stale posting is the best posting"
        say = row.signals["rank_reason"]
        assert say["level"] in LEVELS and say["basis"].strip(), f"{key}: the rank reason lacks a level or a basis"


@invariant("send")
def send_sends_only_what_was_approved_once(h):
    """Counts only the Sendwell Labs fixture: the approved founder email went out once, the drafted one never, the
    touch was logged on the plan's source, and a follow-up is planned seven business days on."""
    ids = h.sending_ids
    sent = [m for m, _, _ in ids.gmail.sent]
    assert [m.to for m in sent] == [stages_sending.FOUNDER_EMAIL], f"Gmail took {[m.to for m in sent]}"
    founder = h.conn.execute("SELECT * FROM message WHERE message_id = ?", (ids.founder_message,)).fetchone()
    assert founder["state"] == "sent" and founder["rfc_message_id"] == sent[0].message_id, "the founder's email is sent"
    other = h.conn.execute("SELECT state FROM message WHERE message_id = ?", (ids.other_message,)).fetchone()
    assert other["state"] == "drafted", f"the never-approved draft is {other['state']}"
    logged = h.conn.execute("SELECT channel, json_extract(context, '$.source') AS source FROM outreach_touch "
                            "WHERE person_id = ?", (ids.founder,)).fetchall()
    assert [tuple(r) for r in logged] == [("email", "plan")], f"outreach touches: {[tuple(r) for r in logged]}"
    planned = h.conn.execute("SELECT seq, state, due_on FROM message WHERE touch_id = ? AND seq >= 1",
                             (ids.founder_touch,)).fetchall()
    assert [tuple(r) for r in planned] == [(1, "planned", "2026-11-19")], f"follow-ups: {[tuple(r) for r in planned]}"
    states = h.conn.execute("SELECT m.state, COUNT(*) FROM message m JOIN touch t ON t.touch_id = m.touch_id "
                            "JOIN person p ON p.person_id = t.person_id WHERE p.company_id = ? GROUP BY m.state",
                            (ids.company,)).fetchall()
    assert {r[0]: r[1] for r in states} == {"sent": 1, "drafted": 1, "planned": 1}, \
        f"Sendwell's messages by state: {[tuple(r) for r in states]}"


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
    """The read board's postings (the switched-off fixture's posting has its own check, #46)."""
    return [tuple(r) for r in h.conn.execute(
        "SELECT posting_id, title, last_seen_at, closed_at FROM posting WHERE board_id = ? ORDER BY posting_id",
        (h.crawl.board_id(),))]


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
        off = h.crawl.off_board_ids()  # the stage's own switched-off fixture (#46), read by nobody
        self.assertEqual([r["token"] for r in boards if r["board_id"] not in off], ["kestrelrobotics"])
        self.assertEqual(h.crawl.client.requests, [stages_crawl.list_url()])

    def test_flow_read_boards_passes_with_smartrecruiters_and_pinpoint_off_and_leaves_their_boards_alone(self):
        h = self.flow()  # the read_boards invariant ran after the first tick
        self.assertEqual(len(h.crawl.off_board_ids()), 2)
        self.assertEqual(h.crawl.report.switched_off, 2)
        for answer in (None, status_response(500), h.crawl.empty()):  # later ticks, whatever the read board says
            h.crawl.run(answer)
            crawl_left_the_switched_off_boards_alone(h)

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
            "importer": ("import_history",), "sources": ("discover", "resolve", "find_boards"),
            "crawl": ("read_boards",), "reader": ("read_postings",), "rules": ("rules",), "ranking": ("rank",),
            "plan": ("plan",), "mcp": ("draft",), "sending": ("send",), "review": ("needs_you",)})
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


def _rows(h, sql, *args):
    return h.conn.execute(sql, args).fetchall()


class RankFlowTests(HarnessCase):
    """The rank stage, through every stage before it. Each test runs the stages up to and including `rank` on a
    fresh harness and reads the stored rows of the fixture companies (`h.ranking.ids`, key to company id)."""

    def flow(self):
        h = self.harness()
        self.assertIn("rank", [n for n, _ in h.stages])
        for name, run in h.stages:
            run()
            if name in INVARIANTS:
                INVARIANTS[name](h)
            if name == "rank":  # later streams' stages (plan, send, ...) are not this class's business
                break
        return h

    def row(self, h, key):
        return load_scores(h.conn, latest_version(h.conn))[h.ranking.ids[key]]

    def order(self, h):
        return [r.company_id for r in ranked_list(h.conn)]

    def place(self, h, key):
        return self.order(h).index(h.ranking.ids[key])

    def postings(self, h, key):
        return _rows(h, "SELECT * FROM posting WHERE company_id = ? ORDER BY posting_id", h.ranking.ids[key])

    def no_go(self, h, key):
        return _rows(h, "SELECT * FROM no_go WHERE company_id = ? ORDER BY no_go_id", h.ranking.ids[key])

    def points(self, row, signal):
        return row.signals["values"][signal]["points"]

    def test_rank_stage_registers_one_stage_named_rank_after_the_crawl_stage(self):
        h = self.harness(register_streams=False)
        stages_crawl.register(h)
        stages_ranking.register(h)
        self.assertEqual([n for n, _ in h.stages], ["read_boards", "rank"])
        self.assertIs(h.ranking.conn, h.conn)

    def test_flow_rank_puts_the_sponsoring_new_grad_employer_first_and_names_both(self):
        h = self.flow()
        first = ranked_list(h.conn)[0]
        self.assertEqual(first.company_id, h.ranking.ids["A"])
        reasons = " ".join(first.signals["reasons"])
        self.assertIn("new grad", reasons)
        self.assertIn("sponsors", reasons)

    def test_flow_rank_puts_the_large_later_stage_company_last_but_present(self):
        h = self.flow()
        order = self.order(h)
        self.assertEqual(order[-1], h.ranking.ids["D"])
        self.assertIn("ranked last", " ".join(self.row(h, "D").signals["reasons"]))
        self.assertIsNone(self.row(h, "D").signals["hide"])

    def test_flow_rank_puts_staffing_risk_below_a_clean_twin(self):
        h = self.flow()
        self.assertGreater(self.place(h, "C"), self.place(h, "B"))
        self.assertLessEqual(self.points(self.row(h, "C"), "staffing_risk"), -200)
        self.assertEqual(self.points(self.row(h, "B"), "staffing_risk"), 0)

    def test_flow_rank_hides_only_with_evidence(self):
        h = self.flow()
        ids = h.ranking.ids
        hidden = {r.company_id: r.signals["hide"] for r in hidden_list(h.conn)}
        # E: a final refusal. The run wrote the rules no-go row and stores the hide that row gives, as the next run
        # computes it: an active no-go for no sponsorship.
        self.assertIn(ids["E"], hidden)
        self.assertEqual((hidden[ids["E"]]["kind"], hidden[ids["E"]]["provisional"]), ("no_go", False))
        self.assertIn("no sponsorship", hidden[ids["E"]]["reason"])
        rows = self.no_go(h, "E")
        self.assertEqual([(r["reason"], r["source"], r["lifted_at"]) for r in rows], [("no_sponsorship", "rules", None)])
        self.assertTrue(rows[0]["source_ref"])
        # F: the same refusal, still provisional: hidden pending the reader, nothing written.
        self.assertEqual((hidden[ids["F"]]["kind"], hidden[ids["F"]]["provisional"]), ("no_sponsorship", True))
        self.assertEqual(self.no_go(h, "F"), [])
        # G: a lifted no-go and a final refusal: shown, ranked, and the lifted row is the only row.
        self.assertNotIn(ids["G"], hidden)
        self.assertIn(ids["G"], self.order(h))
        rows = self.no_go(h, "G")
        self.assertEqual([(r["source"], r["lifted_at"] is not None) for r in rows], [("devanshu", True)])
        # H: a clearance posting gives no role credit, the company still lists.
        self.assertNotIn(ids["H"], hidden)
        self.assertIn(ids["H"], self.order(h))
        self.assertEqual(self.points(self.row(h, "H"), "live_role"), 0)
        self.assertNotIn("level_role", self.row(h, "H").lanes)
        self.assertEqual(set(hidden) & set(ids.values()), {ids["E"], ids["F"]}, "only E and F are hidden")
        marks = ",".join("?" * len(ids))
        rules_rows = _rows(h, f"SELECT company_id FROM no_go WHERE source = 'rules' AND company_id IN ({marks})",
                           *ids.values())
        self.assertEqual([r[0] for r in rules_rows], [ids["E"]])

    def test_flow_rank_keeps_a_company_with_a_sponsoring_posting_visible_and_flags_the_conflict(self):
        h = self.flow()
        n = h.ranking.ids["N"]
        self.assertIn(n, self.order(h))
        self.assertEqual(self.no_go(h, "N"), [])
        self.assertIn("postings disagree on sponsorship", self.row(h, "N").signals["flags"])
        items = _rows(h, "SELECT * FROM review_item WHERE company_id = ? AND kind = 'flagged' AND raised_by = 'ranker' "
                         "AND state = 'open'", n)
        self.assertEqual(len(items), 1)
        refusals = _rows(h, "SELECT d.provisional FROM posting p JOIN current_decision d USING (posting_id) "
                            "WHERE p.company_id = ? AND d.sponsorship = 'no'", n)
        self.assertEqual([r[0] for r in refusals], [0], "N's refusal is final: the sponsoring posting is what saves it")

    def test_flow_rank_keeps_a_doubtful_board_company_visible_without_role_credit(self):
        h = self.flow()
        row = self.row(h, "K")
        self.assertIn(h.ranking.ids["K"], self.order(h))
        self.assertEqual(self.points(row, "live_role"), 0)
        self.assertEqual(row.lanes.count("level_role"), 0)
        self.assertGreaterEqual(row.signals["ignored_postings"], 1)
        n = row.signals["ignored_postings"]
        self.assertIn(f"{n} {'posting' if n == 1 else 'postings'} ignored: board match doubtful", row.signals["flags"])

    def test_flow_rank_follows_a_merge(self):
        h = self.flow()
        ids = h.ranking.ids
        self.assertEqual(_rows(h, "SELECT 1 FROM company_score WHERE company_id = ?", ids["L"]), [])
        self.assertEqual(_rows(h, "SELECT merged_into FROM company WHERE company_id = ?", ids["L"])[0][0], ids["B"])
        # B scores the same with and without its duplicate in the store.
        with tempfile.TemporaryDirectory() as folder:
            without = fresh_store()
            self.addCleanup(without.close)
            employers = [e for e in json.loads(EMPLOYERS.read_text(encoding="utf-8")) if e["key"] != "L"]
            postings = [p for p in json.loads(POSTINGS.read_text(encoding="utf-8")) if p["employer"] != "L"]
            paths = []
            for name, rows in (("employers.json", employers), ("postings.json", postings)):
                path = Path(folder) / name
                path.write_text(json.dumps(rows), encoding="utf-8")
                paths.append(path)
            other = seed_ranking_fixture(without, *paths)
            run_rank(without, today=stages_ranking.TODAY)
            b_without = load_scores(without, latest_version(without))[other["B"]]
        b = self.row(h, "B")
        self.assertEqual((b.score, b.lanes, b.signals["values"], b.signals["flags"]),
                         (b_without.score, b_without.lanes, b_without.signals["values"], b_without.signals["flags"]))

    def test_flow_rank_orders_the_cap_exempt_lab_above_a_company_with_only_lca_history(self):
        h = self.flow()
        self.assertLess(self.place(h, "I"), self.place(h, "J"))
        self.assertGreater(self.points(self.row(h, "I"), "cap_exempt"), 0)
        self.assertEqual(self.row(h, "J").lanes, ("reason_no_role",))

    def test_flow_rank_pay_level_from_posted_pay(self):
        h = self.flow()
        pay = self.row(h, "M").signals["values"]["pay_level"]
        self.assertEqual(pay["value"], default_ranker_config().section("pay_level")["level_2"])
        self.assertIn("level 2", pay["reason"])
        self.assertIn("posted", pay["reason"])

    def test_flow_rank_a_2016_posting_gives_no_role_credit_and_the_company_stays_listed(self):
        h = self.flow()
        row = self.row(h, "O")
        self.assertIn(h.ranking.ids["O"], self.order(h))
        self.assertEqual(row.lanes, ())
        self.assertEqual(self.points(row, "live_role"), 0)
        (posting,) = self.postings(h, "O")
        self.assertEqual(posting["posted_on"], "2016-03-02")
        self.assertEqual(row.signals["stale_postings"], [{"posting_id": posting["posting_id"], "title": posting["title"],
                                                           "reason": "posted 2016-03-02, over 12 months"}])
        self.assertTrue(any("1 stale posting not counted as live" in f for f in row.signals["flags"]))
        self.assertIsNone(row.signals["hide"])
        self.assertIsNone(row.signals["best_posting_id"])

    def test_flow_rank_ranks_the_live_posting_twin_above_the_2016_twin(self):
        h = self.flow()
        self.assertLess(self.place(h, "P"), self.place(h, "O"))
        row = self.row(h, "P")
        (live,) = [r for r in self.postings(h, "P") if r["posted_on"] == "2026-10-18"]
        (old,) = [r for r in self.postings(h, "P") if r["posted_on"] == "2016-03-02"]
        self.assertEqual(row.signals["best_posting_id"], live["posting_id"])
        self.assertEqual([b["posting_id"] for b in row.signals["stale_postings"]], [old["posting_id"]])
        self.assertIn("level_role", row.lanes)

    def test_flow_rank_a_first_empty_read_makes_the_posting_stale_and_keeps_the_company(self):
        h = self.flow()
        row = self.row(h, "Q")
        self.assertIn(h.ranking.ids["Q"], self.order(h))
        (posting,) = [p for p in self.postings(h, "Q") if p["last_seen_at"].startswith("2026-10-18")]
        stale = {b["posting_id"]: b["reason"] for b in row.signals["stale_postings"]}
        self.assertEqual(stale, {posting["posting_id"]:
                                 "not on the board at its latest read (last listed 2026-10-18 or later)"})
        self.assertEqual(row.lanes, ())
        self.assertEqual(self.points(row, "live_role"), 0)
        self.assertEqual(row.signals["values"]["live_role"]["confidence"], "medium")

    def test_flow_rank_a_failed_board_read_never_makes_a_posting_stale(self):
        h = self.flow()
        row = self.row(h, "R")
        board = _rows(h, "SELECT last_read, error_streak FROM board WHERE company_id = ?", h.ranking.ids["R"])[0]
        self.assertEqual(tuple(board), ("error", 1))
        self.assertEqual(row.signals["stale_postings"], [])
        live = row.signals["values"]["live_role"]
        self.assertGreater(live["points"], 0)
        self.assertEqual(live["confidence"], "medium")
        self.assertIn("last board read failed", live["basis"])

    def test_flow_rank_a_stale_refusal_hides_nothing(self):
        h = self.flow()
        row = self.row(h, "S")
        self.assertIn(h.ranking.ids["S"], self.order(h))
        self.assertEqual(self.no_go(h, "S"), [])
        self.assertIsNone(row.signals["hide"])
        decision = _rows(h, "SELECT d.sponsorship, d.provisional FROM posting p JOIN current_decision d USING "
                            "(posting_id) WHERE p.company_id = ? AND p.posted_on = '2016-03-02'", h.ranking.ids["S"])
        self.assertEqual([tuple(r) for r in decision], [("no", 0)], "S's 2016 refusal is a final decision")

    def test_flow_rank_every_row_says_how_sure_it_is(self):
        h = self.flow()
        scores = load_scores(h.conn, latest_version(h.conn))
        for key, cid in h.ranking.ids.items():
            if key == "L":
                continue
            signals = scores[cid].signals
            say = signals["rank_reason"]
            self.assertIn(say["level"], LEVELS, key)
            self.assertTrue(say["basis"].strip(), key)
            self.assertTrue(say["line"].startswith(say["text"]), key)
            self.assertTrue(say["line"].endswith(f"{say['level']} confidence: {say['basis']})"), key)
            self.assertEqual(signals["confidence"], {"level": say["level"], "basis": say["basis"]}, key)
            for name, part in signals["values"].items():
                self.assertIn(part["confidence"], LEVELS, (key, name))
                self.assertTrue(part["basis"].strip(), (key, name))

    def test_flow_rank_a_guess_never_reads_high_and_loses_to_the_confirmed_twin(self):
        h = self.flow()
        self.assertEqual(self.row(h, "D").signals["values"]["large_later_stage"]["confidence"], "low")
        t, u = self.row(h, "T"), self.row(h, "U")
        self.assertLess(self.place(h, "T"), self.place(h, "U"))
        self.assertEqual(self.points(t, "everify") - self.points(u, "everify"), 6.0)
        self.assertIn("guess", u.signals["values"]["everify"]["basis"])
        self.assertEqual(t.signals["values"]["everify"]["confidence"], "high")
        self.assertEqual(u.signals["values"]["everify"]["confidence"], "low")

    def test_flow_rank_is_idempotent(self):
        h = self.flow()

        def snapshot():
            return {
                "scores": _rows(h, "SELECT company_id, ranker_version, score, lanes, signals FROM company_score "
                                   "ORDER BY company_id, ranker_version"),
                "no_go": _rows(h, "SELECT * FROM no_go ORDER BY no_go_id"),
                "items": _rows(h, "SELECT * FROM review_item ORDER BY review_id"),
            }

        before = [tuple(r) for r in snapshot()["scores"]]
        again = snapshot()
        dict(h.stages)["rank"]()
        after = snapshot()
        self.assertEqual([tuple(r) for r in after["scores"]], before)
        self.assertEqual([tuple(r) for r in after["no_go"]], [tuple(r) for r in again["no_go"]])
        self.assertEqual([tuple(r) for r in after["items"]], [tuple(r) for r in again["items"]])
        self.assertTrue(after["no_go"])

    def test_the_rank_stage_seeds_the_fixture_once_and_runs_without_network(self):
        h = self.harness()
        with mock.patch("socket.socket", side_effect=AssertionError("the rank stage opened a socket")):
            h.run(through="rank")
        companies = _rows(h, "SELECT count(*) FROM company")[0][0]
        postings = _rows(h, "SELECT count(*) FROM posting")[0][0]
        ids = dict(h.ranking.ids)
        h.run(through="rank")  # a second run ranks again and seeds nothing new
        self.assertEqual(_rows(h, "SELECT count(*) FROM company")[0][0], companies)
        self.assertEqual(_rows(h, "SELECT count(*) FROM posting")[0][0], postings)
        self.assertEqual(h.ranking.ids, ids)
        self.assertEqual(len(ids), len(json.loads(EMPLOYERS.read_text(encoding="utf-8"))))

    def test_the_rank_stage_ranks_on_the_fixture_day_with_the_shipped_stale_rule(self):
        h = self.flow()
        self.assertEqual(h.ranking.report.version, latest_version(h.conn))
        self.assertEqual(stages_ranking.TODAY, date(2026, 10, 21))
        self.assertEqual(h.ranking.report.stale_postings, 3, "O, P and Q each have one stale posting")


class ReaderFlowTests(HarnessCase):
    """The reader's stage, through every stage before it. Each test sets the stage's script (and, when it says so,
    promotes the current version in the stage's config) before the stages run, then reads `h.conn`."""

    def flow(self, *, promote=False, **script):
        h = self.harness()
        self.assertEqual([n for n, _ in h.stages][:5],
                         ["discover", "resolve", "find_boards", "read_boards", "read_postings"])
        if promote:
            h.reader.promote()
        h.reader.script[stages_reader.HIDE_TITLE].update(script)
        for name, run in h.stages:
            run()
            if name in INVARIANTS:
                INVARIANTS[name](h)
            if name == "read_postings":
                break
        return h

    def test_flow_refused_quote_leaves_the_decision_provisional(self):
        h = self.flow(promote=True, sponsorship=("wont_sponsor", "no visa sponsorship is offered"))
        r = _reader_posting(h)
        self.assertEqual(r.readings, [], "a refused reading is never stored as a fact")
        self.assertEqual((r.decision["provisional"], r.decision["reader_version"]), (1, None))
        self.assertFalse(passes(stored_decision(r.decision)), "still a rules hide, read again next night")
        (queued,) = r.queue
        self.assertEqual((queued["state"], queued["attempts"]), ("queued", 1))
        self.assertIn("sponsorship", queued["last_error"])
        self.assertEqual(r.items, [])

    def test_flow_agreeing_verified_reading_makes_the_decision_final(self):
        h = self.flow(promote=True)
        r = _reader_posting(h)
        self.assertEqual([row["verified"] for row in r.readings], [1])
        self.assertEqual(r.decision["provisional"], 0)
        self.assertEqual(r.decision["reader_version"], reader_version(h.reader.cfg))
        self.assertEqual(h.reader.cfg.promoted_version, reader_version(h.reader.cfg))
        self.assertFalse(passes(stored_decision(r.decision)), "an agreed hide hides, for good")
        self.assertEqual(r.items, [])

    def test_flow_with_no_promoted_version_an_agreeing_reading_decides_nothing(self):
        h = self.flow()
        self.assertEqual(h.reader.cfg.promoted_version, "")
        r = _reader_posting(h)
        self.assertEqual([row["verified"] for row in r.readings], [1], "the reading is stored, in shadow")
        self.assertEqual((r.decision["provisional"], r.decision["reader_version"]), (1, None))
        self.assertFalse(passes(stored_decision(r.decision)))
        self.assertEqual(r.items, [])

    def test_flow_disagreement_with_a_provisional_hide_shows_the_posting_and_raises_the_review(self):
        # The plan wrote provisional = 0 here; combine.py stays stricter (ledger, batch 4-5): a disputed hide is shown
        # and stays provisional, so a later agreeing read can still decide it.
        h = self.flow(promote=True, sponsorship=("unknown", ""))
        r = _reader_posting(h)
        self.assertEqual((r.decision["provisional"], r.decision["reader_version"]), (1, None))
        self.assertTrue(passes(stored_decision(r.decision)), "shown again")
        self.assertEqual(len(r.items), 1)
        (item,) = r.items
        self.assertEqual((item["raised_by"], item["posting_id"], item["state"]), ("reader", r.id, "open"))

    def test_flow_queue_order_ignores_company_score_when_absent_and_reads_it_when_present(self):
        h = self.harness()
        h.run(through="read_boards")
        body = h.fixture("posting_hide.txt").read_text(encoding="utf-8").replace(stages_reader.REFUSAL, "")
        alder = seed_company(h.conn, "Alder Labs")
        birch = seed_company(h.conn, "Birch Works")
        first = seed_posting(h.conn, alder, "Software Engineer, New Grad", body + " Alder.", "Austin, TX")
        second = seed_posting(h.conn, birch, "Software Engineer, New Grad", body + " Birch.", "Austin, TX")
        h.reader.prepare()
        self.assertEqual(h.conn.execute("SELECT count(*) FROM company_score").fetchone()[0], 0)

        def order():
            batch = next_batch(h.conn, 10, cfg=h.reader.cfg)
            release(h.conn, batch)
            return [b.posting_id for b in batch]

        self.assertEqual(order(), [h.reader.posting_id, second, first], "no scores: newest lane 1 posting first")
        h.conn.execute("INSERT INTO company_score(company_id, ranker_version, score, signals, lanes, scored_at) "
                       "VALUES (?, 'rank-flow-test', 0.9, '{}', '[\"level_role\"]', '2026-10-22T08:00:00Z')", (alder,))
        self.assertEqual(order(), [h.reader.posting_id, first, second], "the scored company's lane 1 posting first")

    def test_flow_a_rebuild_keeps_the_reader_decisions(self):
        h = self.flow(promote=True)
        before = _reader_posting(h).decision
        self.assertEqual(before["provisional"], 0)
        calls = h.reader.calls
        with mock.patch.object(reader_combine, "load_reader_config", return_value=h.reader.cfg):
            dict(h.stages)["rules"]()
        after = _reader_posting(h).decision
        self.assertEqual((after["provisional"], after["reader_version"]), (0, before["reader_version"]))
        self.assertEqual(h.reader.calls, calls, "a rebuild makes no reader call")

    def test_read_postings_registers_one_stage_and_never_builds_the_real_runner(self):
        h = self.harness(register_streams=False)
        with mock.patch("outreach.reader.night.claude_runner", side_effect=AssertionError("real runner")):
            stages_reader.register(h)
        self.assertEqual([n for n, _ in h.stages], ["read_postings"])
        self.assertEqual(h.reader.runner, h.reader.fake)
        calls = []
        other = self.harness(register_streams=False)
        stages_reader.register(other, runner=lambda *a: calls.append(a))
        self.assertIsNot(other.reader.runner, other.reader.fake)


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
        # The reading confirms what the body says ("We sponsor work visas"): a final row needs the reader to agree.
        raw = reading_json(sponsorship=("will_sponsor", "We sponsor work visas"))
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
