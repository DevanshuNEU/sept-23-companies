"""The reader's night job: claude -p through an injected Runner, a cap and clean stops (#32, Task 6).

No test runs claude. Every `run_night` gets a fake Runner that answers each call from a script, and the one test of
`claude_runner` replaces `subprocess.run` and only looks at the command it would have run.
"""
import dataclasses
import io
import json
import os
import plistlib
import re
import shutil
import subprocess
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

import live_checkout
from outreach.reader import __main__ as cli
from outreach.reader import night, queue
from outreach.reader.config import load_reader_config
from outreach.reader.night import NightSummary, RunResult, classify_run, claude_runner, parse_reply, run_night
from outreach.reader.types import FIELDS, ReadRequest
from outreach.reader.version import reader_version
from outreach.rules import passes, rebuild
from outreach.rules.rebuild import stored_decision
from outreach.store import body_sha256, connect, utcnow
from tests.storetest import fresh_store, seed_company, seed_posting

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "launchd" / "com.devanshu.outreach-reader-night.plist.template"
TITLE = "Software Engineer, New Grad"
FILLER = " We build control software for warehouse robots and ship to real customers every week."
REFUSAL = "We do not sponsor visas for this role."
PARAPHRASE = "they cannot sponsor anyone at all"
QUOTED_LIMIT = "Customers never hit a usage limit on our platform."
DEAD = "2026-01-01T00:00:00Z"  # a lease taken then and never given back ran out long ago
LIMIT_TEXT = "Claude AI usage limit reached. Your limit resets at 7am."


def body(i: int, lead: str = "") -> str:
    return f"{lead} Posting number {i}.".strip() + FILLER * 8


def blank_fields() -> dict:
    out = {}
    for name in FIELDS:
        if name in ("years_low", "years_high"):
            out[name] = {"value": None, "quote": ""}
        elif name in ("start_date", "location", "pay"):
            out[name] = {"value": "", "quote": ""}
        else:
            out[name] = {"value": "unknown", "quote": ""}
    return out


def envelope(text: str, *, is_error: bool = False) -> str:
    """What `claude -p --output-format json` prints: one result object."""
    return json.dumps({"type": "result", "subtype": "success", "is_error": is_error, "result": text,
                       "session_id": "00000000-0000-0000-0000-000000000000"})


def readings(payload: dict, *, skip=(), **over) -> dict:
    """A reply with one reading per posting in `payload` (minus `skip`), every field unknown or empty, then `over`
    as (value, quote) for every reading."""
    out = []
    for p in payload["postings"]:
        if p["posting_id"] in skip:
            continue
        fields = blank_fields()
        for name, (value, quote) in over.items():
            fields[name] = {"value": value, "quote": quote}
        out.append({"posting_id": p["posting_id"], "body_sha256": p["body_sha256"], "fields": fields})
    return {"readings": out}


def ok(text: str) -> RunResult:
    return RunResult(0, envelope(text), "")


def good(_n, payload) -> RunResult:
    return ok(json.dumps(readings(payload)))


def session_limit(_n, _payload) -> RunResult:
    return RunResult(1, envelope(LIMIT_TEXT, is_error=True), "")


class Fake:
    """A Runner that answers call n (from 1) with `answer(n, payload)`, raising it when it is an exception."""

    def __init__(self, answer):
        self.answer = answer
        self.calls: list[tuple[str, dict, str]] = []

    def __call__(self, system_prompt: str, user_message: str, model: str) -> RunResult:
        payload = json.loads(user_message)
        self.calls.append((system_prompt, payload, model))
        out = self.answer(len(self.calls), payload)
        if isinstance(out, BaseException):
            raise out
        return out

    def ids(self, call: int) -> list[int]:
        return [p["posting_id"] for p in self.calls[call][1]["postings"]]

    def sizes(self) -> list[int]:
        return [len(c[1]["postings"]) for c in self.calls]


class NightCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.company = seed_company(self.conn, "Kestrel")
        base = load_reader_config()
        self.version = reader_version(base)
        self.cfg = dataclasses.replace(base, batch_size=20, max_postings_per_night=400, max_batches=25,
                                       max_consecutive_failures=2, max_attempts=3)
        self.promoted = dataclasses.replace(self.cfg, promoted_version=self.version)

    def postings(self, n: int, lead: str = "") -> list[int]:
        ids = [seed_posting(self.conn, self.company, TITLE, body(i, lead), "Boston, MA") for i in range(n)]
        rebuild(self.conn)
        return ids

    def run_night(self, runner, cfg=None, **kw) -> NightSummary:
        return run_night(self.conn, runner, cfg or self.cfg, **kw)

    def queue_rows(self):
        return self.conn.execute("SELECT * FROM read_queue ORDER BY posting_id").fetchall()

    def states(self) -> Counter:
        return Counter(r["state"] for r in self.queue_rows())

    def row(self, pid):
        return self.conn.execute("SELECT * FROM read_queue WHERE posting_id = ?", (pid,)).fetchone()

    def job(self):
        return self.conn.execute("SELECT * FROM job_run WHERE job = 'read_postings' ORDER BY run_id DESC").fetchone()

    def decision(self, pid):
        return self.conn.execute("SELECT * FROM current_decision WHERE posting_id = ?", (pid,)).fetchone()

    def stored_count(self, verified=None) -> int:
        sql = "SELECT count(*) FROM posting_reading" + ("" if verified is None else " WHERE verified = ?")
        return self.conn.execute(sql, () if verified is None else (verified,)).fetchone()[0]

    def done_with_reading(self, pid):
        """A posting already read at the current version: its queue row is done and its reading verified."""
        text = self.conn.execute("SELECT body FROM posting_body WHERE posting_id = ?", (pid,)).fetchone()[0]
        sha = body_sha256(text)
        self.conn.execute("INSERT INTO read_queue(posting_id, body_sha256, reader_version, priority, state, attempts, "
                          "enqueued_at) VALUES (?, ?, ?, 1000000, 'done', 1, ?)", (pid, sha, self.version, utcnow()))
        fields = blank_fields()
        fields["engineering"] = {"value": "yes", "quote": TITLE}
        self.conn.execute(
            "INSERT INTO posting_reading(posting_id, body_sha256, reader_version, read_at, fields, verified, "
            "refused_fields) VALUES (?, ?, ?, ?, ?, 1, '[]')",
            (pid, sha, self.version, utcnow(),
             json.dumps({"posting_id": pid, "body_sha256": sha, "reader_version": self.version, "fields": fields})))


class BatchTests(NightCase):
    def test_night_reads_the_queue_in_batches(self):
        self.postings(45)
        fake = Fake(good)
        summary = self.run_night(fake)
        self.assertEqual((summary.status, summary.batches, summary.stored, summary.partial),
                         ("queue_empty", 3, 45, 0))
        self.assertEqual(fake.sizes(), [20, 20, 5])
        self.assertEqual(self.states(), Counter(done=45))
        self.assertEqual(self.stored_count(verified=1), 45)
        prompt, payload, model = fake.calls[0]
        self.assertEqual(prompt, self.cfg.prompt_path.read_text(encoding="utf-8"))
        self.assertEqual(model, self.cfg.model)
        self.assertEqual(payload["reader_version"], self.version)
        job = self.job()
        self.assertEqual((job["status"], job["error"]), ("ok", ""))
        self.assertIsNotNone(job["finished_at"])
        self.assertEqual(json.loads(job["summary"])["stored"], 45)

    def test_night_cap_stops_the_run(self):
        self.postings(45)
        fake = Fake(good)
        summary = self.run_night(fake, dataclasses.replace(self.cfg, max_postings_per_night=30))
        self.assertEqual((summary.status, summary.batches, summary.stored), ("cap_reached", 2, 30))
        self.assertEqual(fake.sizes(), [20, 10])
        self.assertEqual(self.states(), Counter(done=30, queued=15))
        self.assertEqual(self.job()["status"], "ok")

        fake = Fake(good)
        summary = self.run_night(fake, dataclasses.replace(self.cfg, max_batches=1))
        self.assertEqual((summary.status, summary.batches), ("cap_reached", 1))
        self.assertEqual(self.states(), Counter(done=45))

    def test_model_skipping_a_posting_leaves_it_queued(self):
        self.postings(45)
        state = {}

        def answer(n, payload):
            if n == 1:
                ids = [p["posting_id"] for p in payload["postings"]]
                state["skipped"] = ids[-1]
                reply = readings(payload, skip={ids[-1]})
                # A reading for a posting this batch was not given, and a second one for a posting it was: neither
                # is submitted.
                outside = min(r["posting_id"] for r in self.queue_rows() if r["state"] == "queued")
                state["outside"] = outside
                reply["readings"].append({"posting_id": outside, "body_sha256": self.row(outside)["body_sha256"],
                                          "fields": blank_fields()})
                reply["readings"].append(dict(reply["readings"][0], fields={"engineering": "yes"}))
                # A reading under the wrong body hash comes first for one posting; the right one after it counts.
                reply["readings"].insert(0, dict(reply["readings"][1], body_sha256="cd" * 32))
                return ok(json.dumps(reply))
            return good(n, payload)

        fake = Fake(answer)
        summary = self.run_night(fake, dataclasses.replace(self.cfg, max_batches=1))
        self.assertEqual((summary.stored, summary.requeued), (19, 1))
        skipped = self.row(state["skipped"])
        self.assertEqual((skipped["state"], skipped["attempts"], skipped["lease_until"]), ("queued", 1, None))
        self.assertIn("no reading", skipped["last_error"])
        outside = self.row(state["outside"])
        self.assertEqual((outside["state"], outside["attempts"]), ("queued", 0))
        self.assertEqual(self.stored_count(), 19)

        fake = Fake(good)
        self.run_night(fake)
        self.assertIn(state["skipped"], fake.ids(0))
        self.assertEqual(self.row(state["skipped"])["state"], "done")
        self.assertEqual(self.states(), Counter(done=45))

    def test_a_lease_handed_on_during_the_call_is_left_to_its_new_holder(self):
        self.postings(3)
        later = "2099-01-01T00:00:00Z"

        def answer(n, payload):
            if n == 1:  # the call outlived its leases and a side session took the postings
                self.assertEqual(len(queue.next_batch(self.conn, 3, cfg=self.cfg, now=later)), 3)
                return ok(json.dumps(readings(payload, sponsorship=("wont_sponsor", PARAPHRASE))))
            return session_limit(n, payload)

        summary = self.run_night(Fake(answer))
        self.assertEqual((summary.stored, summary.requeued), (0, 0))
        for r in self.queue_rows():
            self.assertEqual((r["state"], r["attempts"]), ("leased", 2))  # still the side session's

    def test_requeue_keeps_the_attempt_and_leaves_a_handed_on_lease_alone(self):
        (pid,) = self.postings(1)
        queue.enqueue_missing(self.conn, cfg=self.cfg)
        (req,) = queue.next_batch(self.conn, 1, cfg=self.cfg, now=DEAD)
        self.assertEqual(queue.requeue(self.conn, [req], "skipped", attempts={pid: 2}), 0)  # not our attempt
        self.assertEqual(self.row(pid)["state"], "leased")
        self.assertEqual(queue.requeue(self.conn, [req], "skipped", attempts={pid: 1}), 1)
        self.assertEqual((self.row(pid)["state"], self.row(pid)["attempts"]), ("queued", 1))
        self.assertEqual(queue.requeue(self.conn, [req], "again"), 0)  # no longer leased


class StopTests(NightCase):
    def assert_all_back(self, attempts=0):
        rows = self.queue_rows()
        self.assertTrue(rows)
        self.assertEqual({r["state"] for r in rows}, {"queued"})
        self.assertEqual({r["lease_until"] for r in rows}, {None})
        if attempts is not None:
            self.assertEqual({r["attempts"] for r in rows}, {attempts})

    def test_session_limit_stops_cleanly_and_releases(self):
        self.postings(45)
        fake = Fake(session_limit)
        summary = self.run_night(fake)
        self.assertEqual((summary.status, summary.batches, summary.stored, summary.requeued),
                         ("session_limit", 1, 0, 20))
        self.assertIn("usage limit", summary.stopped_reason)
        self.assertEqual(len(fake.calls), 1)
        self.assert_all_back(attempts=0)
        job = self.job()
        self.assertEqual(job["status"], "partial")
        self.assertIn("usage limit", job["error"])

    def test_auth_error_stops_with_failed_job(self):
        self.postings(5)
        fake = Fake(lambda n, p: RunResult(1, "", "Not logged in. Please run /login"))
        summary = self.run_night(fake)
        self.assertEqual(summary.status, "auth")
        self.assertEqual(len(fake.calls), 1)
        self.assert_all_back(attempts=0)
        self.assertEqual(self.job()["status"], "failed")

    def test_a_quoted_phrase_in_a_good_reply_is_not_a_limit(self):
        self.postings(3, lead=QUOTED_LIMIT)
        quote = QUOTED_LIMIT.rstrip(".")
        fake = Fake(lambda n, p: ok(json.dumps(readings(p, engineering=("yes", quote)))))
        summary = self.run_night(fake)
        self.assertEqual((summary.status, summary.stored), ("queue_empty", 3))
        result = ok(json.dumps(readings(fake.calls[0][1], engineering=("yes", quote))))
        self.assertIn("usage limit", result.stdout)
        self.assertEqual(classify_run(result, session_limit_patterns=self.cfg.session_limit_patterns,
                                      auth_patterns=self.cfg.auth_patterns), "ok")

    def test_garbage_reply_releases_and_counts_a_failure(self):
        self.postings(45)
        fake = Fake(lambda n, p: ok("I could not read these postings, sorry."))
        summary = self.run_night(fake)
        self.assertEqual(summary.status, "failed")
        self.assertIn("2 failed calls in a row", summary.stopped_reason)
        # The batch of 20 is released and does not count; its postings are then read one per call, so each
        # failure after it is pinned on one posting, which keeps the attempt it used.
        self.assertEqual(fake.sizes(), [20, 1, 1])
        self.assert_all_back(attempts=None)
        attempts = Counter(r["attempts"] for r in self.queue_rows())
        self.assertEqual(attempts, Counter({0: 43, 1: 2}))
        self.assertEqual({self.row(fake.ids(n)[0])["attempts"] for n in (1, 2)}, {1})
        self.assertEqual(self.job()["status"], "failed")
        self.assertEqual(self.stored_count(), 0)

    def test_runner_timeout_is_a_failed_run(self):
        self.postings(3)
        errors = (subprocess.TimeoutExpired(["claude", "-p"], 900), RuntimeError("anything else the runner raised"))
        for err in errors:
            with self.subTest(type(err).__name__):
                fake = Fake(lambda n, p, err=err: err)
                summary = self.run_night(fake)
                self.assertEqual(summary.status, "failed")
                self.assertEqual(fake.sizes(), [3, 1, 1])  # the batch of three does not count
                self.assertNotIn("leased", self.states())
                self.assertEqual(self.job()["status"], "failed")

    def test_a_broken_cli_releases_without_spending_an_attempt_and_stops(self):
        # Final review: a missing binary or a flag the CLI does not know fails every call the same way; that is not
        # the posting's fault, so no attempt is spent and the night stops at once.
        answers = (FileNotFoundError(2, "No such file or directory", "claude"),
                   RunResult(1, "", "error: unknown option '--safe-mode'"),
                   RunResult(1, "", "zsh: command not found: claude"),
                   RunResult(1, envelope("Model claude-haiku-9 not found", is_error=True), ""))
        for answer in answers:
            with self.subTest(answer=repr(answer)[:60]):
                self.conn.execute("DELETE FROM read_queue")
                if not self.conn.execute("SELECT count(*) FROM posting").fetchone()[0]:
                    self.postings(45)
                fake = Fake(lambda n, p, answer=answer: answer)
                summary = self.run_night(fake)
                self.assertEqual((summary.status, summary.batches, summary.requeued), ("failed", 1, 20))
                self.assertIn("claude CLI", summary.stopped_reason)
                self.assertEqual(len(fake.calls), 1)
                self.assert_all_back(attempts=0)
                self.assertEqual(self.job()["status"], "failed")

    def test_a_posting_that_quotes_a_stop_phrase_is_not_a_stop(self):
        # Grok second review: a failed call whose output quotes the posting must not read as a login problem, a
        # session limit or a broken CLI. The posting takes the normal failure path and the others are read.
        others = self.postings(44)
        lead = ("Two-factor authentication, a usage limit per tenant, unknown option flags and file not found "
                "errors are what you will fix.")
        (poison,) = self.postings(1, lead=lead)
        def answer(n, payload):
            ours = [p for p in payload["postings"] if p["posting_id"] == poison]
            if ours:  # the CLI echoes the prompt it could not take, posting and all
                return RunResult(1, "", "Error: prompt is too long: " + ours[0]["body"])
            return good(n, payload)
        fake = Fake(answer)
        summary = self.run_night(fake, self.promoted)
        self.assertEqual(summary.status, "queue_empty", summary.stopped_reason)
        self.assertEqual({self.row(pid)["state"] for pid in others}, {"done"})
        self.assertEqual((self.row(poison)["state"], self.row(poison)["attempts"]), ("queued", 1))
        for _ in range(self.cfg.max_attempts):
            self.assertEqual(self.run_night(fake, self.promoted).status, "queue_empty")
        self.assertEqual(self.row(poison)["state"], "refused")

    def test_the_model_text_of_a_failed_call_is_not_matched(self):
        def kind(result):
            return classify_run(result, session_limit_patterns=self.cfg.session_limit_patterns,
                                auth_patterns=self.cfg.auth_patterns)
        quoted = json.dumps({"readings": [], "note": "the posting asks for authentication work under a usage limit"})
        self.assertEqual(kind(RunResult(1, envelope(quoted), "")), "failed")  # a reply, not the CLI's error
        self.assertEqual(kind(RunResult(1, envelope(LIMIT_TEXT, is_error=True), "")), "session_limit")
        self.assertEqual(kind(RunResult(1, "", "Not logged in. Please run /login")), "auth")
        self.assertEqual(kind(RunResult(0, LIMIT_TEXT, "")), "session_limit")

    def test_a_non_zero_exit_and_an_error_result_are_failed_runs(self):
        for result in (RunResult(2, "", "Error: something broke"), RunResult(0, envelope("boom", is_error=True), ""),
                       RunResult(0, "not json at all", "")):
            with self.subTest(result=result):
                self.assertEqual(classify_run(result, session_limit_patterns=self.cfg.session_limit_patterns,
                                              auth_patterns=self.cfg.auth_patterns), "failed")
        # The limit text as plain stdout, with no result object around it, still stops the night as a limit.
        self.assertEqual(classify_run(RunResult(0, LIMIT_TEXT, ""), session_limit_patterns=("usage limit",),
                                      auth_patterns=()), "session_limit")

    def test_skipped_night_is_safe(self):
        self.postings(45)
        self.assertEqual(self.run_night(Fake(session_limit)).status, "session_limit")
        # A run that died with leases out (the Mac slept, the process was killed): nothing gave them back.
        dead = queue.next_batch(self.conn, 20, cfg=self.cfg, now=DEAD)
        self.assertEqual(len(dead), 20)
        fake = Fake(good)
        summary = self.run_night(fake)
        self.assertEqual((summary.status, summary.stored), ("queue_empty", 45))
        self.assertEqual(self.states(), Counter(done=45))
        for req in dead:
            self.assertEqual(self.row(req.posting_id)["attempts"], 2)

    def test_a_store_error_closes_the_job_and_gives_the_leases_back(self):
        self.postings(3)
        with mock.patch.object(night, "submit", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                self.run_night(Fake(good))
        self.assert_all_back(attempts=0)
        job = self.job()
        self.assertEqual(job["status"], "failed")
        self.assertIn("disk full", job["error"])


class FailOpenTests(NightCase):
    def test_refused_quotes_requeue_and_finish_at_the_limit(self):
        ids = self.postings(45, lead=REFUSAL)
        self.assertFalse(any(passes(stored_decision(self.decision(pid))) for pid in ids))  # the rules hide them
        fake = Fake(lambda n, p: ok(json.dumps(readings(p, sponsorship=("wont_sponsor", PARAPHRASE)))))
        nights = 0
        while self.states().get("queued", 0) or not self.queue_rows():
            nights += 1
            self.assertLessEqual(nights, self.cfg.max_attempts + 1, "the night keeps reading the same postings")
            summary = self.run_night(fake, self.promoted)
            self.assertIn(summary.status, ("queue_empty", "cap_reached"))
        self.assertEqual(self.states(), Counter(refused=45))
        self.assertLessEqual(len(fake.calls), 3 * self.cfg.max_attempts)
        self.assertEqual(self.stored_count(verified=0), 45)  # partial: the paraphrased field blanked
        for pid in ids:
            row = self.decision(pid)
            self.assertTrue(passes(stored_decision(row)), pid)  # fails open: shown
            self.assertEqual((row["sponsorship"], row["provisional"]), ("unclear", 1))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM review_item WHERE kind = 'reader_disagrees'")
                         .fetchone()[0], 45)

    def test_a_poison_posting_is_isolated_and_ends_refused(self):
        others = self.postings(44)
        (poison,) = self.postings(1, lead=REFUSAL)  # a provisional hide: always first in the queue
        def answer(n, payload):
            if poison in [p["posting_id"] for p in payload["postings"]]:
                return RunResult(1, "", "Error: prompt is too long")
            return good(n, payload)
        fake = Fake(answer)
        # One night reads every other posting: the poison fails once on its own call and is left out for the rest
        # of the night, and the failed batch of several it broke does not count toward the stop.
        summary = self.run_night(fake, self.promoted)
        self.assertEqual(summary.status, "queue_empty")
        self.assertEqual({self.row(pid)["state"] for pid in others}, {"done"})
        self.assertEqual((self.row(poison)["state"], self.row(poison)["attempts"]), ("queued", 1))
        self.assertEqual(sum(poison in [p["posting_id"] for p in c[1]["postings"]] for c in fake.calls), 2)
        # One call a night after that, until it runs out of leases and ends refused, shown and flagged.
        for _ in range(self.cfg.max_attempts):
            self.assertEqual(self.run_night(fake, self.promoted).status, "queue_empty")
        self.assertEqual(self.row(poison)["state"], "refused")
        row = self.decision(poison)
        self.assertTrue(passes(stored_decision(row)))
        self.assertEqual(row["sponsorship"], "unclear")
        self.assertLessEqual(len(fake.calls), 30)


    def test_a_failed_batch_on_the_last_cap_slot_is_still_split(self):
        # Grok second review: the batch a poison posting breaks is discarded and does not count; its one-posting
        # retries run before the caps are checked again.
        for cap in ({"max_batches": 1}, {"max_postings_per_night": 20}):
            with self.subTest(**cap):
                self.conn.execute("DELETE FROM read_queue")
                self.conn.execute("DELETE FROM posting_reading")
                if not self.conn.execute("SELECT count(*) FROM posting").fetchone()[0]:
                    self.others = self.postings(44)
                    (self.poison,) = self.postings(1, lead=REFUSAL)
                poison = self.poison
                def answer(n, payload):
                    if poison in [p["posting_id"] for p in payload["postings"]]:
                        return RunResult(1, "", "Error: prompt is too long")
                    return good(n, payload)
                fake = Fake(answer)
                summary = self.run_night(fake, dataclasses.replace(self.promoted, **cap))
                self.assertEqual(summary.status, "cap_reached")
                self.assertEqual(fake.sizes(), [20] + [1] * 20)
                self.assertEqual((summary.batches, summary.stored), (20, 19))
                self.assertEqual((self.row(poison)["state"], self.row(poison)["attempts"]), ("queued", 1))
                self.assertEqual(self.states(), Counter(done=19, queued=26))

class ApplyAllTests(NightCase):
    def test_night_applies_all_after_its_batches(self):
        (pid,) = self.postings(1)
        self.done_with_reading(pid)
        rebuild(self.conn)  # a rebuild without the combiner leaves the row provisional
        self.assertEqual(self.decision(pid)["provisional"], 1)
        fake = Fake(good)
        summary = self.run_night(fake, self.promoted)
        self.assertEqual(fake.calls, [])
        self.assertEqual(summary.status, "queue_empty")
        self.assertEqual(summary.applied, 1)
        row = self.decision(pid)
        self.assertEqual((row["provisional"], row["reader_version"]), (0, self.version))

    def test_night_applies_all_when_a_session_limit_stops_it(self):
        (pid,) = self.postings(1)
        self.done_with_reading(pid)
        rebuild(self.conn)
        self.postings(3)
        summary = self.run_night(Fake(session_limit), self.promoted)
        self.assertEqual(summary.status, "session_limit")
        self.assertEqual(self.decision(pid)["provisional"], 0)


class ClassifyAndParseTests(unittest.TestCase):
    def test_classify_run_takes_pattern_lists(self):
        result = RunResult(1, "", "Quota EXHAUSTED for today")
        self.assertEqual(classify_run(result, session_limit_patterns=("quota exhausted",), auth_patterns=()),
                         "session_limit")
        self.assertEqual(classify_run(result, session_limit_patterns=("usage limit",), auth_patterns=()), "failed")
        self.assertEqual(classify_run(result, session_limit_patterns=(), auth_patterns=("QUOTA",)), "auth")
        self.assertEqual(classify_run(RunResult(0, envelope("quota exhausted"), ""),
                                      session_limit_patterns=("quota exhausted",), auth_patterns=()), "ok")

    def test_fenced_and_wrapped_json_is_parsed(self):
        reply = {"readings": [{"posting_id": 1, "body_sha256": "ab" * 32, "fields": blank_fields()}]}
        text = json.dumps(reply, indent=2)
        for wrapped in (text, f"```json\n{text}\n```", f"```\n{text}\n```",
                        f"Here are the readings:\n\n```json\n{text}\n```\nLet me know if you need more.",
                        f"Sure. {text} Done."):
            with self.subTest(wrapped=wrapped[:30]):
                self.assertEqual(parse_reply(envelope(wrapped)), reply["readings"])
        # Lines before the result object, and the list form some versions print, are read too.
        self.assertEqual(parse_reply("warning: something\n" + envelope(text)), reply["readings"])
        listed = json.dumps([{"type": "system"}, json.loads(envelope(text))])
        self.assertEqual(parse_reply(listed), reply["readings"])

    def test_unreadable_replies_raise_value_error(self):
        for stdout in ("", "not json", json.dumps({"no": "result"}), envelope("no json here"),
                       envelope(json.dumps({"other": []})), envelope(json.dumps({"readings": {"a": 1}})),
                       envelope(json.dumps({"readings": []}), is_error=True), json.dumps({"result": 5})):
            with self.subTest(stdout=stdout[:40]):
                with self.assertRaises(ValueError):
                    parse_reply(stdout)


class ClaudeRunnerTests(unittest.TestCase):
    def test_claude_runner_builds_the_command(self):
        folder = Path(tempfile.mkdtemp(prefix="outreach-reader-cwd-test-"))
        self.addCleanup(shutil.rmtree, folder, True)
        cwd = folder / "reader-cwd"
        cfg = load_reader_config()
        done = subprocess.CompletedProcess([], 0, stdout=envelope("{}"), stderr="")
        with mock.patch.object(night.subprocess, "run", return_value=done) as run:
            runner = claude_runner(cfg, cwd=cwd)
            self.assertFalse(cwd.exists())  # made on first use, not when the runner is built
            result = runner("THE PROMPT", '{"postings": []}', "claude-haiku-5-5")
        self.assertEqual(result, RunResult(0, envelope("{}"), ""))
        self.assertTrue(cwd.is_dir())
        (argv,), kwargs = run.call_args
        self.assertEqual(Path(argv[0]).name, "claude")
        self.assertEqual(argv[1:], ["-p", "--model", "claude-haiku-5-5", "--output-format", "json", "--tools", "",
                                    "--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config",
                                    "--safe-mode", "--system-prompt", "THE PROMPT"])
        self.assertNotIn("--bare", argv)  # it would turn off the seat login
        self.assertEqual(kwargs["input"], '{"postings": []}')
        self.assertEqual(kwargs["cwd"], cwd)
        self.assertEqual(kwargs["timeout"], cfg.call_timeout_s)
        self.assertTrue(kwargs["capture_output"])
        self.assertFalse(kwargs.get("shell", False))

    def test_the_default_folder_is_a_neutral_one(self):
        self.assertEqual(night.READER_CWD,
                         Path.home() / "Library" / "Application Support" / "outreach" / "reader-cwd")
        self.assertFalse(str(night.READER_CWD).startswith(str(REPO)))


class CommandTests(NightCase):
    def test_night_refuses_outside_the_live_checkout(self):
        elsewhere = tempfile.mkdtemp(prefix="outreach-not-live-")
        with mock.patch.object(live_checkout, "LIVE_ROOT", elsewhere), \
                mock.patch.object(cli, "load_reader_config", side_effect=AssertionError("read the config")), \
                mock.patch.object(cli, "connect", side_effect=AssertionError("opened the store")), \
                mock.patch("sqlite3.connect", side_effect=AssertionError("opened a database")), \
                mock.patch.object(cli.subprocess, "Popen", side_effect=AssertionError("started caffeinate")), \
                mock.patch.object(night.subprocess, "run", side_effect=AssertionError("ran claude")):
            with self.assertRaises(SystemExit) as caught:
                cli.main(["night"], stdin=io.StringIO(""), stdout=io.StringIO())
        self.assertIn("the reader night job", str(caught.exception.code))
        self.assertIn(elsewhere, str(caught.exception.code))

    def test_night_command_runs_in_the_live_checkout(self):
        self.postings(45)
        path = self.conn.execute("PRAGMA database_list").fetchone()["file"]
        fake = Fake(good)
        out = io.StringIO()
        with mock.patch.object(live_checkout, "LIVE_ROOT", live_checkout.REPO_ROOT), \
                mock.patch.object(cli, "load_reader_config", return_value=self.cfg), \
                mock.patch.object(cli, "connect", side_effect=lambda: connect(path)), \
                mock.patch.object(cli, "claude_runner", return_value=fake) as built, \
                mock.patch.object(cli.subprocess, "Popen") as caffeinate, \
                mock.patch.object(night.subprocess, "run", side_effect=AssertionError("ran claude")):
            code = cli.main(["night", "--max-batches", "1", "--max-postings", "15"], stdin=io.StringIO(""),
                            stdout=out)
        self.assertEqual(code, 0)
        # The Mac stays awake while this process lives: caffeinate watches its pid (it is a fake here).
        caffeinate.assert_called_once_with(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
        self.assertEqual(built.call_args.args[0].max_batches, 1)
        self.assertEqual(fake.sizes(), [15])
        self.assertIn("cap_reached", out.getvalue())
        self.assertIn("15 stored", out.getvalue())


class TemplateTests(unittest.TestCase):
    def test_plist_template_is_complete(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("<string>com.devanshu.outreach-reader-night</string>", text)
        self.assertEqual(set(re.findall(r"\{\{[^}]*\}\}", text)), {"{{LIVE_CHECKOUT}}", "{{LOG_PATH}}"})
        self.assertIn("launchctl bootstrap gui/$(id -u)", text)
        self.assertIn("launchctl bootout", text)
        folder = Path(tempfile.mkdtemp(prefix="outreach-plist-test-"))
        self.addCleanup(shutil.rmtree, folder, True)
        filled = folder / "com.devanshu.outreach-reader-night.plist"
        filled.write_text(text.replace("{{LIVE_CHECKOUT}}", "/Users/someone/Desktop/Sept 23 Companies")
                          .replace("{{LOG_PATH}}", "/Users/someone/Library/Logs/outreach/reader-night.log"),
                          encoding="utf-8")
        plist = plistlib.loads(filled.read_bytes())
        self.assertEqual(plist["Label"], "com.devanshu.outreach-reader-night")
        self.assertEqual(plist["StartCalendarInterval"], {"Hour": 2, "Minute": 30})
        self.assertNotIn("StartInterval", plist)
        # The deploy tool's one convention (#17, outreach/deploy/jobs.py): the venv python run as a module from the
        # live checkout, both streams to the log, no shell, no PATH and no other placeholder.
        live, log = "/Users/someone/Desktop/Sept 23 Companies", "/Users/someone/Library/Logs/outreach/reader-night.log"
        self.assertEqual(plist["WorkingDirectory"], live)
        self.assertEqual(plist["ProgramArguments"], [f"{live}/.venv/bin/python", "-m", "outreach.reader", "night"])
        self.assertEqual((plist["StandardOutPath"], plist["StandardErrorPath"]), (log, log))
        self.assertNotIn("EnvironmentVariables", plist)
        for word in ("zsh", "caffeinate", "PATH=", "cd "):
            self.assertNotIn(word, "".join(re.split(r"<!--.*?-->", text, flags=re.S)))
        plutil = shutil.which("plutil")
        if plutil is None:
            self.skipTest("plutil is not installed")
        lint = subprocess.run([plutil, "-lint", str(filled)], capture_output=True, text=True)
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)


if __name__ == "__main__":
    unittest.main()
