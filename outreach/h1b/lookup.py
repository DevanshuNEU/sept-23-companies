"""Read the H-1B list back (#252): `companies.md` as one tag per company name.

`python -m outreach.h1b build` writes `<store folder>/h1b/companies.md`; this reads it, read only, for the job list,
the Outbox and the MCP. The file is parsed once and again only when it changes (its mtime and size), so a row costs one
dict lookup. `OUTREACH_H1B_DIR` names another folder (tests).

A company with no line has no H-1B on record. That claim needs a list to stand on, so with no file, an unreadable one
or one with no table, `tag_for` answers None and nothing is shown.
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from outreach.h1b import h1b_key
from outreach.store.db import db_path

__all__ = ["NO_RECORD", "H1bTag", "h1b_dir", "tag_for", "built_on", "has_list", "forget", "stamp"]

NO_RECORD = "No H-1B on record"
_YEAR = re.compile(r"FY(\d{4})")

_lock = threading.Lock()
_cache: dict = {"stamp": None, "path": None, "rows": None, "built": None}
_BUILT = re.compile(r"^Built (\d{4}-\d{2}-\d{2})", re.M)


@dataclass(frozen=True)
class H1bTag:
    total: int  # approvals over the years the list shows
    trend: str  # rising, falling, steady, new, stopped, one year, none
    years: tuple[tuple[int, int], ...] = ()  # (fiscal year, approvals)
    uscis_name: str = ""  # the USCIS employer name(s) the list matched, as it prints them

    @property
    def label(self) -> str:
        return NO_RECORD if not self.total else f"H-1B {self.total:,}, {self.trend}"

    @property
    def detail(self) -> str:
        if not self.years:
            return "No USCIS H-1B approvals for this company in the list."
        return "USCIS approvals by fiscal year: " + ", ".join(f"FY{y} {n}" for y, n in self.years)


_NONE = H1bTag(0, "none")


def h1b_dir() -> Path:
    return Path(os.environ.get("OUTREACH_H1B_DIR") or Path(db_path()).parent / "h1b")


def forget() -> None:
    """Drop the parsed file (a test, or after a rebuild in the same process)."""
    with _lock:
        _cache.update(stamp=None, path=None, rows=None, built=None)


def stamp() -> tuple[int, int] | None:
    """The list file's (mtime, size), None without one: a cache key for a view that shows a tag, so a new build shows
    at once."""
    try:
        st = (h1b_dir() / "companies.md").stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _parse(text: str) -> dict[str, H1bTag] | None:
    """key -> tag, None when the text holds no table (a half written or empty file)."""
    years: list[int] | None = None
    first_year = last_year = 0
    rows: dict[str, H1bTag] = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = _cells(line)
        if years is None:
            found = [_YEAR.fullmatch(c) for c in cells]
            if cells and cells[0] == "Company" and any(found):
                years = [int(m.group(1)) for m in found if m]
                first_year = next(i for i, m in enumerate(found) if m)
                last_year = first_year + len(years)
                trend_at = last_year
            continue
        if len(cells) <= trend_at or not cells[0] or not all(c.isdigit() for c in cells[first_year:last_year]):
            continue  # the rule row, a note or a broken line
        counts = tuple(zip(years, (int(c) for c in cells[first_year:last_year])))
        total = sum(n for _, n in counts)
        key = h1b_key(cells[0])
        if not total or not key:
            continue
        if key not in rows or total > rows[key].total:
            rows[key] = H1bTag(total, cells[trend_at], counts, cells[trend_at + 1] if len(cells) > trend_at + 1 else "")
    return rows if years is not None else None


def _load() -> dict[str, H1bTag] | None:
    """The parsed list, None without a usable one."""
    path = h1b_dir() / "companies.md"
    seen = stamp()
    if seen is None:
        return None
    with _lock:
        if _cache["path"] == path and _cache["stamp"] == seen:
            return _cache["rows"]
    try:
        text = path.read_text(encoding="utf-8")
        rows = _parse(text)
    except (OSError, UnicodeDecodeError):
        return None
    built = _BUILT.search(text)
    with _lock:
        _cache.update(stamp=seen, path=path, rows=rows, built=built.group(1) if built else None)
    return rows


def has_list() -> bool:
    """True when a usable list is built."""
    return _load() is not None


def built_on() -> str | None:
    """The day the list was built (YYYY-MM-DD), None without a usable list or a build line."""
    if _load() is None:
        return None
    with _lock:
        return _cache["built"]


def tag_for(name: str | None) -> H1bTag | None:
    """The H-1B tag of a company by its name; `H1bTag(0, "none")` (label `NO_RECORD`) for one the list does not hold;
    None when there is no usable list or no name."""
    key = h1b_key(name)
    if not key:
        return None
    rows = _load()
    if rows is None:
        return None
    return rows.get(key, _NONE)
