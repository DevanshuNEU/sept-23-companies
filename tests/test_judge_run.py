"""The judge's run: who it judges, the cache window, the off switch, stops, and the CLI (Part of #6 and #15). The
model is a stub runner and the pages come from a stub reader: no test calls claude or reaches the network."""
import dataclasses
import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from outreach.judge import __main__ as cli
from outreach.judge.config import load_judge_config
from outreach.judge.fetch import Fetched, unavailable
from outreach.judge.run import run_judge, select_targets
from outreach.plan.claude_run import RunResult
from outreach.ranking.common import canonical_ids
from outreach.ranking.judged import load_judgments
from tests.plan_seed import seed_scored_company
from tests.storetest import fresh_store

T0 = datetime(2026, 10, 27, 10, 10, tzinfo=timezone.utc)
ME = "## Who\n- Devanshu, AI engineer: MCP servers, evals.\n- ASK: secret question.\n"
HOME_TEXT = "Acme builds email inboxes for AI agents, so an agent can send and receive mail on its own."


def cfg(**over):
    return dataclasses.replace(load_judge_config(), enabled=True, **over)


class StubReader:
    """Pages by URL: a text is an ok page, None is unavailable. Records every read."""

    def __init__(self, pages=None):
        self.pages = dict(pages or {})
        self.reads: list[str] = []

    def read(self, url, kind):
        self.reads.append(url)
        text = self.pages.get(url, HOME_TEXT if url.endswith("/") else None)
        if text is None:
            return unavailable(kind, url, "HTTP 503", "2026-10-27T10:10:00Z")
        return Fetched({"kind": kind, "url": url, "status": "ok", "why": "", "text": text,
                        "fetched_at": "2026-10-27T10:10:00Z", "sha256": "x"}, "<html></html>")

    def about_url(self, home):
        return None


class StubRunner:
    """Answers each call with a judgment for the company in the message, or with what `answer` returns."""

    def __init__(self, answer=None, fit=72):
        self.calls: list[tuple[str, dict, str]] = []
        self.answer, self.fit = answer, fit

    def __call__(self, system_prompt, user_message, model):
        msg = json.loads(user_message)
        self.calls.append((system_prompt, msg, model))
        if self.answer is not None:
            return self.answer(msg)
        ok = [p for p in msg["pages"] if p["status"] == "ok"]
        evidence = [{"page": ok[0]["kind"], "quote": " ".join(ok[0]["text"].split()[:5])}] if ok else []
        body = {"company_id": msg["company"]["company_id"], "fit": self.fit, "builds": "Builds email inboxes for agents.",
                "domains": ["ai_agents"], "why": "Agent tooling is his work.", "risk": "No visa statement.",
                "level_ok": "unknown", "evidence": evidence}
        return ok_result(json.dumps(body))


def ok_result(text: str) -> RunResult:
    return RunResult(0, json.dumps({"type": "result", "is_error": False, "result": text}), "")


class Base(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()

    def company(self, name, score, *, domain=None, body=None, lanes=("level_role",)):
        cid = seed_scored_company(self.conn, name, score, lanes)
        if domain:
            self.conn.execute(
                "INSERT INTO evidence(company_id, fact, value, confidence, source_kind, recorded_at) "
                "VALUES (?, 'domain', ?, 'confirmed', 'website', '2026-10-01T00:00:00Z')", (cid, domain))
        if body:
            pid = self.conn.execute("SELECT posting_id FROM posting WHERE company_id = ?", (cid,)).fetchone()[0]
            self.conn.execute("INSERT INTO posting_body(posting_id, body) VALUES (?, ?)", (pid, body))
        return cid

    def judge(self, runner=None, reader=None, *, at=T0, config=None, **kw):
        self.runner = runner or StubRunner()
        self.reader = reader or StubReader()
        return run_judge(self.conn, self.runner, self.reader, cfg=config or cfg(), me_text=ME, now=at, **kw)

    def judged(self):
        return self.conn.execute("SELECT company_id, detail FROM event WHERE kind = 'judged' ORDER BY event_id").fetchall()


class OffSwitch(Base):
    def test_off_touches_nothing(self):
        self.company("Acme", 90, domain="acme.example")
        got = self.judge(config=dataclasses.replace(load_judge_config(), enabled=False))
        self.assertEqual("off", got.status)
        self.assertIn("judge is off", got.line())
        self.assertEqual([], self.runner.calls)
        self.assertEqual([], self.reader.reads)
        self.assertEqual(0, self.conn.execute("SELECT count(*) FROM event").fetchone()[0])


class Selection(Base):
    def test_top_n_of_the_ranking_not_hidden_not_closed_at_most_per_run(self):
        a = self.company("A", 90, domain="a.example")
        b = self.company("B", 80, domain="b.example")
        self.company("C", 70, domain="c.example")
        hidden = self.company("Hidden", 95, domain="h.example")
        self.conn.execute("UPDATE company_score SET signals = json_set(signals, '$.hide', json('{\"kind\":\"no_go\"}')) "
                          "WHERE company_id = ?", (hidden,))
        closed = self.company("Closed", 99, domain="x.example")
        self.conn.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?", (closed,))
        due, _fresh = select_targets(self.conn, canonical_ids(self.conn), cfg(top_n=2, per_run=2), now=T0,
                                     version="jv-1")
        self.assertEqual([a, b], [t.company_id for t in due])
        got = self.judge(config=cfg(top_n=3, per_run=1))
        self.assertEqual(1, len(self.runner.calls))
        self.assertEqual((1, 1), (got.calls, got.judged))
        self.assertEqual([a], [r["company_id"] for r in self.judged()])

    def test_named_companies_only(self):
        self.company("Acme Labs", 90, domain="a.example")
        b = self.company("Bolt", 10, domain="b.example")
        got = self.judge(config=cfg(top_n=1, per_run=1), names=["bolt", "Nobody"])
        self.assertEqual([b], [r["company_id"] for r in self.judged()])
        self.assertTrue(any("Nobody" in line for line in got.lines))

    def test_dry_run_lists_and_touches_nothing(self):
        self.company("Acme", 90, domain="acme.example")
        got = self.judge(dry_run=True, config=dataclasses.replace(load_judge_config(), enabled=False))
        self.assertEqual("dry_run", got.status)
        self.assertTrue(any("Acme" in line and "not judged yet" in line for line in got.lines))
        self.assertEqual(([], []), (self.runner.calls, self.reader.reads))
        self.assertEqual(0, self.conn.execute("SELECT count(*) FROM event").fetchone()[0])


class Judging(Base):
    def test_a_judgment_is_stored_from_the_pages_with_his_profile(self):
        cid = self.company("Acme", 90, domain="acme.example", body="Founding Engineer. New grads OK. " * 10)
        got = self.judge()
        self.assertEqual(("done", 1, 1), (got.status, got.judged, got.calls))
        system, msg, model = self.runner.calls[0]
        self.assertIn("MCP servers, evals", system)
        self.assertNotIn("secret question", system)
        self.assertEqual("claude-sonnet-5-5", model)
        self.assertEqual({"home", "posting"}, {p["kind"] for p in msg["pages"]})
        self.assertEqual(["https://acme.example/"], self.reader.reads, "the stored body is used, not fetched")
        j = load_judgments(self.conn, canonical_ids(self.conn))[cid]
        self.assertEqual((72, False), (j.fit, j.thin))

    def test_no_domain_and_a_failed_posting_page_is_judged_thin(self):
        cid = self.company("Acme", 90)
        got = self.judge()
        self.assertEqual(1, got.judged)
        msg = self.runner.calls[0][1]
        self.assertTrue(all(p["status"] == "unavailable" for p in msg["pages"]))
        self.assertIn("no domain", " ".join(p["why"] for p in msg["pages"]))
        self.assertTrue(load_judgments(self.conn, canonical_ids(self.conn))[cid].thin)

    def test_inside_the_cache_window_nothing_is_fetched_or_called(self):
        self.company("Acme", 90, domain="acme.example")
        self.judge()
        got = self.judge(at=T0 + timedelta(days=13))
        self.assertEqual(("no_targets", [], []), (got.status, self.runner.calls, self.reader.reads))

    def test_after_the_window_unchanged_pages_reuse_the_judgment_with_no_call(self):
        cid = self.company("Acme", 90, domain="acme.example")
        self.judge()
        got = self.judge(at=T0 + timedelta(days=14))
        self.assertEqual((0, 1), (got.calls, got.reused))
        self.assertIn("https://acme.example/", self.reader.reads, "the pages are read again after the window")
        rows = self.judged()
        self.assertEqual(2, len(rows))
        self.assertTrue(json.loads(rows[1]["detail"])["reused"])
        self.assertEqual(72, load_judgments(self.conn, canonical_ids(self.conn))[cid].fit)

    def test_after_the_window_changed_pages_are_judged_again(self):
        self.company("Acme", 90, domain="acme.example")
        self.judge()
        reader = StubReader({"https://acme.example/": "Acme now builds sensors for factories, with a big new team."})
        got = self.judge(reader=reader, runner=StubRunner(fit=20), at=T0 + timedelta(days=15))
        self.assertEqual((1, 1, 0), (got.calls, got.judged, got.reused))

    def test_a_changed_posting_inside_the_window_reuses_the_homepage_and_reads_the_new_posting(self):
        cid = self.company("Acme", 90, domain="acme.example")
        self.judge()
        self.conn.execute("UPDATE posting SET title = 'New Grad Engineer', url = 'https://jobs.example.com/new' "
                          "WHERE company_id = ?", (cid,))
        got = self.judge(at=T0 + timedelta(days=3))
        self.assertEqual(1, got.calls)
        self.assertEqual(["https://jobs.example.com/new"], self.reader.reads, "the homepage is not fetched again")
        self.assertEqual(HOME_TEXT, next(p["text"] for p in self.runner.calls[0][1]["pages"] if p["kind"] == "home"))

    def test_a_homepage_that_redirected_is_still_not_fetched_again_inside_the_window(self):
        cid = self.company("Acme", 90, domain="acme.example")

        class Moved(StubReader):
            def read(self, url, kind):
                got = super().read(url, kind)
                return Fetched({**got.page, "url": url.replace("://", "://www.")}, got.markup)

        self.judge(reader=Moved())
        self.conn.execute("UPDATE posting SET url = 'https://jobs.example.com/new' WHERE company_id = ?", (cid,))
        self.judge(at=T0 + timedelta(days=3))
        self.assertEqual(["https://jobs.example.com/new"], self.reader.reads)

    def test_a_new_judge_version_judges_again_from_the_stored_pages(self):
        self.company("Acme", 90, domain="acme.example")
        self.judge()
        got = self.judge(config=cfg(model="claude-other"), at=T0 + timedelta(days=2))
        self.assertEqual(1, got.calls)
        self.assertEqual([], self.reader.reads, "every page was read inside the window: none is fetched again")


class Refusals(Base):
    def test_a_quote_that_is_not_on_the_page_refuses_and_waits_out_the_window(self):
        cid = self.company("Acme", 90, domain="acme.example")
        bad = StubRunner(answer=lambda msg: ok_result(json.dumps({
            "company_id": msg["company"]["company_id"], "fit": 99, "builds": "Builds rockets.", "domains": ["other"],
            "why": "x.", "risk": "y.", "level_ok": "yes", "evidence": [{"page": "home", "quote": "we build rockets now"}]})))
        got = self.judge(runner=bad)
        self.assertEqual((0, 1), (got.judged, got.refused))
        self.assertEqual([], self.judged())
        refusal = self.conn.execute("SELECT * FROM event WHERE kind = 'judge_refused'").fetchone()
        self.assertEqual((cid, "claude"), (refusal["company_id"], refusal["actor"]))
        again = self.judge(at=T0 + timedelta(days=1))
        self.assertEqual(("no_targets", []), (again.status, self.runner.calls))

    def test_a_session_limit_or_a_login_stops_at_once(self):
        self.company("A", 90, domain="a.example")
        self.company("B", 80, domain="b.example")
        for text, status in (("You have hit your usage limit", "session_limit"), ("Not logged in", "auth")):
            with self.subTest(status=status):
                runner = StubRunner(answer=lambda msg, t=text: RunResult(1, "", t))
                got = self.judge(runner=runner)
                self.assertEqual(status, got.status)
                self.assertEqual(1, len(runner.calls))
                self.assertEqual([], self.judged())

    def test_failed_calls_in_a_row_stop_the_run(self):
        for n in range(4):
            self.company(f"C{n}", 90 - n, domain=f"c{n}.example")
        runner = StubRunner(answer=lambda msg: RunResult(1, "", "boom"))
        got = self.judge(runner=runner)
        self.assertEqual(("failed", 2), (got.status, len(runner.calls)))


class _Keep:
    """The test's connection, which the CLI may not close."""

    def __init__(self, conn):
        self.conn = conn

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def close(self):
        pass


class Cli(Base):
    def main(self, *argv, live=False, judge_cfg=None):
        out = io.StringIO()
        with mock.patch.object(cli, "load_judge_config", return_value=judge_cfg or cfg()), \
                mock.patch.object(cli.live_checkout, "is_live", return_value=live), \
                mock.patch.object(cli, "connect", return_value=_Keep(self.conn)), \
                mock.patch.object(cli, "_start_caffeinate"):
            code = cli.main(list(argv), stdout=out, runner=StubRunner(), reader=StubReader(), me_text=ME)
        return code, out.getvalue()

    def test_off_says_so_and_exits_zero_without_opening_the_store(self):
        with mock.patch.object(cli, "connect", side_effect=AssertionError("opened")):
            out = io.StringIO()
            with mock.patch.object(cli, "load_judge_config", return_value=load_judge_config()):
                self.assertEqual(0, cli.main(["run"], stdout=out))
        self.assertIn("judge is off", out.getvalue())

    def test_a_real_run_refuses_outside_the_live_checkout(self):
        self.company("Acme", 90, domain="acme.example")
        with self.assertRaises(SystemExit) as cm:
            self.main("run")
        self.assertIn("live checkout", str(cm.exception))
        self.assertEqual([], self.judged())

    def test_a_real_run_in_the_live_checkout(self):
        self.company("Acme", 90, domain="acme.example")
        code, out = self.main("run", "--limit", "1", live=True)
        self.assertEqual(0, code)
        self.assertIn("1 judged", out)

    def test_dry_run_works_anywhere(self):
        self.company("Acme", 90, domain="acme.example")
        code, out = self.main("run", "--dry-run")
        self.assertEqual(0, code)
        self.assertIn("Acme", out)
        self.assertEqual([], self.judged())

    def test_show_lists_the_top_with_the_judge_line(self):
        self.company("Acme", 90, domain="acme.example")
        self.company("Bolt", 80, domain="b.example")
        self.main("run", "--companies", "Acme", live=True)
        code, out = self.main("show", "--top", "5")
        self.assertEqual(0, code)
        self.assertIn("Acme", out)
        self.assertIn("Fit 72", out)
        self.assertIn("Bolt", out)
        self.assertIn("not judged yet", out)


if __name__ == "__main__":
    unittest.main()


class Job(unittest.TestCase):
    def test_the_template_renders_to_the_judge_run_at_0610(self):
        import plistlib
        from pathlib import Path

        from outreach.deploy.jobs import NEW_JOBS, render
        root = Path(__file__).resolve().parent.parent
        job = next(j for j in NEW_JOBS if j.label == "com.devanshu.outreach-judge")
        self.assertFalse(job.required)
        text = (root / job.template).read_text(encoding="utf-8")
        self.assertFalse(any(d in text for d in (chr(0x2013), chr(0x2014))))
        plist = plistlib.loads(render(text, live_root="/live", log_path="/logs/judge.log", label=job.label).encode())
        self.assertEqual(["/live/.venv/bin/python", "-m", "outreach.judge", "run"], plist["ProgramArguments"])
        self.assertEqual({"Hour": 6, "Minute": 10}, plist["StartCalendarInterval"])
        self.assertEqual(("/logs/judge.log",) * 2, (plist["StandardOutPath"], plist["StandardErrorPath"]))
        self.assertEqual("/live", plist["WorkingDirectory"])
