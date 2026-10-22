"""`python -m outreach.sources <command>`: the sources stream by hand and from launchd (#31 Task 16).

Wiring only: every command builds a source, calls one function from `outreach.sources.jobs` (or `byhand`) and prints
the result as JSON. All of them open the store through `outreach.store.connect()`, so the live-store guard applies.
The exit code is 0 when the job is `ok` and 1 when it is `partial` or `failed` (or the command itself failed, with
the message on stderr).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import re
import sqlite3
import sys
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from outreach.net import FetchError, HttpClient, SpacedClient, UrllibClient
from outreach.sources.base import describe_error
from outreach.sources.byhand import add_by_hand
from outreach.sources.careers import CareersChecker
from outreach.sources.formd import FormDSource, sec_headers
from outreach.sources.hn import HnSource
from outreach.sources.jobs import run_discover, run_find_boards, run_resolve
from outreach.sources.lca import LcaSource
from outreach.sources.lca_rules import CONFIG_PATH as LCA_TOML
from outreach.sources.paths import sources_dir
from outreach.sources.stapply import StapplyDirectory
from outreach.sources.tabular import TabularError
from outreach.store import StoreError, connect, utcnow

__all__ = ["main", "lca_download_urls", "run_due"]

SEC_INTERVAL_S = 1.0  # seconds between requests to data.sec.gov (SEC allows 10 a second)
LCA_MAX_BYTES = 1 << 30  # the quarterly xlsx files are 80 to 250 MB
FORMD_PAGE_URL = "https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets"
_FORMD_LINK = re.compile(r'href="(/files/[^"]*/form-d-data-sets/(\d{4}q[1-4])_d\.zip)"')
_FORMD_NAME = re.compile(r"(\d{4}q[1-4])_d")
_FORMD_MARKER = "last_read.txt"
_STAPPLY_MARKER = "refreshed_at.txt"  # when the last refresh of the stapply lists finished
STAPPLY_REFRESH_DAYS = 7
_TIME = "%Y-%m-%dT%H:%M:%SZ"
RUN_DUE_MAX_CAREERS = 100

ClientFactory = Callable[..., HttpClient]
# What a command may fail with and still say so in one line; anything else is a bug and keeps its traceback.
_EXPECTED = (ValueError, OSError, FetchError, StoreError, TabularError, sqlite3.Error)


def default_client(profile: str = "declared", **kw) -> HttpClient:
    """The real client, spaced per host (the SEC and careers limits live in `SpacedClient` and the callers)."""
    return SpacedClient(UrllibClient(profile, **kw))


def lca_download_urls(path: Path | None = None) -> list[str]:
    """The DOL file URLs in `lca.toml`'s `[downloads]` table. KeyError when the table is missing."""
    data = tomllib.loads(Path(path or LCA_TOML).read_bytes().decode("utf-8"))
    urls = data["downloads"]["urls"]
    if not urls:
        raise ValueError("lca.toml lists no download URL")
    return list(urls)


# --- small helpers ----------------------------------------------------------------------------------------------

def _emit(payload: dict) -> None:
    print(json.dumps(payload, indent=2))


def _say(text: str) -> None:
    print(text, file=sys.stderr)


def _job(conn: sqlite3.Connection, run_id: int) -> tuple[str, str]:
    """The finished run's (status, error)."""
    row = conn.execute("SELECT status, error FROM job_run WHERE run_id = ?", (run_id,)).fetchone()
    return row["status"], row["error"]


def _exit(status: str) -> int:
    return 0 if status == "ok" else 1


def _stapply_directory(root: Path) -> StapplyDirectory | None:
    """The stapply lists under `root`, or None (said on stderr; `find_boards` then runs `partial`)."""
    try:
        return StapplyDirectory.load(root / "stapply")
    except (OSError, ValueError) as err:
        _say(f"no stapply lists to read ({describe_error(err)}); run: python -m outreach.sources fetch stapply")
        return None


def _mark_formd_read(root: Path, label: str) -> None:
    """Remember the newest Form D data set that was read in full; it never moves back."""
    path = root / "formd" / _FORMD_MARKER
    if label > _formd_last_read(root):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(label + "\n", encoding="utf-8")


def _formd_last_read(root: Path) -> str:
    try:
        return (root / "formd" / _FORMD_MARKER).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


# --- the job runners (one `job_run` each); each returns (status, payload) ---------------------------------------

def _discover(conn: sqlite3.Connection, source, since: str | None = None) -> tuple[str, dict]:
    run_id, reports = run_discover(conn, [source], since=since)
    status, error = _job(conn, run_id)
    if status != "ok":
        _say(f"discover {source.name} {status}: {error}")
    return status, {"job": "discover", "run_id": run_id, "status": status,
                    "reports": {name: asdict(r) for name, r in reports.items()}}


def _resolve(conn: sqlite3.Connection) -> tuple[str, dict]:
    run_id, report = run_resolve(conn)
    status, error = _job(conn, run_id)
    if status != "ok":
        _say(f"resolve {status}: {error}")
    return status, {"job": "resolve", "run_id": run_id, "status": status, "report": asdict(report)}


def _find_boards(conn: sqlite3.Connection, factory: ClientFactory, max_careers: int) -> tuple[str, dict]:
    directory = _stapply_directory(sources_dir())
    run_id, report = run_find_boards(conn, directory=directory, careers=CareersChecker(factory("declared")),
                                     max_careers=max_careers)
    status, error = _job(conn, run_id)
    if status != "ok":
        _say(f"find_boards {status}: {error}")
    return status, {"job": "find_boards", "run_id": run_id, "status": status, "report": asdict(report)}


# --- fetch ------------------------------------------------------------------------------------------------------

def _fetch_lca(dest: Path, factory: ClientFactory) -> tuple[int, dict]:
    folder = dest / "lca"
    folder.mkdir(parents=True, exist_ok=True)
    client = factory("browser", timeout_s=120.0, max_bytes=LCA_MAX_BYTES)
    downloaded, kept, failed = [], [], []
    for url in lca_download_urls():
        name = url.rsplit("/", 1)[-1]
        if (folder / name).is_file():
            kept.append(name)
            continue
        try:
            client.download(url, folder / name)
        except (FetchError, OSError) as err:
            failed.append(f"{name}: {describe_error(err)}")
        else:
            downloaded.append(name)
    for line in failed:
        _say(f"fetch lca: {line}")
    return (1 if failed else 0), {"folder": str(folder), "downloaded": downloaded, "kept": kept, "failed": failed}


def _fetch_stapply(dest: Path, factory: ClientFactory) -> tuple[int, dict]:
    folder = dest / "stapply"
    updated = StapplyDirectory.refresh(factory("declared"), folder)
    (folder / _STAPPLY_MARKER).write_text(utcnow() + "\n", encoding="utf-8")  # only a refresh that finished
    return 0, {"folder": str(folder), "updated": list(updated), "skipped_entries": updated.skipped}


def _stapply_due(root: Path, now: str) -> str | None:
    """None when the lists are due (the last finished refresh is 7 or more days old, or there is none), else why
    not."""
    try:
        last = (root / "stapply" / _STAPPLY_MARKER).read_text(encoding="utf-8").strip()
        age = datetime.strptime(now, _TIME) - datetime.strptime(last, _TIME)
    except (FileNotFoundError, ValueError):
        return None
    if age >= timedelta(days=STAPPLY_REFRESH_DAYS):
        return None
    return f"the lists were refreshed {last}"


def _formd_latest(client: HttpClient) -> tuple[str, str]:
    """The newest quarterly data set SEC lists, as (label such as `2026q3`, URL). ValueError when the page lists none."""
    resp = client.get(FORMD_PAGE_URL, sec_headers(FORMD_PAGE_URL), follow_redirects=True)
    if resp.status != 200:
        raise ValueError(f"{FORMD_PAGE_URL}: HTTP {resp.status}")
    links = {label: path for path, label in _FORMD_LINK.findall(resp.text())}
    if not links:
        raise ValueError(f"no Form D data set link (form-d-data-sets/YYYYqN_d.zip) on {FORMD_PAGE_URL}")
    label = max(links)
    return label, "https://www.sec.gov" + links[label]


def _fetch_formd(root: Path, factory: ClientFactory, *, only_if_newer: bool = False) -> dict:
    """Find the newest data set and make sure its zip is in `root/formd`. With `only_if_newer`, a set that is not
    newer than the last one read is not downloaded."""
    client = factory("declared")
    label, url = _formd_latest(client)
    last = _formd_last_read(root)
    newer = label > last
    path = root / "formd" / f"{label}_d.zip"
    downloaded = False
    if (newer or not only_if_newer) and not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        client.download(url, path, sec_headers(url))
        downloaded = True
    return {"latest": label, "url": url, "path": str(path), "downloaded": downloaded, "last_read": last,
            "newer_than_last_read": newer}


# --- run-due ----------------------------------------------------------------------------------------------------

def run_due(conn: sqlite3.Connection, factory: ClientFactory) -> tuple[int, dict]:
    """Everything that is due: HN's thread for this month (read again on each run, once it is out), Form D when SEC
    has a newer data set, the stapply lists weekly, then resolve and find-boards.

    Every step runs even when an earlier one failed. The exit code is 1 when any step is `partial` or `failed`. LCA is
    not here: it is a by-hand quarterly `fetch lca` and `discover lca`.
    """
    steps: dict[str, dict] = {}

    def step(name: str, work: Callable[[], dict]) -> None:
        try:
            steps[name] = work()
        except Exception as err:  # noqa: BLE001 - each step is isolated so the others still run; never silent
            steps[name] = {"status": "failed", "error": describe_error(err)}
            _say(f"run-due {name} failed: {describe_error(err)}")

    def hn() -> dict:
        month = utcnow()[:7]  # this month's thread, never "the latest": on the 1st or 2nd it may not be out yet
        source = HnSource(factory("declared"))
        if not source.thread_out(month):
            return {"status": "skipped", "reason": f"the {month} thread is not out yet"}
        status, payload = _discover(conn, source, since=month)  # a later run the same month reads it again: no new rows
        return {"status": status, "run_id": payload["run_id"]}

    def formd() -> dict:
        root = sources_dir()
        info = _fetch_formd(root, factory, only_if_newer=True)
        if not info["newer_than_last_read"]:
            return {"status": "skipped", "reason": f"no data set newer than {info['last_read']}"}
        status, payload = _discover(conn, FormDSource(Path(info["path"]), factory("declared"),
                                                      interval_s=SEC_INTERVAL_S))
        if status == "ok":  # a partial run is asked again tomorrow
            _mark_formd_read(root, info["latest"])
        return {"status": status, "run_id": payload["run_id"], "data_set": info["latest"]}

    def stapply() -> dict:
        root = sources_dir()
        reason = _stapply_due(root, utcnow())
        if reason:
            return {"status": "skipped", "reason": reason}
        _, payload = _fetch_stapply(root, factory)  # downloads only the files whose hash changed
        return {"status": "ok", "updated": payload["updated"], "skipped_entries": payload["skipped_entries"]}

    def resolve() -> dict:
        status, payload = _resolve(conn)
        return {"status": status, "run_id": payload["run_id"]}

    def find_boards() -> dict:
        status, payload = _find_boards(conn, factory, RUN_DUE_MAX_CAREERS)
        return {"status": status, "run_id": payload["run_id"]}

    for name, work in (("hn", hn), ("formd", formd), ("stapply", stapply), ("resolve", resolve),
                       ("find_boards", find_boards)):
        step(name, work)
    states = {s["status"] for s in steps.values()}
    status = "failed" if "failed" in states else "partial" if "partial" in states else "ok"
    return _exit(status), {"job": "run-due", "status": status, "steps": steps}


# --- the parser -------------------------------------------------------------------------------------------------

EPILOG = """\
Commands:
  fetch lca|stapply|formd [--dest DIR]       download into DIR (default: the sources folder, OUTREACH_SOURCES_DIR)
  discover lca [--files F...]                read LCA files (default: the ones `fetch lca` saved)
  discover formd --dataset PATH              read one Form D quarterly data set (the zip or its folder)
  discover hn [--since YYYY-MM]              read the Who is hiring threads (default: the latest)
  resolve                                    the identity pass: merge candidates and size checks
  find-boards [--max-careers N]              give companies their ATS boards (N careers pages at most)
  add --name N --reason R [--domain D] [--state ST] [--ats A:T]
  run-due                                    what launchd runs daily: this month's HN thread (once it is out), Form D
                                             (when SEC has a newer quarterly set), the stapply lists (weekly),
                                             resolve, then find-boards --max-careers 100

LCA is by hand, once a quarter when DOL posts a file: `fetch lca`, then `discover lca`, then `resolve` and
`find-boards`. run-due never runs it. `fetch stapply` refreshes the stapply lists by hand; run-due does it when the last refresh is 7 days old.

Exit code 0 when the job is ok, 1 when it is partial or failed (run-due: when any step is). Every command opens
the store through `outreach.store.connect()`: the live store opens only from the live checkout.
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m outreach.sources", description=__doc__.splitlines()[0],
                                     epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    fetch = sub.add_parser("fetch", help="download source files")
    fetch.add_argument("what", choices=["lca", "stapply", "formd"])
    fetch.add_argument("--dest", type=Path, help="a folder to use instead of the sources folder")

    discover = sub.add_parser("discover", help="run one source")
    kinds = discover.add_subparsers(dest="source", required=True, metavar="source")
    lca = kinds.add_parser("lca", help="DOL LCA files")
    lca.add_argument("--files", nargs="+", type=Path, help="xlsx or csv files (default: the fetched ones)")
    formd = kinds.add_parser("formd", help="an SEC Form D data set")
    formd.add_argument("--dataset", type=Path, required=True)
    hn = kinds.add_parser("hn", help="Hacker News Who is hiring")
    hn.add_argument("--since", help="YYYY-MM: every thread from that month on")

    sub.add_parser("resolve", help="the identity pass")
    boards = sub.add_parser("find-boards", help="find ATS boards")
    boards.add_argument("--max-careers", type=int, default=100, help="careers pages to fetch this run (default 100)")
    add = sub.add_parser("add", help="add a company by hand")
    add.add_argument("--name", required=True)
    add.add_argument("--reason", required=True)
    add.add_argument("--domain")
    add.add_argument("--state")
    add.add_argument("--ats", help="a board as ats:token, such as lever:acme")
    sub.add_parser("run-due", help="what launchd runs daily")
    return parser


def _lca_files(args: argparse.Namespace) -> list[Path]:
    if args.files:
        return list(args.files)
    folder = sources_dir() / "lca"
    files = sorted(p for p in folder.glob("*") if p.suffix.lower() in (".xlsx", ".csv")) if folder.is_dir() else []
    if not files:
        raise ValueError(f"no LCA files in {folder}; run: python -m outreach.sources fetch lca")
    return files


def _run(args: argparse.Namespace, factory: ClientFactory) -> int:
    if args.command == "fetch":
        dest = args.dest or sources_dir()
        code, payload = {"lca": _fetch_lca, "stapply": _fetch_stapply,
                         "formd": lambda d, f: (0, _fetch_formd(d, f))}[args.what](dest, factory)
        _emit(payload)
        return code
    with contextlib.closing(connect()) as conn:
        if args.command == "discover":
            if args.source == "lca":
                status, payload = _discover(conn, LcaSource(_lca_files(args)))
            elif args.source == "formd":
                source = FormDSource(args.dataset, factory("declared"), interval_s=SEC_INTERVAL_S)
                status, payload = _discover(conn, source)
                match = _FORMD_NAME.match(args.dataset.stem)
                if status == "ok" and match:
                    _mark_formd_read(sources_dir(), match.group(1))
            else:
                status, payload = _discover(conn, HnSource(factory("declared")), since=args.since)
        elif args.command == "resolve":
            status, payload = _resolve(conn)
        elif args.command == "find-boards":
            status, payload = _find_boards(conn, factory, args.max_careers)
        elif args.command == "add":
            added = add_by_hand(conn, name=args.name, reason=args.reason, domain=args.domain, state=args.state,
                                ats=args.ats)
            status = "ok" if added.status != "removed" else "failed"
            if status != "ok":
                _say(f"{args.name} is a removed company: nothing was written")
            payload = {"job": "add", "status": status, **asdict(added)}
        else:  # run-due
            code, payload = run_due(conn, factory)
            _emit(payload)
            return code
    _emit(payload)
    return _exit(status)


def main(argv: Sequence[str] | None = None, *, client_factory: ClientFactory | None = None) -> int:
    """Run one command; returns the exit code. `client_factory(profile, **kw)` builds an HTTP client (tests give a
    fake one). A refused live store raises SystemExit, as everywhere else."""
    args = _parser().parse_args(argv)
    try:
        return _run(args, client_factory or default_client)
    except _EXPECTED as err:
        _say(f"error: {describe_error(err)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
