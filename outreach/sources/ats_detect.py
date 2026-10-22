"""ATS detection (#31 Task 10): the boards a careers page or any text points at.

Patterns are ported from the careers probe in the research scratch, each with a named `token` group. A match whose
token fails the board grammar (`valid_token`) is skipped, never repaired: a guess must not become a board row. So is a
match that a non-ASCII letter cuts short (`boards.greenhouse.io/café` is not the board `caf`).

Known misses, left out on purpose: Workday sites on `myworkdaysite.com`, and Oracle pages with no `/sites/<site>` in
the path (the site number is not in the URL, so there is no token).
"""
from __future__ import annotations

import re

from outreach.sources.boards import BoardRef

__all__ = ["detect_all", "detect_ats", "detect_unsupported", "MAX_BOARDS"]

MAX_BOARDS = 3
# Never a token, whatever the ATS.
_GENERIC = frozenset("embed js api www jobs careers static assets v1 widget".split())
# Vendor hosts: a first label that is the ATS's own site, not a customer's board.
_VENDOR_HOSTS = frozenset("docs help support app login community info go developers blog status marketplace "
                          "partners learn".split()) | _GENERIC
# Path segments that are the ATS's own app, by ATS.
_APP_PATHS = {"smartrecruiters": frozenset({"oneclick-ui", "ni"}), "workable": frozenset({"j"})}
_WORKDAY_NOT_SITES = frozenset("wday login logout assets static api js css images authgwy favicon".split())

_LOCALE = re.compile(r"[a-z]{2}(?:-[a-z]{2})?", re.I)  # xx or xx-XX
_LEFT = r"(?<![\w-])"
_SLUG = r"(?P<token>[\w.%-]+)"  # trailing dots (the end of a sentence) are stripped after the match
_SUB = _LEFT + r"(?P<token>[\w-]+)"
_HOST_END = r"(?![\w-]|\.\w)"  # the host ends here: not `.com.evil.com`, not `.comx`

# (ats, pattern, the ATS's vendor skip set). All run with re.I and re.A.
_PATTERNS = [
    ("greenhouse", _LEFT + r"(?:(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_(?:board|app)(?:/js)?\?for=)?"
     r"|boards-api\.greenhouse\.io/v1/boards/)" + _SLUG, frozenset()),
    ("lever", _LEFT + r"(?:jobs(?:\.eu)?\.lever\.co/|api(?:\.eu)?\.lever\.co/v0/postings/)" + _SLUG, frozenset()),
    ("ashby", _LEFT + r"jobs\.ashbyhq\.com/" + _SLUG, frozenset()),
    ("workable", _LEFT + r"apply\.workable\.com/" + _SLUG, _APP_PATHS["workable"]),
    ("smartrecruiters", _LEFT + r"(?:careers|jobs)\.smartrecruiters\.com/" + _SLUG, _APP_PATHS["smartrecruiters"]),
    ("rippling", _LEFT + r"ats\.rippling\.com/(?:" + _LOCALE.pattern + r"/)?" + _SLUG, frozenset()),
    ("gem", _LEFT + r"jobs\.gem\.com/" + _SLUG, frozenset()),
    ("bamboohr", _SUB + r"\.bamboohr\.com" + _HOST_END, _VENDOR_HOSTS),
    ("jazzhr", _SUB + r"\.applytojob\.com" + _HOST_END, _VENDOR_HOSTS),
    ("breezy", _SUB + r"\.breezy\.hr" + _HOST_END, _VENDOR_HOSTS),
    ("recruitee", _SUB + r"\.recruitee\.com" + _HOST_END, _VENDOR_HOSTS),
    ("teamtailor", _SUB + r"\.teamtailor\.com" + _HOST_END, _VENDOR_HOSTS),
    ("pinpoint", _SUB + r"\.pinpointhq\.com" + _HOST_END, _VENDOR_HOSTS),
]
_COMPILED = [(ats, re.compile(pattern, re.I | re.A), skip) for ats, pattern, skip in _PATTERNS]

_WORKDAY = re.compile(
    _LEFT + r"(?P<tenant>[a-z0-9-]+)\.(?P<wd>wd[0-9]+)\.myworkdayjobs\.com/(?:wday/cxs/[a-z0-9_-]+/)?"
    r"(?:" + _LOCALE.pattern + r"/)?(?P<site>[A-Za-z0-9_-]+)(?!\.?[\w-])", re.I | re.A)
_ORACLE = re.compile(
    _LEFT + r"(?P<host>[a-z0-9][a-z0-9.-]*\.oraclecloud\.com)/hcmUI/CandidateExperience/[\w-]+/sites/"
    r"(?P<site>[A-Za-z0-9_-]+)(?!\.?[\w-])", re.I | re.A)
_ICIMS = re.compile(_LEFT + r"(?P<host>(?:[a-z0-9-]+\.)*(?P<name>[a-z0-9-]+)\.icims\.com)" + _HOST_END, re.I | re.A)

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


def _cut_by_non_ascii(text: str, start: int, end: int) -> bool:
    """Whether a non-ASCII letter or digit touches the span: the match is part of a longer word, not a token."""
    for i in (start - 1, end):
        if 0 <= i < len(text) and ord(text[i]) > 127 and (text[i].isalnum() or text[i] == "_"):
            return True
    return False


def _try(ats: str, token: str) -> BoardRef | None:
    try:
        return BoardRef(ats, token)
    except ValueError:
        return None


def _matches(text: str):
    """(position, BoardRef) for every board named in the text, in no particular order."""
    for ats, pattern, skip in _COMPILED:
        for m in pattern.finditer(text):
            token = m.group("token").rstrip(".").lower()
            if token in _GENERIC or token in skip or (ats == "rippling" and _LOCALE.fullmatch(token)):
                continue
            if _cut_by_non_ascii(text, m.start("token"), m.start("token") + len(m.group("token"))):
                continue
            ref = _try(ats, token)
            if ref:
                yield m.start(), ref
    for m in _WORKDAY.finditer(text):
        site = m.group("site")
        if site.lower() in _WORKDAY_NOT_SITES or _LOCALE.fullmatch(site) or _cut_by_non_ascii(text, m.start(), m.end()):
            continue
        ref = _try("workday", f"{m.group('tenant')}.{m.group('wd')}/{site}")
        if ref:
            yield m.start(), ref
    for m in _ORACLE.finditer(text):
        if _cut_by_non_ascii(text, m.start(), m.end()):
            continue
        ref = _try("oracle_orc", f"{m.group('host')}/{m.group('site')}")
        if ref:
            yield m.start(), ref
    for m in _ICIMS.finditer(text):
        if m.group("name").lower() in _VENDOR_HOSTS or _cut_by_non_ascii(text, m.start(), m.end()):
            continue
        ref = _try("icims", m.group("host"))
        if ref:
            yield m.start(), ref


def detect_all(text: str) -> list[BoardRef]:
    """Every distinct board the text names, in first-seen order. JSON-escaped slashes (`https:\\/\\/`) are read as
    slashes."""
    text = text.replace("\\/", "/")
    found: list[BoardRef] = []
    for _, ref in sorted(_matches(text), key=lambda item: item[0]):
        if ref not in found:
            found.append(ref)
    return found


def detect_ats(text: str) -> list[BoardRef]:
    """The first `MAX_BOARDS` of `detect_all`."""
    return detect_all(text)[:MAX_BOARDS]


def detect_unsupported(text: str) -> list[str]:
    """The platform host names the text links to that have no board reader (Jobvite, Paylocity, ADP, UKG, Notion,
    a LinkedIn jobs page), in first-seen order without duplicates. Never a board."""
    text = text.replace("\\/", "/")
    seen: list[tuple[int, str]] = []
    for host, pattern in _UNSUPPORTED:
        m = pattern.search(text)
        if m:
            seen.append((m.start(), host))
    return [host for _, host in sorted(seen)]
