"""The ATS seed's gate (#192): which Greenhouse and Ashby boards a free company list names are worth adding.

`gate` reads one list answer and decides from it alone: no store, no clock, no network. The seed run (the next task)
reads each candidate board once, calls `gate`, and writes the verdict.

A board is promoted when at least one open job is US engineering, by the rules' own checks on what a list answer
carries: the title's lane (`classify_title`, which reads a title with no body as engineering unless it is off lane) and
the US reading of the location and country (`us_status`, the same call `decide` makes). A job read as outside the US
(`no`) does not count; `yes` and `unknown` do, as on the job list (`us <> 'no'`), so a remote or unplaced role is not
missed. A list answer has no body, so
the level, years and sponsorship checks are not run: the gate asks only whether a US engineering role is open.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from outreach.crawl.base import ReadResult
from outreach.rules.config import Config
from outreach.rules.location import us_status
from outreach.rules.text import clean_text
from outreach.rules.title import classify_title

__all__ = ["SEED_ATS", "RETRY_DAYS", "Gate", "gate"]

SEED_ATS = ("greenhouse", "ashby")
# A board that failed its gate (no US engineering role, or dead) is read again after this many days.
RETRY_DAYS = 30


@dataclass(frozen=True)
class Gate:
    result: Literal["promoted", "no_us_engineering", "dead", "error"]
    open_us_engineering: int
    board_name: str


def gate(result: ReadResult, rules_cfg: Config) -> Gate:
    """The verdict on one list answer. `error` (and `unchanged`, which a first read never gets) stays `error`: it is
    tried again on the next run and never counts as a rejection. `empty` is `dead`."""
    if result.result == "empty":
        return Gate("dead", 0, "")
    if result.result != "ok":
        return Gate("error", 0, "")
    count = sum(1 for job in result.jobs if _is_us_engineering(job.title, job.location, job.country, rules_cfg))
    return Gate("promoted" if count else "no_us_engineering", count, result.board_name)


def _is_us_engineering(title: str, location: str, country: str | None, cfg: Config) -> bool:
    title, location = clean_text(title), clean_text(location)
    country = None if country is None else clean_text(country)
    return classify_title(title, cfg).lane == "engineering" and us_status(location, country, cfg, title) != "no"
