"""The labeled set: postings whose facts Devanshu has confirmed, which `eval` scores reader versions against (#32).

Ported from Job Radar's `label.py` and `labels/RUBRIC.md` (~/Desktop/DevVault/job-search/pipeline), reshaped for the
reader: Job Radar labels a whole posting apply, maybe or no; here every reading field gets a value and a quote.

    uv run python -m outreach.reader gold sample --per-class 30 --seed 1   # draw postings, append them prelabeled
    uv run python -m outreach.reader gold show [--limit N]                 # the next pending items, for a sitting
    uv run python -m outreach.reader gold confirm g0007 [--set sponsorship=wont_sponsor --quote "..."]...
    uv run python -m outreach.reader gold confirm --all-shown              # what the last show printed, untouched
    uv run python -m outreach.reader gold coverage                         # confirmed against target per stratum

The file (`~/Library/Application Support/outreach/gold/items.jsonl` by default, outside the repo so the live checkout
stays fast-forwardable; `--file` overrides it; one JSON object per line, and `tests/gold/items.jsonl` is an empty
copy of the format):

    {"gold_id": "g0001", "stratum": "sponsorship:wont_sponsor", "title": str, "location": str, "body": str,
     "labels": {<every name in FIELDS>: {"value": ..., "quote": str}},
     "status": "prelabeled" | "confirmed", "confirmed_on": "YYYY-MM-DD" | null, "country": str | null}

`country` is the posting's structured country (the rules read it with the location, as combine does); it may be left
out, which means none.

Bodies are stored in the file (public postings), so tests and eval never need the store. `stratum` names the draw
the item came from: `"<field>:<class>"` for a rare class, `"random"` or `"disagree"`. A `prelabeled` item may have
empty `labels` (drawn, not yet pre-labeled); any labels it has must be complete. Claude pre-labels in the side
session by editing the `labels` of prelabeled items; nothing in this repo calls Claude for that. Devanshu confirms.

Why stratified (label.py's reason, still true): the hide classes are rare (about 2% of postings say "will sponsor"),
so a random draw would need thousands of labels to hold 30 of one. Each rare class is drawn on its own, with the
rules' decision as the proxy for "class" when picking; the label is whatever Devanshu confirms. Job Radar weighted
each stratum by its inverse sampling rate for a Horvitz-Thompson recall over the whole population; the reader's eval
scores per field and class instead (precision and recall inside a class do not need population weights), so no
weight is stored.

Labeling rules (ported from RUBRIC.md's settled rules; a label describes the posting text, never the company):

1. Every known value quotes the posting word for word (title, location or body). No quote, no fact: label unknown.
2. Judge the stated requirement, never a word (rubric rule 1). `level` is what the posting says the level is; a
   "Senior" in a title with "2+ years" is `early_career` only if the text says so. `years_low` is the number the text
   asks for; a posting that states none is null, never a guess (rule 2: unknown years is not a rejection).
3. A citizenship requirement, an active clearance or ITAR is `citizenship_or_clearance = yes` (rule 6). "Will not
   sponsor H-1B" is NOT that: it is `sponsorship = wont_sponsor`, a separate field.
4. A posting that says it will not sponsor is `wont_sponsor` even at a company known to sponsor (rubric Q6): the
   label reads this posting.
5. `location` is the posting's own location text; its class (us, non_us) is derived by the rules' `us_status`
   (rule 5: non-US is a hide).
6. `start_date` is the start the text states ("Summer 2027"); next year's cohort is read from it (rule 4).

What the sitting shows (label.py's blind rule, adapted): the title, the body around each quote and the pre-label.
Never the rules' decision and never a stored reading, so the set measures the reader's accuracy, not Devanshu's
agreement with the predictors being scored. The pre-label is Claude's, which is the price of a 30-minute sitting;
correct it whenever it is wrong.

Not ported: Job Radar's refusal to start while the rubric has UNDECIDED rules (the rules above are settled and live
in this docstring, not a separate file) and its monthly self-agreement audit (Cohen's kappa over 50 re-served rows),
which needs a few hundred confirmed items before it means anything.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sqlite3
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from outreach.reader.combine import explain
from outreach.reader.config import ReaderConfig, load_reader_config
from outreach.reader.submit import min_words
from outreach.reader.types import FIELDS, INT_FIELDS, TEXT_FIELDS, FieldReading, _collapse, check_value, quote_in_body
from outreach.rules import Decision, PostingInput, decide
from outreach.rules.config import Config, default_config
from outreach.rules.eligibility import starts_2027
from outreach.rules.location import us_status
from outreach.store import body_sha256

__all__ = ["GOLD_DIR", "GOLD_PATH", "GOLD_READINGS", "FORMAT_PATH", "STRATA", "STRATUM_NAMES", "HIDE_CLASSES", "TARGET_PER_CLASS",
           "BOUND_TARGET", "FieldReading", "GoldItem", "load_gold", "sample_gold", "pending", "coverage", "confirm",
           "rules_fields", "value_class", "show_text", "coverage_text", "labels_digest", "atomic_write",
           "check_label"]

# The real set lives outside the repo, so a sitting in the live checkout never edits a tracked file and the checkout
# stays fast-forwardable (controller ruling, 2026-10-24). Its readings live beside it, one file per reader version.
GOLD_DIR = Path.home() / "Library" / "Application Support" / "outreach" / "gold"
GOLD_PATH = GOLD_DIR / "items.jsonl"
GOLD_READINGS = GOLD_DIR / "readings"
# The repo keeps only an empty file in the format; tests pass their own paths.
FORMAT_PATH = Path(__file__).resolve().parents[2] / "tests" / "gold" / "items.jsonl"

# One (name, field, class) per stratum, in the order they are filled: the four hide classes first (they decide what
# the gate can measure), then the rarer show classes, then "silent", the disagreements and a random draw (field and
# class None for those two).
STRATA: tuple[tuple[str, str | None, str | None], ...] = (
    ("sponsorship:wont_sponsor", "sponsorship", "wont_sponsor"),
    ("citizenship_or_clearance:yes", "citizenship_or_clearance", "yes"),
    ("student_or_grad_window:yes", "student_or_grad_window", "yes"),
    ("location:non_us", "location", "non_us"),
    ("sponsorship:will_sponsor", "sponsorship", "will_sponsor"),
    ("sponsorship:opt_only", "sponsorship", "opt_only"),
    ("level:intern", "level", "intern"),
    ("level:new_grad", "level", "new_grad"),
    ("level:early_career", "level", "early_career"),
    ("sponsorship:silent", "sponsorship", "silent"),
    ("disagree", None, None),
    ("random", None, None),
)
STRATUM_NAMES = tuple(name for name, _, _ in STRATA)
# The classes that can hide a posting (combine.HIDE_FIELDS with their hide value).
HIDE_CLASSES: tuple[tuple[str, str], ...] = (("sponsorship", "wont_sponsor"), ("citizenship_or_clearance", "yes"),
                                             ("student_or_grad_window", "yes"), ("location", "non_us"))
# First set: 30 confirmed per stratum. A hide class needs 150 with no error before it may rely on the 2% bound
# (rule of three, research section 3.3).
TARGET_PER_CLASS = 30
BOUND_TARGET = 150

_ITEM_KEYS = ("gold_id", "stratum", "title", "location", "body", "labels", "status", "confirmed_on")
_OPTIONAL_KEYS = ("country",)
_STATUSES = ("prelabeled", "confirmed")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_NO_FACT = ("unknown", "silent", None, "")
_SPONSORSHIP = {"no": "wont_sponsor", "yes": "will_sponsor", "opt_only": "opt_only", "silent": "silent",
                "unclear": "unknown"}
_LEVEL = {"new_grad": "new_grad", "early_career": "early_career", "swe1": "early_career", "swe2": "mid",
          "open": "mid", "senior": "senior_plus", "not_stated": "unknown"}
_US = {"yes": "us", "no": "non_us"}


@dataclass(frozen=True)
class GoldItem:
    gold_id: str
    stratum: str
    title: str
    location: str
    body: str
    labels: dict[str, FieldReading]
    status: str
    confirmed_on: str | None
    country: str | None = None

    def to_json(self) -> dict:
        return {"gold_id": self.gold_id, "stratum": self.stratum, "title": self.title, "location": self.location,
                "body": self.body,
                "labels": {name: {"value": f.value, "quote": f.quote} for name, f in self.labels.items()},
                "status": self.status, "confirmed_on": self.confirmed_on, "country": self.country}


# ---- reading and checking the file ------------------------------------------------------------------------------

def _found(text: str, item_parts: tuple[str, str, str], floor: int) -> bool:
    """`text` word for word inside one part (title, location, body): the check `submit` makes."""
    return any(quote_in_body(text, part, min_words=floor) for part in item_parts)


def check_label(name: str, label: FieldReading, title: str, location: str, body: str) -> None:
    """ValueError unless `label` is a value field `name` allows, with a quote found in the text when it states a
    fact (a free-text value must itself be in the text or be the quote). A quote given for a no-fact value must be
    there too."""
    check_value(name, label.value)
    if not isinstance(label.quote, str):
        raise ValueError(f"{name}: quote must be text")
    parts = (title, location, body)
    if label.value not in _NO_FACT and not label.quote.strip():
        raise ValueError(f"{name}: a known value needs a quote")
    if label.quote.strip() and not _found(label.quote, parts, min_words(name)):
        raise ValueError(f"{name}: quote {label.quote!r} is not word for word in the title, location or body")
    if name in TEXT_FIELDS and label.value and not (
            _found(label.value, parts, 1) or _collapse(label.value) == _collapse(label.quote)):
        raise ValueError(f"{name}: value {label.value!r} is not in the text and is not its quote")


def _item(obj, line: int) -> GoldItem:
    def bad(message: str) -> ValueError:
        return ValueError(f"line {line}: {message}")

    if not isinstance(obj, dict):
        raise bad("an item must be an object")
    missing = [k for k in _ITEM_KEYS if k not in obj]
    extra = [k for k in obj if k not in _ITEM_KEYS + _OPTIONAL_KEYS]
    if missing or extra:
        raise bad(f"missing {', '.join(missing)}" if missing else f"unexpected {', '.join(map(str, extra))}")
    for key in ("gold_id", "stratum", "title", "location", "body"):
        if not isinstance(obj[key], str):
            raise bad(f"{key} must be text")
    if not isinstance(obj.get("country"), (str, type(None))):
        raise bad("country must be text or null")
    if not obj["gold_id"].strip():
        raise bad("gold_id must not be empty")
    if obj["stratum"] not in STRATUM_NAMES:
        raise bad(f"stratum {obj['stratum']!r} is not one of {', '.join(STRATUM_NAMES)}")
    if obj["status"] not in _STATUSES:
        raise bad(f"status must be one of {', '.join(_STATUSES)}")
    when = obj["confirmed_on"]
    if obj["status"] == "confirmed" and not (isinstance(when, str) and _DATE.match(when)):
        raise bad(f"{obj['gold_id']}: a confirmed item needs confirmed_on as YYYY-MM-DD")
    if obj["status"] == "prelabeled" and when is not None:
        raise bad(f"{obj['gold_id']}: confirmed_on is set on an item that is not confirmed")
    raw = obj["labels"]
    if not isinstance(raw, dict):
        raise bad("labels must be an object")
    if raw or obj["status"] == "confirmed":
        gone = [n for n in FIELDS if n not in raw]
        if gone:
            raise bad(f"{obj['gold_id']}: labels missing field {', '.join(gone)}")
        extra = [n for n in raw if n not in FIELDS]
        if extra:
            raise bad(f"{obj['gold_id']}: labels have unknown field {', '.join(map(str, extra))}")
    labels: dict[str, FieldReading] = {}
    for name in FIELDS:
        if name not in raw:
            continue
        entry = raw[name]
        if not isinstance(entry, dict) or set(entry) != {"value", "quote"}:
            raise bad(f"{obj['gold_id']}: label {name} must be an object with value and quote")
        label = FieldReading(entry["value"], entry["quote"])
        try:
            check_label(name, label, obj["title"], obj["location"], obj["body"])
        except ValueError as err:
            raise bad(f"{obj['gold_id']}: {err}") from None
        labels[name] = label
    return GoldItem(obj["gold_id"], obj["stratum"], obj["title"], obj["location"], obj["body"], labels,
                    obj["status"], when, obj.get("country"))


def _parse(lines: list[str]) -> list[GoldItem]:
    items: list[GoldItem] = []
    seen: dict[str, int] = {}
    for number, text in enumerate(lines, start=1):
        if not text.strip():
            continue
        try:
            obj = json.loads(text)
        except ValueError as err:
            raise ValueError(f"line {number}: not JSON: {err}") from None
        got = _item(obj, number)
        if got.gold_id in seen:
            raise ValueError(f"line {number}: duplicate gold_id {got.gold_id} (first on line {seen[got.gold_id]})")
        seen[got.gold_id] = number
        items.append(got)
    return items


def _lines(path: Path) -> list[str]:
    return Path(path).read_text(encoding="utf-8").splitlines()


def load_gold(path: Path = GOLD_PATH) -> list[GoldItem]:
    """Every item in the file, in order. ValueError naming the line (and field) for anything off: a missing field,
    a value outside ALLOWED, a quote not in the title, location or body, a duplicate gold_id, a confirmed item with
    no confirmed_on."""
    return _parse(_lines(path))


def pending(items: Iterable[GoldItem]) -> list[GoldItem]:
    """The items not yet confirmed, in file order."""
    return [i for i in items if i.status == "prelabeled"]


# ---- the rules' side, in the reader's words ----------------------------------------------------------------------

def rules_fields(d: Decision) -> dict[str, str | None]:
    """The rules' decision as one class per reading field (the inverse of combine's table); None for the fields the
    rules have no counterpart for (years_high, remote, pay)."""
    intern = d.lane == "intern"
    return {
        "engineering": "yes" if d.lane in ("engineering", "intern") else "no",
        "level": "intern" if intern else _LEVEL.get(d.level_tier, "unknown"),
        "years_low": "unknown" if d.years_asked is None else str(d.years_asked),
        "years_high": None,
        "sponsorship": _SPONSORSHIP.get(d.sponsorship, "unknown"),
        "citizenship_or_clearance": "yes" if d.citizenship else "unknown",
        "student_or_grad_window": "yes" if d.eligibility == "excluded" else "unknown",
        "start_date": "starts_2027" if d.start_2027 else "unknown",
        "location": _US.get(d.us, "unknown"),
        "remote": None,
        "pay": None,
    }


def value_class(name: str, value, cfg: Config | None = None) -> str:
    """The class a label or reading value falls in, for scoring: the value itself for a closed list; for location the
    rules' `us_status` (us, non_us, unknown); a number of years as text; start_date `starts_2027`, `other` or
    `unknown`; pay `stated` or `unknown`."""
    if name in INT_FIELDS:
        return "unknown" if not isinstance(value, int) or isinstance(value, bool) else str(value)
    if name in TEXT_FIELDS:
        if not isinstance(value, str) or not value.strip():
            return "unknown"
        cfg = cfg if cfg is not None else default_config()
        if name == "location":
            return _US.get(us_status(value, None, cfg), "unknown")
        if name == "start_date":
            return "starts_2027" if starts_2027("", value, cfg) else "other"
        return "stated"
    return value if isinstance(value, str) else "unknown"


# ---- drawing a sample ------------------------------------------------------------------------------------------

def _candidates(conn: sqlite3.Connection, cfg: ReaderConfig, rules_cfg: Config) -> list[dict]:
    """Open postings the reader would read (engineering or intern lane, body long enough), each with the rules'
    classes, in posting order."""
    out = []
    for row in conn.execute(
            "SELECT p.posting_id, p.title, p.location, p.country, b.body FROM posting p JOIN posting_body b "
            "ON b.posting_id = p.posting_id WHERE p.closed_at IS NULL ORDER BY p.posting_id"):
        body = row["body"] or ""
        if len(" ".join(body.split())) < cfg.min_body_chars:
            continue
        d = decide(PostingInput(row["title"], body, row["location"] or "", row["country"]), rules_cfg)
        if d.lane not in ("engineering", "intern"):
            continue
        out.append({"posting_id": row["posting_id"], "title": row["title"], "location": row["location"] or "",
                    "country": row["country"], "body": body, "sha": body_sha256(body), "rules": rules_fields(d)})
    return out


def _disagrees(conn: sqlite3.Connection, posting_id: int, cfg: ReaderConfig, rules_cfg: Config) -> bool:
    return any(v.state == "disagree" for v in explain(conn, posting_id, cfg=cfg, rules_cfg=rules_cfg))


def sample_gold(conn: sqlite3.Connection, per_class: int, *, seed: int, existing: set[str],
                first_id: int = 1, cfg: ReaderConfig | None = None,
                rules_cfg: Config | None = None) -> list[dict]:
    """Draw up to `per_class` postings per stratum, as item dicts with status `prelabeled` and empty labels.

    Strata are filled in `STRATA` order, each posting at most once (the first stratum it fits), from the postings the
    reader would read; `disagree` takes postings whose stored reading (promoted, else candidate version) disagrees
    with the rules on some field; `random` takes from what is left. A posting whose body hash is in `existing`, or
    repeats one already drawn, is skipped. Deterministic for a seed. Ids run from `g<first_id>` up. Read-only.
    """
    if per_class < 1:
        raise ValueError(f"per_class must be at least 1, got {per_class}")
    cfg = cfg if cfg is not None else load_reader_config()
    rules_cfg = rules_cfg if rules_cfg is not None else default_config()
    pool = _candidates(conn, cfg, rules_cfg)
    random.Random(seed).shuffle(pool)
    used = set(existing)
    picked: list[tuple[str, dict]] = []
    for name, field, cls in STRATA:
        taken = 0
        for c in pool:
            if taken >= per_class:
                break
            if c["sha"] in used:
                continue
            if field is not None and c["rules"][field] != cls:
                continue
            if name == "disagree" and not _disagrees(conn, c["posting_id"], cfg, rules_cfg):
                continue
            used.add(c["sha"])
            picked.append((name, c))
            taken += 1
    return [{"gold_id": f"g{first_id + n:04d}", "stratum": name, "title": c["title"], "location": c["location"],
             "body": c["body"], "labels": {}, "status": "prelabeled", "confirmed_on": None, "country": c["country"]}
            for n, (name, c) in enumerate(picked)]


# ---- confirming ----------------------------------------------------------------------------------------------

def atomic_write(path: Path, text: str) -> None:
    """Write `text` to a temp file in the same folder, then `os.replace` it over `path`. A failure before the
    replace leaves `path` as it was and removes the temp file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def confirm(items_path: Path, gold_id: str, *, set_fields: dict[str, FieldReading] | None = None,
            today: str) -> GoldItem:
    """Apply `set_fields` (corrections) to item `gold_id`, mark it confirmed on `today`, and rewrite the file
    atomically; every other line stays byte for byte. ValueError, with the file untouched, for an unknown id, a bad
    date, a correction outside ALLOWED or whose quote is not in the text, or labels that are not complete."""
    if not isinstance(today, str) or not _DATE.match(today):
        raise ValueError(f"today must be YYYY-MM-DD, got {today!r}")
    lines = _lines(items_path)
    items = _parse(lines)
    found = [n for n, text in enumerate(lines) if text.strip() and json.loads(text).get("gold_id") == gold_id]
    if not found:
        raise ValueError(f"no item {gold_id} in {items_path}")
    index = found[0]
    current = next(i for i in items if i.gold_id == gold_id)
    labels = dict(current.labels)
    for name, label in (set_fields or {}).items():
        if name not in FIELDS:
            raise ValueError(f"unknown field {name!r}")
        check_label(name, label, current.title, current.location, current.body)
        labels[name] = label
    done = replace(current, labels={n: labels[n] for n in FIELDS if n in labels}, status="confirmed",
                   confirmed_on=today)
    new_line = json.dumps(done.to_json(), ensure_ascii=False)
    _parse([new_line])  # the same checks load_gold makes, before anything is written
    lines[index] = new_line
    atomic_write(Path(items_path), "".join(line + "\n" for line in lines))
    return done


def append_items(items_path: Path, new: list[dict]) -> None:
    """Add `new` item dicts at the end of the file (made if missing), atomically, after checking the whole result."""
    path = Path(items_path)
    lines = _lines(path) if path.exists() else []
    lines = [line for line in lines if line.strip()] + [json.dumps(i, ensure_ascii=False) for i in new]
    _parse(lines)
    atomic_write(path, "".join(line + "\n" for line in lines))


def next_id(items: Iterable[GoldItem]) -> int:
    """One more than the highest `g<number>` id in use."""
    numbers = [int(i.gold_id[1:]) for i in items if re.fullmatch(r"g\d+", i.gold_id)]
    return max(numbers, default=0) + 1


def labels_digest(item: GoldItem) -> str:
    """A short hash of an item's labels, to tell whether it changed after a sitting saw it."""
    raw = json.dumps(item.to_json()["labels"], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---- what a sitting reads ---------------------------------------------------------------------------------------

_EXCERPT = 120


def _excerpt(quote: str, item: GoldItem) -> str:
    """Where the quote is: the title, the location, or the body around it."""
    q = _collapse(quote)
    if not q:
        return ""
    if q in _collapse(item.title):
        return "(in the title)"
    if q in _collapse(item.location):
        return "(in the location)"
    flat = " ".join(item.body.split())
    at = flat.lower().find(q)
    if at < 0:
        return ""
    start, end = max(at - _EXCERPT, 0), min(at + len(q) + _EXCERPT, len(flat))
    return ("..." if start else "") + flat[start:end] + ("..." if end < len(flat) else "")


def show_text(items: list[GoldItem]) -> str:
    """The items for a sitting, one block each: id and stratum, title and location, the opening of the body, then a
    line per field with the pre-label and its quote, and the body around each quote."""
    blocks = []
    for item in items:
        flat = " ".join(item.body.split())
        lines = [f"== {item.gold_id}  [{item.stratum}]",
                 f"   {item.title}",
                 f"   {item.location or '(no location given)'}",
                 f"   {flat[:400]}{'...' if len(flat) > 400 else ''}",
                 ""]
        if not item.labels:
            lines.append("   (no pre-label yet: Claude fills the labels in the side session first)")
        for name in FIELDS:
            if name not in item.labels:
                continue
            label = item.labels[name]
            quote = f'  "{label.quote}"' if label.quote else ""
            lines.append(f"   {name:<25} {json.dumps(label.value, ensure_ascii=False)}{quote}")
            where = _excerpt(label.quote, item) if label.quote else ""
            if where:
                lines.append(f"   {'':<25}   {where}")
        blocks.append("\n".join(lines))
    if not blocks:
        return "nothing pending: every item is confirmed (gold sample draws more)"
    return "\n\n".join(blocks)


def coverage(items: Iterable[GoldItem]) -> dict[str, tuple[int, int]]:
    """Per stratum: (confirmed items drawn for it, TARGET_PER_CLASS)."""
    counts = {name: 0 for name in STRATUM_NAMES}
    for i in items:
        if i.status == "confirmed":
            counts[i.stratum] += 1
    return {name: (n, TARGET_PER_CLASS) for name, n in counts.items()}


def coverage_text(items: list[GoldItem], rules_cfg: Config | None = None) -> str:
    """Confirmed against target per stratum, then for each hide class the rule-of-three line: with n confirmed
    items of that class (by label), 0 errors would bound the error rate at 3/n."""
    lines = [f"{name:<30} {n:>4}/{target}" for name, (n, target) in coverage(items).items()]
    confirmed = [i for i in items if i.status == "confirmed"]
    lines.append("")
    for field, cls in HIDE_CLASSES:
        n = sum(1 for i in confirmed if value_class(field, i.labels[field].value, rules_cfg) == cls)
        head = f"{field} = {cls}:"
        if n == 0:
            lines.append(f"{head} n=0, no bound yet")
            continue
        enough = "" if n >= BOUND_TARGET else f" (the 2% bound needs {BOUND_TARGET})"
        lines.append(f"{head} n={n}, 0 errors would bound the error rate at 3/{n} = {300 / n:.1f}%{enough}")
    return "\n".join(lines)
