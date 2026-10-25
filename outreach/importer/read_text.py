"""Readers for the text and JSON sources: contacted.txt, curated touches, devboard, the tracker backup,
contacts.json and send.log.

Each reader is a generator of `SourceRecord`. A missing or undecodable file raises `SourceError` naming the source
and the path, never an empty run. Readers only read; the files are opened for reading and nothing else.
"""
import csv
import json
import re
from pathlib import Path
from typing import Iterator

from outreach.importer.records import SourceError, SourceRecord


def _text(path: Path, source: str) -> str:
    path = Path(path)
    if not path.is_file():
        raise SourceError(f"{source}: no file at {path}")
    try:
        # newline="" keeps every line ending as it is, so a CRLF file reads the same on any machine.
        with open(path, encoding="utf-8", newline="") as f:
            return f.read()
    except UnicodeDecodeError as e:
        raise SourceError(f"{source}: {path} is not UTF-8 text: {e}") from e
    except OSError as e:
        raise SourceError(f"{source}: cannot read {path}: {e}") from e


def _json(path: Path, source: str):
    try:
        return json.loads(_text(path, source))
    except json.JSONDecodeError as e:
        raise SourceError(f"{source}: {path} is not valid JSON: {e}") from e


def _lines(path: Path, source: str) -> list[str]:
    return _text(path, source).splitlines()


# ---- contacted.txt ----------------------------------------------------------------------------------------------

_CONTACTED = re.compile(r"^(\d{4}-\d{2}-\d{2}) - (.+?) - (.+)$")
_NOTE = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")


def _person(text: str) -> dict:
    text = text.strip()
    m = _NOTE.match(text)
    if m and m.group(1):
        return {"name": m.group(1).strip(), "note": m.group(2).strip()}
    return {"name": text, "note": None}


def read_contacted_txt(path: Path) -> Iterator[SourceRecord]:
    """One record per line (`line:<n>`, from 1). Raw `{"line", "kind"}`, and for a touch also `date`, `company`,
    `rest` and `people`. The company is the second field and the rest keeps any later dash."""
    for n, line in enumerate(_lines(path, "contacted_txt"), start=1):
        stripped = line.strip()
        raw: dict = {"line": stripped if not stripped else line}
        if not stripped:
            raw["kind"] = "blank"
        elif stripped.startswith("#"):
            raw["kind"] = "comment"
        else:
            m = _CONTACTED.match(stripped)
            if m:
                date, company, rest = m.groups()
                raw.update({
                    "line": stripped, "kind": "touch", "date": date, "company": company.strip(), "rest": rest.strip(),
                    "people": [_person(p) for p in rest.split(" + ") if p.strip()],
                })
            else:
                raw["kind"] = "unparsed"
        yield SourceRecord("contacted_txt", f"line:{n}", raw)


# ---- curated touches --------------------------------------------------------------------------------------------

CURATED_COLUMNS = ("date", "company", "band", "role", "channel", "outcome", "src", "fu", "req")


def read_curated_touches(path: Path) -> Iterator[SourceRecord]:
    """One record per CSV row (`row:<n>`, from 1, not counting the header), the nine columns as text."""
    text = _text(path, "curated_touches")
    reader = csv.DictReader(text.splitlines())
    missing = [c for c in CURATED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise SourceError(f"curated_touches: {path} has no column {', '.join(missing)}")
    for n, row in enumerate(reader, start=1):
        yield SourceRecord("curated_touches", f"row:{n}", {c: row.get(c) or "" for c in CURATED_COLUMNS})


# ---- devboard ---------------------------------------------------------------------------------------------------

def _parsed_key(key: str) -> dict | None:
    """`ats:token:id` for greenhouse, ashby and lever; Workday `workday:tenant:wdN:site:id` becomes token
    `tenant.wdN/site`, the format the crawler stores. Anything else (hcafe, apify) has no parsed key."""
    head = key.split(":", 1)[0]
    if head in ("greenhouse", "ashby", "lever"):
        parts = key.split(":", 2)
        if len(parts) == 3 and all(parts):
            return {"ats": head, "token": parts[1], "native_id": parts[2]}
    elif head == "workday":
        parts = key.split(":", 4)
        if len(parts) == 5 and all(parts):
            _, tenant, wd, site, native = parts
            return {"ats": "workday", "token": f"{tenant}.{wd}/{site}", "native_id": native}
    return None


def read_devboard(path: Path) -> Iterator[SourceRecord]:
    """One record per key in job-state.json (`job:<key>`): the entry, its `key` and its `parsed_key`."""
    data = _json(path, "devboard")
    if not isinstance(data, dict):
        raise SourceError(f"devboard: {path} must hold an object of jobs")
    for key, value in data.items():
        if not isinstance(value, dict):
            raise SourceError(f"devboard: {path} entry {key!r} is not an object")
        yield SourceRecord("devboard", f"job:{key}", {**value, "key": key, "parsed_key": _parsed_key(key)})


# ---- tracker backup -----------------------------------------------------------------------------------------------

def read_local_storage(path: Path) -> Iterator[SourceRecord]:
    """One record per company id (`company:<id>`) in the old dashboard's backup; the raw is the entry as exported."""
    data = _json(path, "local_storage")
    if not isinstance(data, dict):
        raise SourceError(f"local_storage: {path} must hold an object keyed by company id")
    for company_id, entry in data.items():
        if not isinstance(entry, dict):
            raise SourceError(f"local_storage: {path} entry {company_id!r} is not an object")
        yield SourceRecord("local_storage", f"company:{company_id}", entry)


# ---- contacts.json ----------------------------------------------------------------------------------------------

def read_contacts_json(path: Path) -> Iterator[SourceRecord]:
    """One record per contact (`contact:<id>`), with the file's `firstSendDate` added as `first_send_date`."""
    data = _json(path, "contacts_json")
    if not isinstance(data, dict) or not isinstance(data.get("contacts"), list):
        raise SourceError(f"contacts_json: {path} must hold an object with a contacts list")
    first_send_date = data.get("firstSendDate")
    seen = set()
    for contact in data["contacts"]:
        if not isinstance(contact, dict) or not contact.get("id"):
            raise SourceError(f"contacts_json: {path} has a contact with no id")
        if contact["id"] in seen:
            raise SourceError(f"contacts_json: {path} repeats the contact id {contact['id']!r}")
        seen.add(contact["id"])
        yield SourceRecord("contacts_json", f"contact:{contact['id']}", {**contact, "first_send_date": first_send_date})


# ---- send.log ---------------------------------------------------------------------------------------------------

_STAMP = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] ?(.*)$")
_SENT = re.compile(r"^sent (follow-up )?to (.+?) <([^<>]+)> at (.+?)(?: \(\d+/\d+ today\))?$")
_UNCLEAR = re.compile(r"^send to .+? <?([^\s<>]+@[^\s<>]+?)>? unclear \(")
_FOUND = re.compile(r"^unclear send to (\S+) found in Sent")
_BOUNCED = re.compile(r"^bounced: (\S+)")
_REPLY = re.compile(r"^reply from (\S+)")


def _log_fields(message: str) -> dict:
    if m := _SENT.match(message):
        follow_up, name, email, company = m.groups()
        return {"kind": "sent", "name": name, "email": email, "company": company, "follow_up": bool(follow_up)}
    for kind, pattern in (("found_in_sent", _FOUND), ("unclear", _UNCLEAR), ("bounced", _BOUNCED), ("reply", _REPLY)):
        if m := pattern.match(message):
            return {"kind": kind, "email": m.group(1)}
    return {"kind": "log"}


def read_send_log(path: Path) -> Iterator[SourceRecord]:
    """One record per line (`line:<n>`): raw `{"line", "at", "kind"}`. A `sent` line also names the person, the
    email, the company and whether it was a follow-up; unclear, found, bounced and reply lines name the email.
    A dry-run line is only ever a log line."""
    for n, line in enumerate(_lines(path, "send_log"), start=1):
        m = _STAMP.match(line)
        at, message = (m.group(1), m.group(2)) if m else (None, line)
        fields = {"kind": "log"} if message.startswith("(dry run)") else _log_fields(message)
        yield SourceRecord("send_log", f"line:{n}", {"line": line, "at": at, **fields})
