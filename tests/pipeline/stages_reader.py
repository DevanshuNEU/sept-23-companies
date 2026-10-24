"""The `read_postings` stage (#32 owns it): decide what the crawler left, queue it, and run one night batch.

The crawler stores bodies and decides postings but never queues them (ruling 0), so this stage does what the night
job's caller does: it decides every open posting with the reader's combiner (`decide_and_store(conn, pid,
combiner=reader_combiner(conn, cfg))`, after a `rebuild` when the store's current ruleset is not this code's), runs
`enqueue_missing`, then `run_night(conn, runner, cfg)`.

The crawl fixture's one engineering posting has a body under the 500-character floor, so it is never read. The stage
seeds, once, the posting the reader is about: `HIDE_TITLE` at `HIDE_COMPANY` in Austin, whose body
(`posting_hide.txt`) says "We do not sponsor visas." and nothing else that hides it. Its rules decision is a
provisional hide.

The runner is a fake unless `register` is given one, and the real `claude_runner` is never built here. The fake
answers each posting from `stage.script`, keyed by posting title: (value, quote) per field over a reading that says
unknown everywhere. By default it agrees with the rules on the hide posting (engineering yes, sponsorship
wont_sponsor with the posting's own words). `stage.calls` counts the calls.

The stage runs with the shipped reader config except for two things: `max_batches` is 1, so each run of the stage is
one night batch (a refused reading is read again on the next run, not three times in one), and `promoted_version` is
"" (no reader version decides anything: the reader-off state, which must be normal). A test that wants a promoted
reader calls `stage.promote()`, which promotes the version the files make now.

`register` leaves the stage on the harness as `harness.reader` (a `ReaderStage`).
"""
from __future__ import annotations

import dataclasses
import json

from outreach.reader.combine import reader_combiner
from outreach.reader.config import load_reader_config
from outreach.reader.night import RunResult, Runner, run_night
from outreach.reader.queue import enqueue_missing
from outreach.reader.types import FIELDS
from outreach.reader.version import reader_version
from outreach.rules import ruleset_version
from outreach.rules.rebuild import decide_and_store, rebuild
from tests.storetest import seed_company, seed_posting

__all__ = ["register", "ReaderStage", "HIDE_TITLE", "HIDE_COMPANY", "HIDE_LOCATION", "REFUSAL", "AGREE"]

HIDE_TITLE = "Backend Software Engineer"
HIDE_COMPANY = "Ledgerline Systems"
HIDE_LOCATION = "Austin, TX"
REFUSAL = "We do not sponsor visas."
# A reading that agrees with the rules on the hide posting.
AGREE = {"engineering": ("yes", HIDE_TITLE), "sponsorship": ("wont_sponsor", REFUSAL)}


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


def envelope(text: str) -> str:
    """What `claude -p --output-format json` prints: one result object."""
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text})


class ReaderStage:
    def __init__(self, harness, runner: Runner | None = None):
        self.harness = harness
        self.conn = harness.conn
        self.cfg = dataclasses.replace(load_reader_config(), max_batches=1, promoted_version="")
        self.script: dict[str, dict[str, tuple]] = {HIDE_TITLE: dict(AGREE)}
        self.runner = runner if runner is not None else self.fake
        self.calls = 0
        self.posting_id: int | None = None
        self.decided: list[int] = []
        self.summary = None

    def promote(self) -> str:
        """Promote the version the files make now (in this stage's config only) and return it."""
        version = reader_version(self.cfg)
        self.cfg = dataclasses.replace(self.cfg, promoted_version=version)
        return version

    def fake(self, system_prompt: str, user_message: str, model: str) -> RunResult:
        self.calls += 1
        readings = []
        for p in json.loads(user_message)["postings"]:
            fields = blank_fields()
            for name, (value, quote) in self.script.get(p["title"], {}).items():
                fields[name] = {"value": value, "quote": quote}
            readings.append({"posting_id": p["posting_id"], "body_sha256": p["body_sha256"], "fields": fields})
        return RunResult(0, envelope(json.dumps({"readings": readings})), "")

    def seed(self) -> int:
        """The hide posting, made once."""
        if self.posting_id is None:
            company = seed_company(self.conn, HIDE_COMPANY)
            body = self.harness.fixture("posting_hide.txt").read_text(encoding="utf-8")
            self.posting_id = seed_posting(self.conn, company, HIDE_TITLE, body, HIDE_LOCATION)
        return self.posting_id

    def decide(self) -> None:
        """Every open posting decided with the reader's combiner; a rebuild first when the ruleset is not current."""
        combiner = reader_combiner(self.conn, self.cfg)
        current = self.conn.execute("SELECT ruleset_version FROM ruleset WHERE is_current = 1").fetchone()
        if current is None or current[0] != ruleset_version():
            rebuild(self.conn, combiner=combiner)
        self.decided = [r[0] for r in self.conn.execute(
            "SELECT posting_id FROM posting WHERE closed_at IS NULL ORDER BY posting_id")]
        for posting_id in self.decided:
            decide_and_store(self.conn, posting_id, combiner=combiner)

    def prepare(self) -> int:
        """Seed, decide and queue: everything before the night batch. Returns how many rows were queued."""
        self.seed()
        self.decide()
        return enqueue_missing(self.conn, cfg=self.cfg)

    def run(self):
        self.prepare()
        self.summary = run_night(self.conn, self.runner, self.cfg)
        return self.summary


def register(harness, runner: Runner | None = None) -> None:
    stage = ReaderStage(harness, runner)
    harness.reader = stage
    harness.stages.append(("read_postings", stage.run))
