"""`python -m outreach.crawl <command>`: the board crawler from launchd and by hand (#21, Task 13).

- `tick`: one 30-minute tick (the due boards, then the due watched pages) under the crawl lock. Another tick holding
  the lock is not an error: it prints so and exits 0.
- `read --company ID [ID ...]`: read these companies' boards now (floor kept, an erroring board's backoff kept).
- `probe ATS TOKEN`: one list request through the polite client, no store access. Prints the result, the job count,
  the ETag and the first five titles: how a token or an adapter's shape is checked on first use.

Every command calls `live_checkout.require_live` before it opens anything: they send real requests (and `tick` and
`read` write the live store), so they run only from the live checkout. Exit code: 0 when the run was `ok`, 1 when it
was partial or failed, or the command failed (the message on stderr).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence

import live_checkout
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
    return p


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


def _locked_run(what: str, client: HttpClient | None, run) -> int:
    """Open the store, take the crawl lock and call `run(conn, polite, cfg)`, which returns the report."""
    from outreach.crawl.config import load_crawl_config
    from outreach.crawl.tick import AlreadyRunning, tick_lock

    cfg = load_crawl_config()
    path = db_path()
    conn = connect(path)
    try:
        try:
            with tick_lock(path):
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
    return _locked_run("tick", client, lambda conn, polite, cfg: run_tick(conn, polite, cfg=cfg))


def _read(company_ids: list[int], client: HttpClient | None) -> int:
    from outreach.crawl.tick import read_company_boards
    return _locked_run("read", client,
                       lambda conn, polite, cfg: read_company_boards(conn, polite, company_ids, cfg=cfg))


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


def main(argv: Sequence[str] | None = None, *, root: str | None = None, live_root: str | None = None,
         client: HttpClient | None = None) -> int:
    """Run one command and return the exit code. `root` and `live_root` default to this checkout and the live one;
    `client` stands in for the real HTTP client in tests. Outside the live checkout every command raises SystemExit
    before it opens the store or builds a client."""
    args = _parser().parse_args(argv)
    root = root or live_checkout.REPO_ROOT
    live_root = live_root or live_checkout.LIVE_ROOT
    what = {"tick": "the crawl tick", "read": "reading boards", "probe": "probing a board"}[args.command]
    live_checkout.require_live(what, root, live_root)
    try:
        if args.command == "tick":
            return _tick(client)
        if args.command == "read":
            return _read(args.company, client)
        return _probe(args.ats, args.token, client)
    except _EXPECTED as err:
        _say(f"error: {describe_error(err)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
