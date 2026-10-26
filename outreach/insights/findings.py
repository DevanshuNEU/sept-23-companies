"""What the interview companies share (#81), in counts.

For each dimension, over the interview companies: `of` is how many there are and `known` how many have a value. A
dimension is `unknown` when `known` is under `share_min_count`; `shared` when its most common value (one value, not a
tie) has at least `share_min_count` companies and at least half of `known`; otherwise `mixed`. The comparison with
the companies reached without an interview is drawn only when both groups have at least `group_min` companies with a
known value (`enough`); otherwise the line says too few to tell. No rate is computed here at all: the page makes one
from the counts only when `enough`.

Unknown is never a value: a company with no size is counted in `of` and not in `known`, and never shares "no size"
with another. A way in that was never recorded (no channel, or the importer's `other` placeholder) is unknown, and
so is the first contact behind it. Pure: no store, no clock.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from outreach.insights.bands import board_words, hq_state
from outreach.insights.config import InsightsConfig
from outreach.insights.dataset import INTERVIEW_OUTCOMES, OutcomeRow, interview_rows

__all__ = ["DIMENSIONS", "NOUNS", "CHANNEL_PLURAL", "ROLE_KIND_PLURAL", "Shared", "shared_findings"]

DIMENSIONS = ("size_band", "stage_band", "level_band", "board_system", "best_channel", "best_role_kind",
              "sponsorship", "everify", "hq_state")
NOUNS = {"size_band": "size", "stage_band": "stage", "level_band": "role level", "board_system": "job board",
         "best_channel": "way in", "best_role_kind": "first contact", "sponsorship": "sponsorship stance",
         "everify": "E-Verify status", "hq_state": "home state"}
CHANNEL_PLURAL = {"linkedin_request": "LinkedIn requests", "linkedin_message": "LinkedIn messages",
                  "email": "emails", "application": "applications", "hn_reply": "HN replies",
                  "other": "no recorded way in"}
ROLE_KIND_PLURAL = {"engineer": "engineers", "manager": "managers", "founder": "founders",
                    "recruiter": "recruiters", "alum": "alumni", "other": "other contacts"}
_SPONSORSHIP = {"yes": "yes", "no": "no", "opt_only": "OPT only", "unclear": "unclear"}
_EVERIFY = {"enrolled": "enrolled", "not_enrolled": "not enrolled"}
_NO_WAY_IN = (None, "other")
_KIND_ORDER = {"shared": 0, "mixed": 1, "unknown": 2}


@dataclass(frozen=True)
class Shared:
    dimension: str
    kind: str  # shared, mixed, unknown
    value: str | None  # the shared value as stored (a band, an ats code, a channel); None unless shared
    count: int
    of: int
    known: int
    compare_count: int
    compare_of: int
    compare_known: int
    enough: bool
    text: str
    compare_text: str
    spread: tuple[tuple[str, int], ...]  # each known value and its count, most first, then by value


def _channel(r: OutcomeRow) -> str | None:
    return None if r.best_channel in _NO_WAY_IN else r.best_channel


def _role_kind(r: OutcomeRow) -> str | None:
    return None if r.best_channel in _NO_WAY_IN else r.best_role_kind


_VALUE: dict[str, Callable[[OutcomeRow], str | None]] = {
    "size_band": lambda r: r.features.size_band,
    "stage_band": lambda r: r.features.stage_band,
    "level_band": lambda r: r.features.level_band,
    "board_system": lambda r: r.board_system,
    "best_channel": _channel,
    "best_role_kind": _role_kind,
    "sponsorship": lambda r: r.sponsorship,
    "everify": lambda r: r.everify,
    "hq_state": lambda r: hq_state(r.hq),
}


def _words(dimension: str, v: str) -> str:
    """A value as a mixed line lists it."""
    if dimension == "board_system":
        return board_words(v)
    if dimension == "best_channel":
        return CHANNEL_PLURAL.get(v, v.replace("_", " "))
    if dimension == "best_role_kind":
        return ROLE_KIND_PLURAL.get(v, v)
    if dimension == "sponsorship":
        return _SPONSORSHIP.get(v, v)
    if dimension == "everify":
        return _EVERIFY.get(v, v)
    return v


def _phrase(dimension: str, v: str) -> str:
    """What the shared companies do, after "{count} of your {of} interview companies"."""
    if dimension == "size_band":
        return f"have {v} people"
    if dimension == "stage_band":
        return f"are {v}"
    if dimension == "level_band":
        return f"were for {v} roles"
    if dimension == "board_system":
        return f"post jobs on {board_words(v)}"
    if dimension == "best_channel":
        return f"started with {_words(dimension, v)}"
    if dimension == "best_role_kind":
        return f"started with a touch to {_words(dimension, v)}"
    if dimension == "sponsorship":
        return f"have sponsorship on file as {_words(dimension, v)}"
    if dimension == "everify":
        return "are E-Verify enrolled" if v == "enrolled" else "are not E-Verify enrolled"
    return f"are based in {v}"


def _one(dimension: str, mine: Sequence[OutcomeRow], others: Sequence[OutcomeRow], cfg: InsightsConfig) -> Shared:
    value_of = _VALUE[dimension]
    noun = NOUNS[dimension]
    values = [v for v in map(value_of, mine) if v is not None]
    other_values = [v for v in map(value_of, others) if v is not None]
    of, known = len(mine), len(values)
    compare_of, compare_known = len(others), len(other_values)
    spread = tuple(sorted(Counter(values).items(), key=lambda kv: (-kv[1], kv[0])))

    def finding(kind, value=None, count=0, compare_count=0, enough=False, text="", compare_text=""):
        return Shared(dimension, kind, value, count, of, known, compare_count, compare_of, compare_known, enough,
                      text, compare_text, spread)

    if of == 0:
        return finding("unknown", text="No interview companies yet, too few to tell.")
    if known < cfg.share_min_count:
        return finding("unknown", text=f"{noun[0].upper()}{noun[1:]} is known for only {known} of {of}, too few "
                                       f"to tell.")
    top, count = spread[0]
    tied = len(spread) > 1 and spread[1][1] == count
    if count < cfg.share_min_count or count * 2 < known or tied:
        listed = ", ".join(f"{_words(dimension, v)} {c}" for v, c in spread[:3])
        more = f" and {len(spread) - 3} more" if len(spread) > 3 else ""
        return finding("mixed", text=f"No one {noun} stands out: {listed}{more}.")
    known_note = f" ({noun} known for {known})" if known < of else ""
    text = f"{count} of your {of} interview companies {_phrase(dimension, top)}{known_note}."
    compare_count = sum(1 for v in other_values if v == top)
    enough = known >= cfg.group_min and compare_known >= cfg.group_min
    if enough:
        compare_text = (f"Of the {compare_of} companies you reached without an interview, {compare_count} of "
                        f"{compare_known} with a known {noun} do too.")
    elif of < cfg.group_min:
        compare_text = (f"Too few to tell whether this sets them apart: that takes {cfg.group_min} interview "
                        f"companies, and you have {of}.")
    elif known < cfg.group_min:
        compare_text = (f"Too few to tell whether this sets them apart: that takes {cfg.group_min} with a known "
                        f"{noun}, and you have {known}.")
    else:
        compare_text = (f"Too few to tell whether this sets them apart: that takes {cfg.group_min} companies "
                        f"reached without an interview with a known {noun}, and you have {compare_known}.")
    return finding("shared", top, count, compare_count, enough, text, compare_text)


def shared_findings(rows: Sequence[OutcomeRow], cfg: InsightsConfig) -> list[Shared]:
    """One finding per dimension: `shared` first (most companies first), then `mixed`, then `unknown`; ties in
    `DIMENSIONS` order. The interview group is `interview_rows(rows)`; the comparison group is every other row."""
    mine = interview_rows(rows)
    others = [r for r in rows if r.best_outcome not in INTERVIEW_OUTCOMES]
    found = [_one(d, mine, others, cfg) for d in DIMENSIONS]
    order = {d: i for i, d in enumerate(DIMENSIONS)}
    return sorted(found, key=lambda s: (_KIND_ORDER[s.kind], -s.count if s.kind == "shared" else 0,
                                        order[s.dimension]))
