"""Confidence grades, the rank confidence and the rank reason line (#6, ruling 2026-10-22).

Pure: no store, no clock (`today` is a parameter). A grade is a level (`low`, `medium`, `high`) and a basis, one plain
line that says what the level rests on. Unknown is `low`, never `high`: only a hand-locked evidence row or a fact the
reader confirmed reads `high` without an observed source.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from outreach.ranking.config import RankerConfig
from outreach.ranking.types import SignalValue

__all__ = ["LEVELS", "scrub_text", "Confidence", "Assessment", "RankReason", "weaker", "down_one", "grade_evidence", "grade_role",
           "grade_no_role", "grade_sponsor", "grade_pay", "grade_reach", "factor", "rank_confidence",
           "build_rank_reason"]

LEVELS = ("low", "medium", "high")
_DASHES = (chr(0x2013), chr(0x2014))
_DASH_RUN = re.compile(r"\s*[" + "".join(_DASHES) + r"]+\s*")
_SPACE_RUN = re.compile(r"\s+")
_SHARE_EPS = 1e-9  # the share thresholds are inclusive; float sums must not flip a boundary case


def scrub_text(text: str) -> str:
    """Reason text is one plain line: an em or en dash becomes a spaced hyphen, any run of whitespace (newlines and
    tabs too) becomes one space, and the ends are trimmed. A posting title with a dash cannot reach a reason."""
    return _SPACE_RUN.sub(" ", _DASH_RUN.sub(" - ", text)).strip()


@dataclass(frozen=True)
class Confidence:
    level: str
    basis: str

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise ValueError(f"confidence level must be one of {LEVELS}, got {self.level!r}")
        if not isinstance(self.basis, str) or not self.basis.strip():
            raise ValueError("a confidence needs a basis in plain words")
        if any(d in self.basis for d in _DASHES):
            raise ValueError(f"a confidence basis must not hold an em or en dash: {self.basis!r}")


@dataclass(frozen=True)
class Assessment(SignalValue):
    """A signal's value and reason plus how sure it is. Still a `SignalValue`, so `check_signal` accepts it."""
    confidence: Confidence

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.confidence, Confidence):
            raise ValueError(f"an assessment needs a Confidence, got {self.confidence!r}")
        object.__setattr__(self, "reason", scrub_text(self.reason))


@dataclass(frozen=True)
class RankReason:
    text: str
    confidence: Confidence

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", scrub_text(self.text))

    @property
    def line(self) -> str:
        return f"{self.text} ({self.confidence.level} confidence: {self.confidence.basis})"


def weaker(a: Confidence, b: Confidence) -> Confidence:
    """The lower level, with that one's basis (`a` on a tie)."""
    return b if LEVELS.index(b.level) < LEVELS.index(a.level) else a


def down_one(c: Confidence, why: str) -> Confidence:
    """One level lower, never below `low`; the basis says why."""
    return Confidence(LEVELS[max(LEVELS.index(c.level) - 1, 0)], f"{c.basis}, {why}")


_COLUMN_LEVEL = {"confirmed": "high", "estimate": "medium", "guess": "low"}


def _as_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def grade_evidence(*, confidence: str | None, locked: bool, source_kind: str | None, observed_on: str | None,
                   today: date, old_days: int, label: str) -> Confidence:
    """Grade one evidence row. No row is `low`; a hand-locked row is `high`; otherwise the column maps `confirmed`,
    `estimate` and `guess` to `high`, `medium` and `low`. Evidence observed more than `old_days` before `today` goes
    one level down and says how old it is (it never goes up)."""
    if confidence is None:
        return Confidence("low", f"no {label} evidence")
    if locked:
        graded = Confidence("high", f"{label} set by hand")
    else:
        source = source_kind or "unknown source"
        level = _COLUMN_LEVEL.get(confidence)
        if level is None:  # an unrecognised column is unknown, so low
            graded = Confidence("low", f"{label} from {source}, unrecognised confidence {confidence}")
        else:
            graded = Confidence(level, f"{label} from {source}, {confidence}")
    seen = _as_date(observed_on)
    if seen is not None and (today - seen).days > old_days:
        graded = down_one(graded, f"observed {seen.isoformat()}, over {old_days} days old")
    elif seen is None and observed_on:  # a date was recorded but cannot be read: keep the level, say so
        graded = Confidence(graded.level, f"{graded.basis}, observed date unknown")
    return graded


_BOARD_STATES = ("read_ok", "read_failed", "never_read", "no_board")


def _check_board_state(state: str) -> None:
    if state not in _BOARD_STATES:
        raise ValueError(f"board state must be one of {_BOARD_STATES}, got {state!r}")


def grade_role(*, board_state: str, posted_known: bool, last_seen_on: str) -> Confidence:
    """Confidence in a live role, from how its board was last read."""
    _check_board_state(board_state)
    if board_state == "read_ok":
        if posted_known:
            return Confidence("high", "listed at the board's last good read, posted date known")
        return Confidence("medium", "listed at the board's last good read, no posted date")
    if board_state == "read_failed":
        return Confidence("medium", f"last board read failed, last listed {last_seen_on}")
    if board_state == "never_read":
        return Confidence("low", f"board not read by this system yet, listing is from an import dated {last_seen_on}")
    return Confidence("low", "posting is not from a board read")


def grade_no_role(*, company_board_state: str, stale_kinds: tuple[str, ...], stale_reason: str) -> Confidence:
    """Confidence that a company has no live role: stale postings, else what its boards say."""
    _check_board_state(company_board_state)
    if "old" in stale_kinds:
        return Confidence("high", f"only stale postings: {stale_reason}")
    if stale_kinds:
        return Confidence("medium", f"only stale postings: {stale_reason}; a first empty read can be a glitch")
    return {
        "read_ok": Confidence("high", "board read ok, no matching posting listed"),
        "read_failed": Confidence("medium", "board read failed, roles unknown"),
        "never_read": Confidence("low", "board not read yet, roles unknown"),
        "no_board": Confidence("low", "no board found yet: roles are unknown, not absent"),
    }[company_board_state]


def grade_sponsor(*, provisional: bool | None) -> Confidence:
    """`None` means no live posting. A final decision (`provisional` False) is a verified reading."""
    if provisional is None:
        return Confidence("low", "no live posting to read sponsorship from")
    if provisional:
        return Confidence("medium", "phrase rules only, reader has not confirmed")
    return Confidence("high", "reader confirmed the wording with a quote")


def grade_pay(*, source: str, straddles: bool, filings: int, medium_filings: int) -> Confidence:
    if source == "posted":
        if straddles:
            return Confidence("medium", "level changes inside the posted range")
        return Confidence("high", "posted range, same level at the minimum and the midpoint")
    if source == "lca":
        level = "medium" if filings >= medium_filings else "low"
        return Confidence(level, f"LCA history, {filings} junior filings")
    if source == "":
        return Confidence("low", "no posted pay and too few LCA filings")
    raise ValueError(f"pay source must be 'posted', 'lca' or '', got {source!r}")


def grade_reach(*, via: str, has_team_source: bool) -> Confidence:
    if via == "person":
        if has_team_source:
            return Confidence("high", "works on the team, source on file")
        return Confidence("medium", "person on file, no source")
    if via == "hn_post":
        return Confidence("medium", "HN post asks for email")
    if via == "":
        return Confidence("low", "no person recorded yet")
    raise ValueError(f"reach route must be 'person', 'hn_post' or '', got {via!r}")


def factor(c: Confidence, cfg: RankerConfig) -> float:
    """What a positive component is multiplied by (1.0, 0.85 or 0.5 shipped). Never above 1.0."""
    return cfg.section("confidence")[f"factor_{c.level}"]


def _percents(shares: Sequence[float]) -> list[int]:
    """Whole percents that sum to 100, by largest remainder (ties go to the earlier share). A share above zero never
    shows as 0%: it takes one point from the largest."""
    exact = [x * 100 for x in shares]
    out = [math.floor(x + _SHARE_EPS) for x in exact]
    by_remainder = sorted(range(len(out)), key=lambda i: (-(exact[i] - out[i]), i))
    for i in by_remainder[:100 - sum(out)]:
        out[i] += 1
    for i, x in enumerate(shares):
        if x > _SHARE_EPS and out[i] == 0:
            donor = max(range(len(out)), key=lambda j: (out[j], -j))
            if out[donor] > 1:
                out[donor] -= 1
                out[i] = 1
    return out


def rank_confidence(parts: Mapping[str, tuple[float, Confidence]], cfg: RankerConfig) -> Confidence:
    """How much of a company's score rests on facts. `parts` maps a signal name to its `raw_points` (before the
    discount) and confidence; zero parts are ignored and a negative part counts by its absolute value."""
    settings = cfg.section("confidence")
    weight = {name: abs(points) for name, (points, _c) in parts.items() if points != 0}
    total = sum(weight.values())
    if not weight or total <= 0:
        return Confidence("low", "no signal has evidence")
    share = {level: 0.0 for level in LEVELS}
    for name, w in weight.items():
        share[parts[name][1].level] += w / total
    ranked = sorted(weight, key=lambda n: (-weight[n], n))
    largest = parts[ranked[0]][1].level
    if largest == "low" or share["low"] >= settings["rank_low_share"] - _SHARE_EPS:
        level = "low"
    elif largest == "high" and share["high"] >= settings["rank_high_share"] - _SHARE_EPS:
        level = "high"
    else:
        level = "medium"
    high, medium, low = _percents([share["high"], share["medium"], share["low"]])
    basis = f"{high}% of the points rest on high-confidence facts"
    if medium:
        basis += f", {medium}% on medium-confidence facts"
    if low:
        names = [n.replace("_", " ") for n in ranked if parts[n][1].level == "low"][:3]
        basis += f", {low}% on guesses or missing data ({', '.join(names)})"
    return Confidence(level, basis)


def build_rank_reason(reasons: Sequence[str], confidence: Confidence, cfg: RankerConfig) -> RankReason:
    """The first `reason_count` reasons joined by "; ", or "No signal has evidence yet"."""
    count = cfg.section("confidence")["reason_count"]
    clean = [r for r in (scrub_text(r) for r in reasons) if r]
    text = "; ".join(clean[:count]) if clean else "No signal has evidence yet"
    return RankReason(text=text, confidence=confidence)
