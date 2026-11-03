"""`python -m outreach.crawl <command>`: the board crawler from launchd and by hand (#21, Task 13).

- `tick`: one 30-minute tick under the crawl lock: the due boards, then the daily slug probe (the first tick of each
  New York day, up to 50 companies with no board), then the due watched pages. The one log line carries all three,
  as does the tick's `read_boards` job summary (`probe`, `probe_error`, `watch`, `watch_error`). Another tick holding
  the lock is not an error: it prints so and exits 0.
- `read --company ID [ID ...]`: read these companies' boards now (floor kept, an erroring board's backoff kept). A
  board whose ATS's reader is switched off in config.toml (`ats_off`, #46) is not read: one line on stderr names it
  and the reason, the other boards are read, and the exit code is 1.
- `probe ATS TOKEN`: one list request through the polite client, no store access. Prints the result, the job count,
  the ETag and the first five titles: how a token or an adapter's shape is checked on first use. A switched-off ATS
  is refused with its reason and nothing is sent.
- `probe-slugs [--limit N]`: for up to N companies with no board (default 50, the daily tick step's size), try the
  slugs their names suggest on Greenhouse, Lever and Ashby and add each `ok` hit as a `slug_probe` board (at most 12
  requests a company). Takes the crawl lock, so it never overlaps a tick. The tick runs the same pass once a day, so
  this is for a first run or a larger batch by hand.
- `recheck-boards`: raise the doubtful-board item (Needs you) for every active board found by a name match or a guessed
  slug that lists over 300 open postings and has none yet. Store only, no requests; run it once after a deploy that
  widens the check.
- `profile [--runs N] [--json]`: where the tick time goes (#152). Sums the `by_ats` summaries of the newest N finished
  `read_boards` runs (default 20; a run without `by_ats` is skipped) and prints, per ATS, boards, seconds, seconds per
  board, share of the ticks' time, list and body requests, and new, closed and reopened postings. Store only, no requests.
- `churn [--days N] [--limit N] [--json]`: per board, the postings closed, new and listed again over the last N days
  (default 1; the top 20 boards by closed, #152), with the open count and the open US engineering postings. A posting
  counts as listed again when its board closed one with the same title and location in the 7 days before. Store only,
  no requests.

Every command calls `live_checkout.require_live` before it opens anything, so they all run only from the live
checkout. The commands that read boards (`tick`, `read`, `probe`, `probe-slugs`) send real requests, and `tick` and
`read` write the live store; `profile`, `churn` and `recheck-boards` read the store only. Exit code: 0 when the run
was `ok`, 1 when it was partial or failed, or the command failed (the message on stderr).
"""
from __future__ import annotations

import argparse
import contextlib
import sqlite3
import sys
from collections.abc import Sequence

import live_checkout
from outreach.crawl.probe import PROBE_LIMIT
from outreach.net import FetchError, HttpClient, UrllibClient
from outreach.sending.notify import mac_notifier
from outreach.store import StoreError, connect, db_path, describe_error

__all__ = ["main"]

_EXPECTED = (ValueError, OSError, FetchError, StoreError, sqlite3.Error)
_TITLES_SHOWN = 5
PROFILE_RUNS = 20
CHURN_DAYS = 1
CHURN_LIMIT = 20


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m outreach.crawl", description="The board crawler (#21).")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("tick", help="read the due boards, then the daily slug probe and the due watched careers pages")
    read = sub.add_parser("read", help="read these companies' boards now")
    read.add_argument("--company", type=int, nargs="+", required=True, metavar="ID")
    probe = sub.add_parser("probe", help="list one board without touching the store")
    probe.add_argument("ats")
    probe.add_argument("token")
    slugs = sub.add_parser("probe-slugs", help="guess board slugs for companies with no board")
    slugs.add_argument("--limit", type=_positive, default=PROBE_LIMIT, metavar="N")
    sub.add_parser("recheck-boards", help="raise the doubtful item for big boards matched by name only (store only)")
    prof = sub.add_parser("profile", help="where the tick time goes, per ATS, from the newest read_boards runs")
    prof.add_argument("--runs", type=_positive, default=PROFILE_RUNS, metavar="N")
    prof.add_argument("--json", action="store_true", help="print the numbers as JSON")
    churn = sub.add_parser("churn", help="postings closed, new and listed again per board over the last N days")
    churn.add_argument("--days", type=_positive, default=CHURN_DAYS, metavar="N")
    churn.add_argument("--limit", type=_positive, default=CHURN_LIMIT, metavar="N")
    churn.add_argument("--json", action="store_true", help="print the numbers as JSON")
    return p


def _positive(text: str) -> int:
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError(f"{text} is not a positive number")
    return n


def _say(text: str) -> None:
    print(text, file=sys.stderr)


def _polite(client: HttpClient | None, cfg):
    from outreach.crawl.polite import PoliteClient
    inner = client or UrllibClient("declared", timeout_s=cfg.timeout_s, max_bytes=cfg.max_bytes,
                                   max_redirects=cfg.max_redirects)
    return PoliteClient(inner, cfg)


def _line(report) -> str:
    """One line for the log: the board counts, then the watched pages."""
    text = (f"read_boards run {report.run_id}: read {report.read} (ok {report.ok}, empty {report.empty}, "
            f"error {report.error}, unchanged {report.unchanged}), deferred {report.deferred}, "
            f"failed {report.failed}, new {report.new}, closed {report.closed}, {report.seconds:.1f} s")
    if report.switched_off:
        text += f"; switched off {report.switched_off} (their reader is off in config.toml)"
    if report.rebuilt:
        text += "; rebuilt the ruleset first"
    if report.tripped:
        text += f"; breaker open: {', '.join(report.tripped)}"
    if report.watch is not None:
        w = report.watch
        text += (f"; pages: baseline {w.baseline}, unchanged {w.unchanged}, changed {w.changed}, "
                 f"unstable {w.unstable}, error {w.error}, deferred {w.deferred}")
    if report.probe is not None:
        p = report.probe
        text += (f"; slug probe: probed {p.probed}, added {p.added}, conflicts {p.conflicts}, "
                 f"name mismatch {p.name_mismatch}, errors {p.errors}")
    if report.probe_error:
        text += f"; slug probe failed: {report.probe_error}"
    if report.watch_error:
        text += f"; watched pages failed: {report.watch_error}"
    if report.feed is not None:
        f = report.feed
        text += f"; feed: {f['new']} new of {f['seen']}" if f["changed"] else "; feed: unchanged"
    if report.feed_error:
        text += f"; feed failed: {report.feed_error}"
    if report.alarm_error:
        text += f"; alarm check failed: {report.alarm_error}"
    if report.alert_error:
        text += f"; job alert failed: {report.alert_error}"
    elif report.alerted:
        text += f"; job alert: {report.alerted} new"
    return text


def _status(conn: sqlite3.Connection, run_id: int | None) -> str:
    row = conn.execute("SELECT status, error FROM job_run WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return "failed"
    if row["error"]:
        _say(f"read_boards {row['status']}: {row['error']}")
    return row["status"]


def _locked_run(what: str, client: HttpClient | None, run, *, lock: bool) -> int:
    """Open the store and call `run(conn, polite, cfg)`, which returns the report; under the crawl lock when `lock`
    (`read_company_boards` takes it itself)."""
    from outreach.crawl.config import load_crawl_config
    from outreach.crawl.tick import AlreadyRunning, tick_lock

    cfg = load_crawl_config()
    path = db_path()
    conn = connect(path)
    try:
        try:
            with tick_lock(path) if lock else contextlib.nullcontext():
                report = run(conn, _polite(client, cfg), cfg)
        except AlreadyRunning as e:
            print(f"{what}: {e}; nothing to do")
            return 0 if what == "tick" else 1
        print(_line(report))
        ok = _status(conn, report.run_id) == "ok" and not report.watch_error
        return 0 if ok else 1
    finally:
        conn.close()


def _tick(client: HttpClient | None) -> int:
    from outreach.crawl.tick import run_tick
    return _locked_run("tick", client, lambda conn, polite, cfg: run_tick(conn, polite, cfg=cfg, notifier=mac_notifier),
                       lock=True)


def _read(company_ids: list[int], client: HttpClient | None) -> int:
    from outreach.crawl.cadence import switched_off_boards
    from outreach.crawl.tick import read_company_boards

    refused: list[int] = []

    def run(conn, polite, cfg):
        for board in switched_off_boards(conn, cfg.ats_off, only_company_ids=company_ids):
            _say(f"company {board.company_id}: not reading its {board.ats} board {board.token}: the {board.ats} "
                 f"reader is switched off ({cfg.ats_off[board.ats]})")
            refused.append(board.board_id)
        return read_company_boards(conn, polite, company_ids, cfg=cfg)

    code = _locked_run("read", client, run, lock=False)
    return 1 if refused else code


def _probe(ats: str, token: str, client: HttpClient | None) -> int:
    from outreach.crawl.ats import adapter_for, supported_ats
    from outreach.crawl.base import Validators
    from outreach.crawl.config import load_crawl_config

    cfg = load_crawl_config()
    if ats in cfg.ats_off:
        _say(f"the {ats} reader is switched off ({cfg.ats_off[ats]}); nothing was sent. "
             "Delete its line under [ats_off] in outreach/crawl/config.toml to turn it back on.")
        return 1
    adapter = adapter_for(ats, _polite(client, cfg))
    if adapter is None:
        _say(f"no adapter for {ats!r}; the crawler reads: {', '.join(sorted(supported_ats()))}")
        return 1
    result = adapter.list(token, Validators())
    name = f", board name {result.board_name!r}" if result.board_name else ""
    print(f"{ats} {token}: {result.result}, {len(result.jobs)} jobs, http {result.http_status or '-'}, "
          f"etag {result.validators.etag or '-'}{name}")
    if result.error:
        print(f"  error: {result.error}")
    for job in result.jobs[:_TITLES_SHOWN]:
        print(f"  {job.title} ({job.location or 'no location'})")
    return 1 if result.result == "error" else 0


def _recheck_boards() -> int:
    from outreach.crawl.ingest import recheck_big_boards
    from outreach.store import utcnow

    conn = connect(db_path())
    try:
        raised = recheck_big_boards(conn, utcnow())
    finally:
        conn.close()
    print(f"recheck-boards: raised {raised} doubtful board item{'' if raised == 1 else 's'}")
    return 0


def _profile(runs: int, as_json: bool) -> int:
    import dataclasses
    import json

    from outreach.crawl.profile import profile

    conn = connect(db_path())
    try:
        result = profile(conn, runs)
    finally:
        conn.close()
    if as_json:
        print(json.dumps(dataclasses.asdict(result), indent=2))
        return 0
    if not result.runs:
        print("profile: no read_boards runs with a per ATS summary yet")
        return 0
    print(f"profile: {result.runs} run{'' if result.runs == 1 else 's'}, {result.seconds:.1f} s of tick time")
    print(f"{'ats':<18}{'boards':>7}{'seconds':>10}{'s/board':>9}{'share':>8}{'lists':>7}{'bodies':>8}"
          f"{'new':>6}{'closed':>8}{'reopened':>9}")
    for r in result.rows:
        print(f"{r.ats:<18}{r.boards:>7}{r.seconds:>10.1f}{r.seconds_per_board:>9.2f}{r.share * 100:>7.1f}%"
              f"{r.list_requests:>7}{r.body_requests:>8}{r.new:>6}{r.closed:>8}{r.reopened:>9}")
    print("seconds = list calls + bodies (fetch and store) + ingest; lists and bodies are request counts")
    return 0


def _churn(days: int, limit: int, as_json: bool) -> int:
    import dataclasses
    import json

    from outreach.crawl.churn import churn
    from outreach.store import utcnow

    conn = connect(db_path())
    try:
        rows = churn(conn, utcnow(), days, limit)
    finally:
        conn.close()
    if as_json:
        print(json.dumps([dataclasses.asdict(r) for r in rows], indent=2))
        return 0
    if not rows:
        print(f"churn: no postings opened or closed in the last {days} day{'' if days == 1 else 's'}")
        return 0
    print(f"churn: the last {days} day{'' if days == 1 else 's'}, top {len(rows)} board{'' if len(rows) == 1 else 's'} "
          "by closed")
    print(f"{'board':>7}  {'ats':<16}{'token':<28}{'open':>8}{'closed':>8}{'new':>8}{'relisted':>10}{'us eng open':>13}")
    for r in rows:
        print(f"{r.board_id:>7}  {r.ats:<16}{r.token[:26]:<28}{r.open:>8}{r.closed:>8}{r.new:>8}{r.relisted:>10}"
              f"{r.us_engineering_open:>13}")
    print("relisted = new postings whose board closed one with the same title and location in the 7 days before")
    return 0


def _probe_slugs(limit: int, client: HttpClient | None) -> int:
    from outreach.crawl.config import load_crawl_config
    from outreach.crawl.probe import probe_slugs
    from outreach.crawl.tick import AlreadyRunning, tick_lock

    cfg = load_crawl_config()
    path = db_path()
    conn = connect(path)
    try:
        try:
            with tick_lock(path):
                ids = [r[0] for r in conn.execute("SELECT company_id FROM company")]
                report = probe_slugs(conn, ids, _polite(client, cfg), limit=limit, cfg=cfg)
        except AlreadyRunning as e:
            print(f"probe-slugs: {e}; nothing to do")
            return 1
    finally:
        conn.close()
    print(f"probe-slugs: considered {report.considered}, probed {report.probed}, added {report.added}, "
          f"conflicts {report.conflicts}, name mismatch {report.name_mismatch}, errors {report.errors}")
    return 0


def main(argv: Sequence[str] | None = None, *, root: str | None = None, live_root: str | None = None,
         client: HttpClient | None = None) -> int:
    """Run one command and return the exit code. `root` and `live_root` default to this checkout and the live one;
    `client` stands in for the real HTTP client in tests. Outside the live checkout every command raises SystemExit
    before it opens the store or builds a client."""
    args = _parser().parse_args(argv)
    root = root or live_checkout.REPO_ROOT
    live_root = live_root or live_checkout.LIVE_ROOT
    what = {"tick": "the crawl tick", "read": "reading boards", "probe": "probing a board",
            "probe-slugs": "probing company slugs", "recheck-boards": "rechecking boards",
            "profile": "profiling the crawl", "churn": "reading the crawl churn"}[args.command]
    live_checkout.require_live(what, root, live_root)
    try:
        if args.command == "tick":
            return _tick(client)
        if args.command == "read":
            return _read(args.company, client)
        if args.command == "probe-slugs":
            return _probe_slugs(args.limit, client)
        if args.command == "recheck-boards":
            return _recheck_boards()
        if args.command == "profile":
            return _profile(args.runs, args.json)
        if args.command == "churn":
            return _churn(args.days, args.limit, args.json)
        return _probe(args.ats, args.token, client)
    except _EXPECTED as err:
        _say(f"error: {describe_error(err)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
