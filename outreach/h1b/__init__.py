"""The H-1B list (#252): how many H-1B petitions USCIS approved for each company, per fiscal year, with a trend.

The source is USCIS's H-1B Employer Data Hub, the same data H1BGrader and MyVisaJobs show: the yearly CSV files
(`h1b_datahubexport-<year>.csv` from the archive page, and the hub's own Crosstab export for the newest years), read
from a folder once downloaded. Nothing here sends a request. A row's approvals are its initial plus continuing
approvals.

`build` writes plain markdown next to the store (never into the public repo, since the list names the companies he
tracks):

- `companies.md`: one line per Knock company matched to a USCIS employer, its approvals per year and its trend.
  A company that is not there has no H-1B on record.
- `unsure.md`: companies with open postings whose name only nearly matches a USCIS employer, for him to decide.
- `confirmed.md`: his answers, written by hand (`- Knock name = USCIS NAME` or `- Knock name = none`). A build
  always follows them, and never asks about a confirmed company again.

Names match by `h1b_key`: lower case, letters and digits only, with trailing legal words (Inc, LLC, LLP, Corp,
Corporation, Ltd...) and a few generic ones (Laboratories, Technologies, Holdings, USA...) dropped, on the company's
name and its name aliases. Knock's own `name_key` keeps "Corporation" and "LLP", so "MICROSOFT CORPORATION" missed
Microsoft (2026-11-13 preview); this key is the H-1B list's only and changes no company identity. A USCIS name with
"DBA" counts under both its names.
"""
from __future__ import annotations

import bisect
import csv
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from outreach.store.writes import NAME_ALIAS_SOURCES

__all__ = ["H1bRow", "BuildReport", "h1b_key", "read_uscis", "trend", "parse_confirmed", "build"]

MIN_PREFIX = 5  # a near match needs a shared start at least this long ("databricks" and "databricksengineering")
MAX_CANDIDATES = 3
MIN_ASK = 3  # a near USCIS name with fewer approvals than this in the years shown is not worth asking about
RISE, FALL = 1.25, 0.75  # the last year against the mean of the two before it
_DBA = re.compile(r"\s+d/?b/?a\s+", re.I)
_LEGAL = {"inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "corp", "corporation", "co", "company", "plc",
          "pllc", "pc", "pbc", "na", "sa", "ag", "gmbh", "bv"}
_GENERIC = {"laboratories", "labs", "technologies", "technology", "holdings", "group", "usa", "us", "america",
            "international", "com"}


def h1b_key(name: str | None) -> str:
    """The H-1B list's name key: trailing legal and generic words dropped, then letters and digits only. A name made
    only of such words keeps its last one."""
    words = re.findall(r"[a-z0-9]+", (name or "").lower().replace("&", " and "))
    while len(words) > 1 and words[-1] in _LEGAL | _GENERIC:
        words.pop()
    return "".join(words)


@dataclass(frozen=True)
class H1bRow:
    year: int
    employer: str
    approvals: int  # initial plus continuing approvals (every approval column)
    keys: tuple[str, ...]
    hub: bool = False  # from the hub's Crosstab export, which replaces the archive file of the same year


@dataclass(frozen=True)
class BuildReport:
    years: tuple[int, ...]
    employers: int
    matched: int
    unsure: int


def _int(text: str | None) -> int:
    text = (text or "").replace(",", "").strip()
    return int(text) if text.isdigit() else 0


def _keys(employer: str) -> tuple[str, ...]:
    parts = [p for p in _DBA.split(employer) if p.strip()]
    keys = [k for k in (h1b_key(p) for p in parts) if k]
    return tuple(dict.fromkeys(keys))


def _column(header: list[str], *words: str) -> str | None:
    for name in header:
        low = name.lower()
        if all(w in low for w in words):
            return name
    return None


def _encoding(path: Path) -> str:
    """The hub's Crosstab export is UTF-16 with a byte order mark (2026-11-13); the archive files are UTF-8."""
    with open(path, "rb") as fh:
        start = fh.read(2)
    return "utf-16" if start in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"


def read_uscis(path: Path):
    """The rows of one USCIS file, archive or Crosstab export: the year, the employer, its approvals and name keys.
    The archive file is UTF-8 with commas; the Crosstab export is UTF-16 with tabs and six approval columns."""
    with open(path, newline="", encoding=_encoding(path), errors="replace") as fh:
        first = fh.readline()
        fh.seek(0)
        reader = csv.DictReader(fh, delimiter="\t" if "\t" in first else ",")
        header = [h.strip() for h in (reader.fieldnames or [])]
        reader.fieldnames = header
        year_col, name_col = _column(header, "fiscal", "year"), _column(header, "employer")
        approval_cols = [h for h in header if "approval" in h.lower()]
        hub = any("new employment" in h.lower() for h in header)
        if not (year_col and name_col and approval_cols):
            raise ValueError(f"{Path(path).name}: not a USCIS H-1B data hub file (columns: {', '.join(header)})")
        for r in reader:
            employer = (r.get(name_col) or "").strip()
            year = _int(r.get(year_col))
            keys = _keys(employer)
            if not employer or not year or not keys:
                continue
            yield H1bRow(year, employer, sum(_int(r.get(c)) for c in approval_cols), keys, hub)


def trend(counts: dict[int, int], years: list[int]) -> str:
    """rising, falling, steady, new, stopped, one year, or none, from approvals per year over `years`."""
    series = [counts.get(y, 0) for y in years]
    if not any(series):
        return "none"
    if len(series) == 1:
        return "one year"
    last, before = series[-1], series[:-1]
    if last and not any(before):
        return "new"
    if not last and not series[-2]:
        return "stopped"
    base = sum(before[-2:]) / len(before[-2:])
    if base == 0:
        return "rising"
    ratio = last / base
    return "rising" if ratio >= RISE else "falling" if ratio <= FALL else "steady"


def parse_confirmed(text: str) -> dict[str, str | None]:
    """His answers: `- Knock name = USCIS NAME` (a match) or `- Knock name = none` (no match). Other lines are notes."""
    out: dict[str, str | None] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("- ") or " = " not in line:
            continue
        knock, uscis = (part.strip() for part in line[2:].split(" = ", 1))
        if knock:
            out[knock] = None if uscis.lower() == "none" else uscis
    return out


# Companies with an open software posting at his level (fit or level not stated) under the current ruleset: the ones
# worth asking him about.
_SOFTWARE_OPEN_SQL = ("SELECT DISTINCT p.company_id FROM posting p JOIN decision d ON d.posting_id = p.posting_id "
                      "AND d.ruleset_version = (SELECT ruleset_version FROM ruleset WHERE is_current = 1) "
                      "WHERE p.closed_at IS NULL AND d.lane = 'engineering' AND COALESCE(d.role, 'software') = 'software' "
                      "AND COALESCE(d.level_state, CASE WHEN d.level_ok = 1 THEN 'fit' "
                      "WHEN d.level_tier IN ('not_stated', 'open') THEN 'uncertain' ELSE 'reject' END) "
                      "IN ('fit', 'uncertain')")


def _companies(conn: sqlite3.Connection) -> list[tuple[int, str, set[str], bool]]:
    """Live companies: (id, name, name keys, has an open software posting)."""
    keys: dict[int, set[str]] = defaultdict(set)
    marks = ", ".join("?" for _ in NAME_ALIAS_SOURCES)
    for cid, alias in conn.execute(f"SELECT company_id, alias FROM company_alias WHERE source IN ({marks})",
                                   NAME_ALIAS_SOURCES):
        keys[cid].add(h1b_key(alias))
    open_ids = {r[0] for r in conn.execute(_SOFTWARE_OPEN_SQL)}
    out = []
    for cid, name in conn.execute("SELECT company_id, name FROM company WHERE merged_into IS NULL "
                                  "AND status <> 'removed' ORDER BY name COLLATE NOCASE, company_id"):
        own = {h1b_key(name)} | keys.get(cid, set())
        out.append((cid, name, {k for k in own if k}, cid in open_ids))
    return out


def _near(key: str, sorted_keys: list[str], known: set[str]) -> set[str]:
    """USCIS keys that start with `key`, or that `key` starts with (covering more than half of it), sharing at least
    MIN_PREFIX characters."""
    if len(key) < MIN_PREFIX:
        return set()
    found = set()
    i = bisect.bisect_left(sorted_keys, key)
    while i < len(sorted_keys) and sorted_keys[i].startswith(key):
        found.add(sorted_keys[i])
        i += 1
    # A USCIS key that is only the start of this one must cover more than half of it ("alpha" is no near name for
    # "alphanexus").
    found |= {key[:n] for n in range(max(MIN_PREFIX, len(key) // 2 + 1), len(key)) if key[:n] in known}
    return found


def build(conn: sqlite3.Connection, source_dir: Path, out_dir: Path, *, years: list[int] | None = None,
          now: datetime | None = None) -> BuildReport:
    """Read every USCIS file in `source_dir`, match the employers to Knock's companies and write the markdown files
    in `out_dir` (made if missing). `years`: the fiscal years shown, default every year the files hold."""
    source_dir, out_dir = Path(source_dir), Path(out_dir)
    per_key: dict[str, Counter] = defaultdict(Counter)  # key -> year -> approvals
    names: dict[str, Counter] = defaultdict(Counter)  # key -> the USCIS names under it
    rows = [row for path in sorted(source_dir.glob("*.csv")) for row in read_uscis(path)]
    hub_years = {row.year for row in rows if row.hub}
    for row in rows:
        if row.year in hub_years and not row.hub:  # the hub's whole year replaces the archive's (FY2023 is partial)
            continue
        for key in row.keys:
            per_key[key][row.year] += row.approvals
            names[key][row.employer] += 1
    shown = sorted(years) if years else sorted({y for c in per_key.values() for y in c})
    out_dir.mkdir(parents=True, exist_ok=True)
    confirmed_path = out_dir / "confirmed.md"
    confirmed = parse_confirmed(confirmed_path.read_text(encoding="utf-8")) if confirmed_path.exists() else {}
    known = set(per_key)
    sorted_keys = sorted(known)

    def total(key: str) -> int:
        return sum(per_key[key][y] for y in shown)

    lines, unsure, seen = [], [], set()
    for cid, name, keys, has_open in _companies(conn):
        if name in seen:  # two Knock companies of one name get one line
            continue
        if name in confirmed:
            chosen = confirmed[name]
            match = None if chosen is None else next(iter(_keys(chosen)), None)
            if match is None or match not in known:
                continue
        else:
            hits = [k for k in keys if k in known and total(k)]
            match = max(hits, key=total) if hits else None
        if match is not None:
            counts = per_key[match]
            usc = names[match].most_common(1)[0][0]
            cells = " | ".join(str(counts.get(y, 0)) for y in shown)
            lines.append(f"| {name} | {cells} | {trend(dict(counts), shown)} | {usc} |")
            seen.add(name)
            continue
        if not has_open:
            continue
        near = set().union(*(_near(k, sorted_keys, known) for k in keys)) if keys else set()
        near = sorted((k for k in near if total(k) >= MIN_ASK), key=total, reverse=True)[:MAX_CANDIDATES]
        if near:
            seen.add(name)
            options = "; ".join(f"{names[k].most_common(1)[0][0]} ({total(k)} approvals)" for k in near)
            unsure.append(f"- [ ] {name}: {options}")

    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    head = " | ".join(f"FY{y}" for y in shown)
    rule = " | ".join("---" for _ in shown)
    (out_dir / "companies.md").write_text(
        f"# H-1B approvals by company\n\nBuilt {stamp} from USCIS's H-1B Employer Data Hub (initial plus continuing "
        f"approvals, fiscal years {shown[0]} to {shown[-1]}). A company that is not listed has no H-1B on record.\n"
        f"Trend: the last year against the mean of the two before it (rising at 25% more, falling at 25% less).\n"
        f"To fix a match, add `- Knock name = USCIS NAME` or `- Knock name = none` to confirmed.md and build again.\n\n"
        f"| Company | {head} | Trend | USCIS name |\n| --- | {rule} | --- | --- |\n" + "\n".join(lines) + "\n",
        encoding="utf-8")
    (out_dir / "unsure.md").write_text(
        f"# Names to check\n\nBuilt {stamp}. Each company below has open postings and a USCIS employer with a nearly "
        f"matching name, and an open software posting. Add `- Knock name = USCIS NAME` (same company) or `- Knock name = none` to confirmed.md.\n\n"
        + "\n".join(unsure) + ("\n" if unsure else ""), encoding="utf-8")
    return BuildReport(years=tuple(shown), employers=len(per_key), matched=len(lines), unsure=len(unsure))
