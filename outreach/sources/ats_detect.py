"""ATS detection (#31 Task 10): the boards a careers page or any text points at.

Patterns are ported from the careers probe in the research scratch, each with a named `token` group. A match whose
token fails the board grammar (`valid_token`) is skipped, never repaired: a guess must not become a board row.
"""
from __future__ import annotations

import re

from outreach.sources.boards import BoardRef

__all__ = ["detect_ats", "detect_unsupported", "MAX_BOARDS"]

MAX_BOARDS = 3
_GENERIC = frozenset("embed js api www jobs careers static assets v1 widget".split())

_SLUG = r"(?P<token>[\w.%-]+)"  # trailing dots (the end of a sentence) are stripped after the match
_SUB = r"(?P<token>[\w-]+)"
_LOCALE = r"[a-z]{2}-[A-Za-z]{2}"

# (ats, pattern). All run with re.I and re.A, so a host in any case matches; the path parts that keep their spelling
# (a Workday site, an Oracle site number) are read from the original text.
_PATTERNS = [
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?" + _SLUG),
    ("lever", r"jobs(?:\.eu)?\.lever\.co/" + _SLUG),
    ("ashby", r"jobs\.ashbyhq\.com/" + _SLUG),
    ("workable", r"apply\.workable\.com/" + _SLUG),
    ("smartrecruiters", r"(?:careers|jobs)\.smartrecruiters\.com/" + _SLUG),
    ("rippling", r"ats\.rippling\.com/" + _SLUG),
    ("gem", r"jobs\.gem\.com/" + _SLUG),
    ("bamboohr", _SUB + r"\.bamboohr\.com"),
    ("jazzhr", _SUB + r"\.applytojob\.com"),
    ("breezy", _SUB + r"\.breezy\.hr"),
    ("recruitee", _SUB + r"\.recruitee\.com"),
    ("teamtailor", _SUB + r"\.teamtailor\.com"),
    ("pinpoint", _SUB + r"\.pinpointhq\.com"),
]
_COMPILED = [(ats, re.compile(pattern, re.I | re.A)) for ats, pattern in _PATTERNS]

_WORKDAY = re.compile(
    r"(?P<tenant>[a-z0-9-]+)\.(?P<wd>wd[0-9]+)\.myworkdayjobs\.com/(?:wday/cxs/[a-z0-9_-]+/)?"
    r"(?:" + _LOCALE + r"/)?(?P<site>[A-Za-z0-9_-]+)", re.I | re.A)
_ORACLE = re.compile(
    r"(?P<host>[a-z0-9][a-z0-9.-]*\.oraclecloud\.com)/hcmUI/CandidateExperience/[\w-]+/sites/(?P<site>[A-Za-z0-9_-]+)",
    re.I | re.A)
_ICIMS = re.compile(r"(?P<host>(?P<name>[a-z0-9][a-z0-9-]*)\.icims\.com)", re.I | re.A)

# (host name reported, pattern). Hosts that hold jobs but have no board reader here (the board.ats set has no entry).
_UNSUPPORTED = [(host, re.compile(pattern, re.I | re.A)) for host, pattern in [
    ("jobs.jobvite.com", r"jobs\.jobvite\.com/"),
    ("recruiting.paylocity.com", r"recruiting\.paylocity\.com"),
    ("workforcenow.adp.com", r"workforcenow\.adp\.com"),
    ("ultipro.com", r"[a-z0-9-]+\.ultipro\.com"),
    ("ukg.net", r"[a-z0-9-]+\.ukg\.net"),
    ("notion.site", r"[a-z0-9-]+\.notion\.site"),
    ("linkedin.com", r"linkedin\.com/company/[\w-]+/jobs"),
]]


def _slug_ref(ats: str, token: str) -> BoardRef | None:
    token = token.rstrip(".").lower()
    if token in _GENERIC:
        return None
    return _try(ats, token)


def _try(ats: str, token: str) -> BoardRef | None:
    try:
        return BoardRef(ats, token)
    except ValueError:
        return None


def _matches(text: str):
    """(position, BoardRef) for every board named in the text, in no particular order."""
    for ats, pattern in _COMPILED:
        for m in pattern.finditer(text):
            ref = _slug_ref(ats, m.group("token"))
            if ref:
                yield m.start(), ref
    for m in _WORKDAY.finditer(text):
        site = m.group("site")
        if site.lower() == "wday" or re.fullmatch(_LOCALE, site):
            continue
        ref = _try("workday", f"{m.group('tenant')}.{m.group('wd')}/{site}")
        if ref:
            yield m.start(), ref
    for m in _ORACLE.finditer(text):
        ref = _try("oracle_orc", f"{m.group('host')}/{m.group('site')}")
        if ref:
            yield m.start(), ref
    for m in _ICIMS.finditer(text):
        if m.group("name").lower() not in _GENERIC:
            ref = _try("icims", m.group("host"))
            if ref:
                yield m.start(), ref


def detect_ats(text: str) -> list[BoardRef]:
    """The boards the text names, in first-seen order, without duplicates, at most `MAX_BOARDS`."""
    found: list[BoardRef] = []
    for _, ref in sorted(_matches(text), key=lambda item: item[0]):
        if ref not in found:
            found.append(ref)
    return found[:MAX_BOARDS]


def detect_unsupported(text: str) -> list[str]:
    """The platform host names the text links to that have no board reader (Jobvite, Paylocity, ADP, UKG, Notion,
    a LinkedIn jobs page), in first-seen order without duplicates. Never a board."""
    seen: list[tuple[int, str]] = []
    for host, pattern in _UNSUPPORTED:
        m = pattern.search(text)
        if m:
            seen.append((m.start(), host))
    return [host for _, host in sorted(seen)]
