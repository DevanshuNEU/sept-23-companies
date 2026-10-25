"""What moved between two ranker versions (#6, Task 12).

Pure: two stored versions in, a report out. No store, no clock, no network. A weight change is judged here before
it is committed (`python -m outreach.ranking rank --diff-with VERSION` dry-runs the current code and config).

- Ranks are 1-based positions over the rows with no hide, in list order (`rank_positions`).
- A company present in both versions moves when its rank changes by `min_move` or more and it is in the top `top_n`
  of both. One that is in the top `top_n` of only one version is listed as entered or left instead, whatever the size
  of its move, so nobody is listed twice.
- A company whose hide (kind or provisional flag) changed is a hide change and nothing else.
- A company stored in only one version is listed as entered (or left) when it is in that version's top `top_n`.
  Ones further down are not listed: a crawl that adds thousands of companies must not bury the report.
- `top_signal_change` is the signal whose points changed most (largest absolute change, ties by name), for example
  `everify +28.0`.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from outreach.ranking.confidence import scrub_text
from outreach.ranking.score import Scored, to_columns
from outreach.ranking.scores import StoredScore

__all__ = ["Move", "MovedReport", "rank_positions", "what_moved", "scored_to_stored"]

NO_CHANGE = "no signal changed"
_EPSILON = 0.05  # a change of points that rounds to 0.0 is no change


@dataclass(frozen=True)
class Move:
    company_id: int
    name: str
    old_rank: int | None
    new_rank: int | None
    old_score: float | None
    new_score: float | None
    top_signal_change: str
    hide_change: str = ""   # set on hide changes only, for example "no hide to no go"


@dataclass(frozen=True)
class MovedReport:
    old: str
    new: str
    moved_up: list[Move]
    moved_down: list[Move]
    entered_top: list[Move]
    left_top: list[Move]
    hide_changes: list[Move]
    unchanged: int
    min_move: int = 0
    top_n: int = 0

    def lines(self) -> list[str]:
        """The report as plain lines: no dashes, both versions in the first line, one company per line."""
        out = [f"What moved between ranker {self.old or 'unknown'} and ranker {self.new or 'unknown'}"]
        top = f"the top {self.top_n}" if self.top_n else "the top list"
        sections = (
            (f"Moved up (rank change of {self.min_move} or more):", self.moved_up),
            (f"Moved down (rank change of {self.min_move} or more):", self.moved_down),
            (f"Entered {top}:", self.entered_top),
            (f"Left {top}:", self.left_top),
            ("Hide changes:", self.hide_changes),
        )
        if self.min_move and self.top_n:
            out.append(f"Listing a rank change of {self.min_move} or more among the top {self.top_n}.")
        listed = False
        for heading, moves in sections:
            if not moves:
                continue
            listed = True
            out.append(heading)
            out.extend(_line(m) for m in moves)
        if not listed:
            out.append(f"Nothing moved by a rank change of {self.min_move} or more, and nothing entered or left {top}.")
        out.append(f"{self.unchanged} companies in both versions are not listed.")
        return out


def _rank(rank: int | None) -> str:
    return "unranked" if rank is None else str(rank)


def _score(score: float | None) -> str:
    return "none" if score is None else f"{score:g}"


def _line(m: Move) -> str:
    text = f"{scrub_text(m.name)}: rank {_rank(m.old_rank)} to {_rank(m.new_rank)}, score {_score(m.old_score)} to " \
           f"{_score(m.new_score)}"
    note = m.hide_change or m.top_signal_change
    return scrub_text(f"{text} ({note})") if note else scrub_text(text)


def rank_positions(rows: Mapping[int, StoredScore]) -> dict[int, int]:
    """1-based positions over the rows with no hide, highest score first, then the lower company id."""
    visible = sorted((r for r in rows.values() if r.signals.get("hide") is None),
                     key=lambda r: (-r.score, r.company_id))
    return {r.company_id: place for place, r in enumerate(visible, 1)}


def _hide_key(row: StoredScore | None) -> tuple[str, bool] | None:
    hide = row.signals.get("hide") if row is not None else None
    return None if hide is None else (hide["kind"], bool(hide.get("provisional")))


def _hide_text(key: tuple[str, bool] | None) -> str:
    if key is None:
        return "no hide"
    kind, provisional = key
    return f"{kind.replace('_', ' ')}{', pending reader' if provisional else ''}"


def _points(row: StoredScore | None) -> dict[str, float]:
    if row is None:
        return {}
    return {n: float(v.get("points", 0.0)) for n, v in row.signals.get("values", {}).items()}


def _top_signal_change(old: StoredScore | None, new: StoredScore | None) -> str:
    before, after = _points(old), _points(new)
    best_name, best = None, 0.0
    for name in sorted(set(before) | set(after)):
        delta = after.get(name, 0.0) - before.get(name, 0.0)
        if abs(delta) >= _EPSILON and abs(delta) > abs(best):
            best_name, best = name, delta
    return NO_CHANGE if best_name is None else f"{best_name} {best:+.1f}"


def what_moved(old: Mapping[int, StoredScore], new: Mapping[int, StoredScore], names: Mapping[int, str], *,
               min_move: int, top_n: int, old_version: str = "", new_version: str = "") -> MovedReport:
    """Compares two versions; see the module text for the rules. `names` maps company ids to names (a missing id
    reads `company <id>`). `old_version` and `new_version` are only labels for `lines()`."""
    old_pos, new_pos = rank_positions(old), rank_positions(new)
    up: list[Move] = []
    down: list[Move] = []
    entered: list[Move] = []
    left: list[Move] = []
    hides: list[Move] = []
    unchanged = 0

    def make(cid: int, hide_change: str = "") -> Move:
        o, n = old.get(cid), new.get(cid)
        return Move(cid, names.get(cid) or f"company {cid}", old_pos.get(cid), new_pos.get(cid),
                    o.score if o is not None else None, n.score if n is not None else None,
                    _top_signal_change(o, n), hide_change)

    for cid in sorted(set(old) | set(new)):
        o, n = old.get(cid), new.get(cid)
        orank, nrank = old_pos.get(cid), new_pos.get(cid)
        in_old = orank is not None and orank <= top_n
        in_new = nrank is not None and nrank <= top_n
        if o is None or n is None:
            if n is not None and in_new:
                entered.append(make(cid))
            elif o is not None and in_old:
                left.append(make(cid))
            continue
        if _hide_key(o) != _hide_key(n):
            hides.append(make(cid, f"{_hide_text(_hide_key(o))} to {_hide_text(_hide_key(n))}"))
        elif in_new and not in_old:
            entered.append(make(cid))
        elif in_old and not in_new:
            left.append(make(cid))
        elif in_old and in_new and abs(orank - nrank) >= min_move:
            (up if nrank < orank else down).append(make(cid))
        else:
            unchanged += 1

    def gain(m: Move) -> tuple[int, int, int]:
        return (-(m.old_rank - m.new_rank), m.new_rank, m.company_id)

    up.sort(key=gain)
    down.sort(key=lambda m: (-(m.new_rank - m.old_rank), m.new_rank, m.company_id))
    entered.sort(key=lambda m: (m.new_rank, m.company_id))
    left.sort(key=lambda m: (m.old_rank, m.company_id))
    hides.sort(key=lambda m: m.company_id)
    return MovedReport(old=old_version, new=new_version, moved_up=up, moved_down=down, entered_top=entered,
                       left_top=left, hide_changes=hides, unchanged=unchanged, min_move=min_move, top_n=top_n)


def scored_to_stored(rows: Iterable[Scored]) -> dict[int, StoredScore]:
    """Freshly computed rows in the shape of stored ones (the JSON a write would store), for the dry-run path."""
    out = {}
    for s in rows:
        signals, lanes = to_columns(s)
        out[s.company_id] = StoredScore(s.company_id, s.score, tuple(json.loads(lanes)), json.loads(signals), "")
    return out
