"""`python -m outreach.crawl <command>`: the board crawler from launchd and by hand (#21, Task 13).

- `tick`: one 30-minute tick (the due boards, the daily slug probe, then the due watched pages) under the crawl lock. Another tick holding
  the lock is not an error: it prints so and exits 0.
- `read --company ID [ID ...]`: read these companies' boards now (floor kept, an erroring board's backoff kept).
- `probe ATS TOKEN`: one list request through the polite client, no store access. Prints the result, the job count,
  the ETag and the first five titles: how a token or an adapter's shape is checked on first use.
- `probe-slugs [--limit N]`: for up to N companies with no board (default 50, the daily tick step's size), try the slugs their names suggest on
  Greenhouse, Lever and Ashby and add each `ok` hit as a `slug_probe` board (at most 12 requests a company). Takes
  the crawl lock, so it never overlaps a tick.

Every command calls `live_checkout.require_live` before it opens anything: they send real requests (and `tick` and
`read` write the live store), so they run only from the live checkout. Exit code: 0 when the run was `ok`, 1 when it
was partial or failed, or the command failed (the message on stderr).
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
from outreach.store import StoreError, connect, db_path, describe_error

__all__ = ["main"]

_EXPECTED = (ValueError, OSError, FetchError, StoreError, sqlite3.Error)
_TITLES_SHOWN = 5


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m outreach.crawl", description="The board crawler (#21).")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("tick", help="read the due boards, then the due watched careers pages")
    read = sub.add_parser("read", help="read these companies' boards now")
    read.add_argument("--company", type=int, nargs="+", required=True, metavar="ID")
    probe = sub.add_parser("probe", help="list one board without touching the store")
    probe.add_argument("ats")
    probe.add_argument("token")
    slugs = sub.add_parser("probe-slugs", help="guess board slugs for companies with no board")
    slugs.add_argument("--limit", type=_positive, default=PROBE_LIMIT, metavar="N")
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
    return _locked_run("tick", client, lambda conn, polite, cfg: run_tick(conn, polite, cfg=cfg), lock=True)


def _read(company_ids: list[int], client: HttpClient | None) -> int:
    from outreach.crawl.tick import read_company_boards
    return _locked_run("read", client,
                       lambda conn, polite, cfg: read_company_boards(conn, polite, company_ids, cfg=cfg), lock=False)


def _probe(ats: str, token: str, client: HttpClient | None) -> int:
    from outreach.crawl.ats import adapter_for, supported_ats
    from outreach.crawl.base import Validators
    from outreach.crawl.config import load_crawl_config

    cfg = load_crawl_config()
    adapter = adapter_for(ats, _polite(client, cfg))
    if adapter is None:
        _say(f"no adapter for {ats!r}; the crawler reads: {', '.join(sorted(supported_ats()))}")
        return 1
    result = adapter.list(token, Validators())
    print(f"{ats} {token}: {result.result}, {len(result.jobs)} jobs, http {result.http_status or '-'}, "
          f"etag {result.validators.etag or '-'}" + (f", board name {result.board_name!r}" if result.board_name else ""))
    if result.error:
        print(f"  error: {result.error}")
    for job in result.jobs[:_TITLES_SHOWN]:
        print(f"  {job.title} ({job.location or 'no location'})")
    return 1 if result.result == "error" else 0


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
            "probe-slugs": "probing company slugs"}[args.command]
    live_checkout.require_live(what, root, live_root)
    try:
        if args.command == "tick":
            return _tick(client)
        if args.command == "read":
            return _read(args.company, client)
        if args.command == "probe-slugs":
            return _probe_slugs(args.limit, client)
        return _probe(args.ats, args.token, client)
    except _EXPECTED as err:
        _say(f"error: {describe_error(err)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
