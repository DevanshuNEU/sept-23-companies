"""The outcome dataset as a CSV or JSON file, for his own analysis (#81).

Company level by default. `--with-people` adds one field, the name of the person behind the best outcome (a real
person with an address or a profile on file, never an importer placeholder). No field ever holds an email address or
a LinkedIn URL. The file is written outside the repo only, under the folder he names or `export_dir`, through a temp
file and `os.replace`, so a failed write leaves no partial file. A CSV cell that could run as a spreadsheet formula
starts with a `'`.
"""
from __future__ import annotations

import csv
import json
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from outreach.insights.bands import hq_state
from outreach.insights.config import InsightsConfig
from outreach.insights.dataset import OutcomeRow

__all__ = ["COMPANY_FIELDS", "PEOPLE_FIELDS", "FORMATS", "ExportError", "safe_cell", "to_records", "resolve_out",
           "write_csv", "write_json"]

COMPANY_FIELDS = ("company_id", "company", "status", "merged_ids", "best_outcome", "outcome_source", "touches",
                  "channels", "best_channel", "best_role_kind", "best_lane", "first_touch_on", "first_reply_on",
                  "days_to_reply", "interview_on", "offer_on", "size_low", "size_high", "size_confidence",
                  "size_band", "stage", "stage_band", "hq", "hq_state", "everify", "sponsorship", "board_system",
                  "level_tier", "level_band", "level_basis", "score", "rank")
PEOPLE_FIELDS = ("best_person",)
FORMATS = ("csv", "json")
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
# The checkout this code runs from (live_checkout.REPO_ROOT, found from this file so the package stays inside
# `outreach` and the standard library).
REPO_ROOT = Path(__file__).resolve().parents[2]


class ExportError(ValueError):
    """The export cannot be written where asked: inside the repo, or over a file without --force."""


def safe_cell(value) -> str:
    """One CSV cell: None is empty, a list joins with `;`, and text that starts with `=`, `+`, `-`, `@`, a tab or a
    carriage return gets a leading `'` so a spreadsheet shows it and never runs it."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return safe_cell(";".join("" if v is None else str(v) for v in value))
    if isinstance(value, str):
        return "'" + value if value.startswith(_FORMULA_STARTS) else value
    return str(value)


def to_records(rows: Iterable[OutcomeRow], *, with_people: bool) -> list[dict]:
    """One dict per row with the `COMPANY_FIELDS` (and `best_person` only `with_people`), lists as lists."""
    out = []
    for r in rows:
        rec = {
            "company_id": r.company_id, "company": r.name, "status": r.status, "merged_ids": list(r.merged_ids),
            "best_outcome": r.best_outcome, "outcome_source": r.outcome_source, "touches": r.touches,
            "channels": list(r.channels), "best_channel": r.best_channel, "best_role_kind": r.best_role_kind,
            "best_lane": r.best_lane, "first_touch_on": r.first_touch_on, "first_reply_on": r.first_reply_on,
            "days_to_reply": r.days_to_reply, "interview_on": r.interview_on, "offer_on": r.offer_on,
            "size_low": r.size_low, "size_high": r.size_high, "size_confidence": r.size_confidence,
            "size_band": r.features.size_band, "stage": r.stage, "stage_band": r.features.stage_band, "hq": r.hq,
            "hq_state": hq_state(r.hq), "everify": r.everify, "sponsorship": r.sponsorship,
            "board_system": r.board_system, "level_tier": r.level_tier, "level_band": r.features.level_band,
            "level_basis": r.level_basis, "score": r.score, "rank": r.rank,
        }
        if with_people:
            rec["best_person"] = r.best_person
        out.append(rec)
    return out


def resolve_out(out: str | None, fmt: str, today: str, cfg: InsightsConfig, *, force: bool) -> Path:
    """Where the export goes: `export_dir/outcomes-<today>.<fmt>` with no `out`, that file name inside `out` when it
    is a folder, else `out` itself. The parent folder is created. `ExportError` for a path inside the repo (where it
    could be committed) or an existing file without `force`."""
    if fmt not in FORMATS:
        raise ExportError(f"format must be one of {', '.join(FORMATS)}, not {fmt!r}")
    name = f"outcomes-{today}.{fmt}"
    if out is None:
        path = Path(cfg.export_dir).expanduser() / name
    else:
        path = Path(out).expanduser()
        if path.is_dir() or out.endswith(("/", os.sep)):
            path = path / name
    path = Path(os.path.realpath(path.absolute()))
    if path == REPO_ROOT or path.is_relative_to(REPO_ROOT):
        raise ExportError(f"{path} would write inside the repo, where it could be committed; pick a folder outside "
                          f"{REPO_ROOT}")
    if path.exists() and not force:
        raise ExportError(f"{path} already exists; pass --force to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _atomic_write(path: Path, write) -> None:
    """`write(file)` into a temp file beside `path`, then `os.replace`; the temp file is removed on any failure."""
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            write(f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def write_csv(records: Sequence[Mapping], fields: Sequence[str], path: Path) -> None:
    def write(f):
        w = csv.writer(f)
        w.writerow(fields)
        for rec in records:
            w.writerow([safe_cell(rec.get(name)) for name in fields])
    _atomic_write(Path(path), write)


def write_json(records: Sequence[Mapping], fields: Sequence[str], path: Path, meta: dict) -> None:
    """`{"generated_at", "ranker_version", "fields", "rows", "notes"}`; `meta` gives the first two."""
    people = any(name in PEOPLE_FIELDS for name in fields)
    notes = ["Facts as known now, not at the time of each touch, with their confidence.",
             "Includes people's names; never addresses or LinkedIn URLs." if people else "Company level only."]
    body = {"generated_at": meta.get("generated_at"), "ranker_version": meta.get("ranker_version"),
            "fields": list(fields), "rows": [{name: rec.get(name) for name in fields} for rec in records],
            "notes": notes}
    _atomic_write(Path(path), lambda f: f.write(json.dumps(body, indent=2, ensure_ascii=False) + "\n"))
