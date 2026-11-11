import io
import json
import os
import unittest
from unittest import mock

from outreach.net import Response
from outreach.research import __main__ as cli
from outreach.research.gpt import CodexRunner, build_prompt, parse_reply, research_company
from outreach.research.store import fresh_company_facts
from tests.storetest import fresh_store, seed_company, seed_posting

NOW = "2026-11-10T12:00:00Z"
PAGE = "https://acme.example/blog/launch"


class FakeRunner:
    def __init__(self, text="", error=None):
        self.text, self.error, self.calls = text, error, []

    def run(self, prompt):
        self.calls.append(prompt)
        if self.error:
            raise self.error
        return self.text


class FakeClient:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, headers=None, *, follow_redirects=False, max_bytes=None):
        self.calls.append(url)
        return Response(status=200, url=url, body=self.pages[url].encode(), headers={"content-type": "text/html"})


def reply(*items):
    return json.dumps({"facts": list(items)})


GOOD = {"fact": "Acme launched a router.", "quote": "we shipped the new router", "url": PAGE, "date": "2026-10-01"}
CLIENT_PAGES = {PAGE: "<p>Today we shipped the new router for everyone.</p>"}


class GptResearchTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.cid = seed_company(self.conn, "Acme")
        seed_posting(self.conn, self.cid, "Backend Engineer")

    def test_prompt_names_the_company_and_its_postings(self):
        text = build_prompt({"name": "Acme", "domain": "acme.example"}, ["Backend Engineer", "SRE"])
        for want in ("Acme", "acme.example", "Backend Engineer", "SRE", "JSON"):
            self.assertIn(want, text)

    def test_gpt_reply_with_prose_around_json_is_parsed(self):
        bad = [{"fact": "x", "quote": "", "url": PAGE}, {"fact": "y", "quote": "q", "url": "http://a.example/"}]
        text = "Sure! Here you go:\n" + reply(GOOD, *bad, dict(GOOD, quote="q2", date="soon")) + "\nHope it helps."
        facts = parse_reply(text)
        self.assertEqual([f.quote for f in facts], ["we shipped the new router", "q2"])
        self.assertEqual([f.page_date for f in facts], ["2026-10-01", None])

    def test_bad_reply_stores_nothing_and_reports_failed(self):
        for runner in (FakeRunner("no json here"), FakeRunner(error=RuntimeError("codex timed out"))):
            res = research_company(self.conn, self.cid, runner=runner, client=FakeClient({}), now=NOW)
            self.assertEqual(res["status"], "failed")
        self.assertEqual(fresh_company_facts(self.conn, self.cid, now=NOW), [])

    def test_research_company_checks_then_stores(self):
        lie = dict(GOOD, quote="we raised a billion dollars")
        res = research_company(self.conn, self.cid, runner=FakeRunner(reply(GOOD, lie)),
                               client=FakeClient(CLIENT_PAGES), now=NOW)
        self.assertEqual((res["status"], res["kept"], len(res["rejected"])), ("stored", 1, 1))
        rows = fresh_company_facts(self.conn, self.cid, now=NOW)
        self.assertEqual([(r["quote"], r["found_by"]) for r in rows], [("we shipped the new router", "gpt")])

    def test_nothing_when_no_quote_is_on_a_page(self):
        res = research_company(self.conn, self.cid, runner=FakeRunner(reply(dict(GOOD, quote="not there"))),
                               client=FakeClient(CLIENT_PAGES), now=NOW)
        self.assertEqual((res["status"], res["kept"]), ("nothing", 0))

    def test_fresh_company_is_skipped_unless_forced(self):
        client = FakeClient(CLIENT_PAGES)
        research_company(self.conn, self.cid, runner=FakeRunner(reply(GOOD)), client=client, now=NOW)
        again = FakeRunner(reply(GOOD))
        res = research_company(self.conn, self.cid, runner=again, client=client, now=NOW)
        self.assertEqual(res["status"], "fresh")
        self.assertEqual(again.calls, [])
        research_company(self.conn, self.cid, runner=again, client=client, now=NOW, force=True)
        self.assertEqual(len(again.calls), 1)

    def test_codex_runner_fails_plainly_without_codex(self):
        with mock.patch("shutil.which", return_value=None), self.assertRaisesRegex(RuntimeError, "codex"):
            CodexRunner().run("hi")


class CliTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        path = self.conn.execute("PRAGMA database_list").fetchone()["file"]
        patch = mock.patch.dict(os.environ, {"OUTREACH_DB": path})
        patch.start()
        self.addCleanup(patch.stop)
        self.cid = seed_company(self.conn, "Acme")

    def test_real_run_outside_the_live_checkout_is_refused(self):
        runner = FakeRunner(reply(GOOD))
        with mock.patch("live_checkout.is_live", return_value=False), self.assertRaises(SystemExit):
            cli.main(["company", str(self.cid)], stdout=io.StringIO(), runner=runner)
        self.assertEqual(runner.calls, [])

    def test_dry_run_calls_nothing(self):
        runner, client, out = FakeRunner(reply(GOOD)), FakeClient({}), io.StringIO()
        with mock.patch("live_checkout.is_live", return_value=False):
            code = cli.main(["company", str(self.cid), "--dry-run"], stdout=out, runner=runner, client=client)
        self.assertEqual(code, 0)
        self.assertIn("Acme", out.getvalue())
        self.assertEqual((runner.calls, client.calls), ([], []))
        self.assertEqual(fresh_company_facts(self.conn, self.cid, now=NOW), [])

    def test_live_run_prints_one_line_per_company(self):
        out = io.StringIO()
        with mock.patch("live_checkout.is_live", return_value=True):
            code = cli.main(["company", str(self.cid)], stdout=out, runner=FakeRunner(reply(GOOD)),
                            client=FakeClient(CLIENT_PAGES), now=NOW)
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().strip(), f"company {self.cid}: stored, kept 1, rejected 0")


if __name__ == "__main__":
    unittest.main()
