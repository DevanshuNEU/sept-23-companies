"""Readers for the DevVault notes: company files and interview prep.

`parse_frontmatter` reads the small YAML-like subset those files use and never drops what it does not understand:
a shape it cannot read stays as the raw text of its key.
"""
import re
from pathlib import Path
from typing import Iterator

from outreach.importer.read_text import _text
from outreach.importer.records import SourceError, SourceRecord

_KEY = re.compile(r"^([A-Za-z_][\w-]*):(?:\s+(.*))?$")
_ITEM_START = re.compile(r"^(\s*)-\s+([A-Za-z_][\w-]*):(?:\s+(.*))?$")
_ITEM_FIELD = re.compile(r"^\s+([A-Za-z_][\w-]*):(?:\s+(.*))?$")
_SESSION = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-\d+)?\.md$")


def _scalar(text: str | None):
    """A scalar value: quotes stripped, `null` and `~` to None, a trailing ` # comment` dropped. Everything else
    stays the text it was (a date is not turned into a date, a number is not turned into a number)."""
    text = (text or "").strip()
    if text[:1] in ("'", '"'):
        quote, out, i = text[0], [], 1
        while i < len(text):
            ch = text[i]
            if quote == '"' and ch == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                return "".join(out)
            out.append(ch)
            i += 1
        return text  # an unclosed quote: keep the text as written
    text = re.split(r"\s#", text, maxsplit=1)[0].strip()
    return None if text in ("", "null", "~") else text


def _list_of_maps(block: list[str]):
    """The block as a list of dicts when every line is `- key: value` or `  key: value`; otherwise None."""
    items: list[dict] = []
    for line in block:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if m := _ITEM_START.match(line):
            items.append({m.group(2): _scalar(m.group(3))})
        elif (m := _ITEM_FIELD.match(line)) and items and not line.lstrip().startswith("-"):
            items[-1][m.group(1)] = _scalar(m.group(2))
        else:
            return None
    return items or None


def parse_frontmatter(text: str) -> dict | None:
    """The frontmatter of a note, or None when the text does not start with `---` (or never closes it)."""
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].rstrip() != "---":
        return None
    try:
        end = next(i for i, line in enumerate(lines[1:], start=1) if line.rstrip() == "---")
    except StopIteration:
        return None
    body = lines[1:end]
    out: dict = {}
    stray: list[str] = []
    i = 0
    while i < len(body):
        line = body[i]
        i += 1
        if not line.strip() or (line.startswith("#")):
            continue
        m = None if line[:1].isspace() else _KEY.match(line)
        if not m:
            stray.append(line)
            continue
        key, value = m.group(1), m.group(2)
        if value is not None and value.strip():
            out[key] = _scalar(value)
            continue
        block = []
        while i < len(body) and (not body[i].strip() or body[i][:1].isspace()):
            block.append(body[i])
            i += 1
        while block and not block[-1].strip():
            block.pop()
        if not block:
            out[key] = None
        else:
            parsed = _list_of_maps(block)
            out[key] = parsed if parsed is not None else "\n".join(block)
    if stray:
        out["_unparsed"] = "\n".join(stray)
    return out


def _folder_or_error(folder: Path, source: str) -> Path:
    folder = Path(folder)
    if not folder.is_dir():
        raise SourceError(f"{source}: no folder at {folder}")
    return folder


def read_devvault_notes(folder: Path) -> Iterator[SourceRecord]:
    """One record per `*.md` under `folder`, except README.md: `file:<relative path>`, raw `{"path", "folder",
    "frontmatter"}`. `folder` is the first part of the path ("" for a file directly in `folder`)."""
    folder = _folder_or_error(folder, "devvault_notes")
    for file in sorted(folder.rglob("*.md")):
        if file.name == "README.md" or not file.is_file():
            continue
        relative = file.relative_to(folder).as_posix()
        parts = relative.split("/")
        yield SourceRecord("devvault_notes", f"file:{relative}", {
            "path": relative,
            "folder": parts[0] if len(parts) > 1 else "",
            "frontmatter": parse_frontmatter(_text(file, "devvault_notes")),
        })


def read_interview_prep(folder: Path) -> Iterator[SourceRecord]:
    """One record per subfolder: `folder:<slug>`, raw `{"slug", "brief", "session_dates", "has_brief"}`. The dates
    are the sorted, distinct dates in the names of `sessions/YYYY-MM-DD-N.md`."""
    folder = _folder_or_error(folder, "interview_prep")
    for sub in sorted(p for p in folder.iterdir() if p.is_dir()):
        brief = sub / "brief.md"
        dates = sorted({m.group(1) for f in (sub / "sessions").glob("*.md") if (m := _SESSION.match(f.name))}) \
            if (sub / "sessions").is_dir() else []
        yield SourceRecord("interview_prep", f"folder:{sub.name}", {
            "slug": sub.name,
            "brief": parse_frontmatter(_text(brief, "interview_prep")) if brief.is_file() else None,
            "session_dates": dates,
            "has_brief": brief.is_file(),
        })
