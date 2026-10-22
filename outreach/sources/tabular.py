"""Read a CSV or xlsx table with the standard library only (#31 Task 3).

`read_rows` streams: an xlsx is opened with zipfile and parsed with iterparse, clearing each row, so a 500 MB
sheet never sits in memory (only the shared strings table does). Error is not empty: a file that cannot be read
to the end, or parses to no data rows, raises. It never reads as "no employers".
"""
import csv
import re
import zipfile
import zlib
from pathlib import Path
from typing import Iterator, Sequence
from xml.etree import ElementTree as ET


class TabularError(Exception):
    """The file is broken, truncated, or not a table we can read."""


class MissingColumns(TabularError):
    """The header lacks wanted columns."""


class EmptyTable(TabularError):
    """The file has a header (or nothing at all) but no data rows."""


def normalize_header(h: str) -> str:
    """Strip, upper case, `-` to `_`: `H-1B_DEPENDENT` and `H_1B_DEPENDENT` are one column."""
    return h.strip().upper().replace("-", "_")


MAX_MEMBER_BYTES = 2 * 1024**3


def read_rows(path: Path, wanted: Sequence[str], *,
              max_member_bytes: int = MAX_MEMBER_BYTES) -> Iterator[dict[str, str]]:
    """Yield one dict per data row with exactly the `wanted` keys ('' for a blank or absent cell). Numbers come
    back as their text, so a date cell is its serial, such as '45930'. Format is chosen by suffix (.csv or .xlsx).
    A generator: every error is raised when the rows are consumed, and a file that ends early raises after the
    rows before the damage, never quietly.

    A row with no value in any cell (a blank line, or cells that are all empty) is not data, in either format, so
    a header plus only such rows raises EmptyTable.

    xlsx members (the sheet, sharedStrings.xml, workbook.xml and its rels) whose declared uncompressed size is over
    `max_member_bytes` (default 2 GiB; the largest DOL sheet seen is about 0.5 GB) raise TabularError naming the
    member, so a decompression bomb is refused before it is read. zipfile never reads past the declared size, so a
    header that understates the size cannot get around the cap. One huge cell is not bounded: it has to fit in
    memory as text. That is acceptable here because the files are DOL's own disclosure data, where no cell is
    larger than a few hundred characters, and the cap already limits the whole member."""
    path = Path(path)
    suffix = path.suffix.lower()
    keep: dict = {"columns": None}  # xlsx columns to store: None (all) for the header row, then the wanted ones
    if suffix == ".csv":
        raw = _csv_rows(path)
    elif suffix == ".xlsx":
        raw = _xlsx_rows(path, keep, max_member_bytes)
    else:
        raise TabularError(f"{path.name}: unsupported file type {path.suffix!r} (want .csv or .xlsx)")
    wanted = list(wanted)
    positions: dict[int, list[str]] = {}
    count = 0
    header_seen = False
    for is_header, cells in raw:
        if is_header:
            header_seen = True
            index: dict[str, int] = {}
            for col, text in cells.items():
                index.setdefault(normalize_header(text), col)
            missing = [w for w in wanted if normalize_header(w) not in index]
            if missing:
                raise MissingColumns(f"{path.name}: missing columns {', '.join(missing)}")
            for w in wanted:
                positions.setdefault(index[normalize_header(w)], []).append(w)
            keep["columns"] = set(positions)  # the xlsx reader now stores only these columns
            continue
        row = dict.fromkeys(wanted, "")
        for col, keys in positions.items():
            text = cells.get(col, "")
            for key in keys:
                row[key] = text
        count += 1
        yield row
    if not header_seen:
        raise EmptyTable(f"{path.name}: no header row")
    if count == 0:
        raise EmptyTable(f"{path.name}: header but no data rows")


# --- CSV ---------------------------------------------------------------------------------------------------

def _csv_rows(path: Path):
    """Yield (is_header, {column: text}). The header carries every column; data rows are whole rows too (cheap)."""
    header_done = False
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            for record in csv.reader(f):
                if not any(c.strip() for c in record):
                    continue
                cells = dict(enumerate(record))
                yield (not header_done), cells
                header_done = True
    except (UnicodeDecodeError, csv.Error, OSError) as exc:
        raise TabularError(f"{path.name}: cannot read CSV: {exc}") from exc


# --- xlsx --------------------------------------------------------------------------------------------------

_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_REL_STRICT = "{http://purl.oclc.org/ooxml/officeDocument/relationships}id"
_DAMAGE = (zipfile.BadZipFile, zlib.error, EOFError, ET.ParseError, KeyError, IndexError, ValueError, OSError)
_LETTERS = re.compile(r"[A-Za-z]+")


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _column_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _text_of(el) -> str:
    """Join every `<t>` of an `<si>` or `<is>`: plain text and rich-text runs, not phonetic (`rPh`) runs."""
    parts = []
    for child in el:
        name = _local(child.tag)
        if name == "t":
            parts.append(child.text or "")
        elif name == "r":
            parts.extend(t.text or "" for t in child if _local(t.tag) == "t")
    return "".join(parts)


def _check_size(zf: zipfile.ZipFile, member: str, cap: int) -> None:
    size = zf.getinfo(member).file_size  # KeyError (a missing member) is wrapped by the caller
    if size > cap:
        raise TabularError(f"{member} is {size} bytes uncompressed, over the {cap} byte limit")


def _sheet_member(zf: zipfile.ZipFile, cap: int) -> str:
    """The zip member holding the first sheet named in workbook.xml."""
    _check_size(zf, "xl/workbook.xml", cap)
    _check_size(zf, "xl/_rels/workbook.xml.rels", cap)
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    sheet = next((el for el in workbook.iter() if _local(el.tag) == "sheet"), None)
    if sheet is None:
        raise TabularError("workbook names no sheet")
    rid = sheet.get(_REL) or sheet.get(_REL_STRICT)
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    for rel in rels:
        if rel.get("Id") == rid:
            target = rel.get("Target", "")
            return target.lstrip("/") if target.startswith("/") else "xl/" + target
    raise TabularError("workbook sheet has no relationship")


def _shared_strings(zf: zipfile.ZipFile, cap: int) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    _check_size(zf, "xl/sharedStrings.xml", cap)
    strings: list[str] = []
    root = None
    with zf.open("xl/sharedStrings.xml") as f:
        for event, el in ET.iterparse(f, events=("start", "end")):
            if event == "start":
                if root is None:
                    root = el  # the sst element, which would otherwise keep every emptied si shell
                continue
            if _local(el.tag) == "si":
                strings.append(_text_of(el))
                root.clear()
    return strings


def _cell_text(c, shared: list[str]) -> str:
    kind = c.get("t")
    if kind == "inlineStr":
        for child in c:
            if _local(child.tag) == "is":
                return _text_of(child)
        return ""
    v = next((child for child in c if _local(child.tag) == "v"), None)
    text = (v.text or "") if v is not None else ""
    if kind == "s":
        return shared[int(text)] if text else ""
    return text


def _has_content(c) -> bool:
    """True when a cell holds a non-empty value (`<v>` text, or text in an inline string)."""
    for child in c:
        if child.text or (len(child) and _text_of(child)):
            return True
    return False


def _xlsx_rows(path: Path, keep, cap: int):
    """Yield (is_header, {column: text}) from the first sheet. The header row keeps every cell; once the caller
    has set `keep["columns"]` (a set of column indexes), data rows keep only those. A row with no value in any cell is
    skipped."""
    try:
        with zipfile.ZipFile(path) as zf:
            member = _sheet_member(zf, cap)
            _check_size(zf, member, cap)
            shared = _shared_strings(zf, cap)
            header_done = False
            cells: dict[int, str] = {}
            has_value = False
            last = -1
            sheet_data = None
            with zf.open(member) as f:
                for event, el in ET.iterparse(f, events=("start", "end")):
                    if event == "start":
                        if sheet_data is None and _local(el.tag) == "sheetData":
                            sheet_data = el  # holds every emptied row shell unless cleared
                        continue
                    name = _local(el.tag)
                    if name == "c":
                        ref = el.get("r")
                        match = _LETTERS.match(ref) if ref else None
                        col = _column_index(match.group()) if match else last + 1
                        last = col
                        if len(el):
                            if not has_value and _has_content(el):
                                has_value = True
                            wanted_cols = keep["columns"]
                            if wanted_cols is None or col in wanted_cols:
                                text = _cell_text(el, shared)
                                if text != "":
                                    cells[col] = text
                        el.clear()
                    elif name == "row":
                        if has_value:
                            yield (not header_done), cells
                            header_done = True
                        cells, has_value, last = {}, False, -1
                        el.clear()
                        if sheet_data is not None:
                            sheet_data.clear()
    except TabularError as exc:
        raise TabularError(f"{path.name}: {exc}") from exc
    except _DAMAGE as exc:
        raise TabularError(f"{path.name}: unreadable xlsx ({type(exc).__name__}: {exc})") from exc
