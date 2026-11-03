"""One parser for job links: which board and which job a URL names (#153).

`board_key` reads a link into the crawler's own terms (`ats`, board `token`, `native_id`), so a listing from an outside
feed or a link Devanshu pasted can be matched to a stored posting. `posting_key` turns a full key into the crawler's
`posting.source_key`, lower-cased for comparison. Pure functions on text: nothing here touches the network or a store.
"""
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from outreach.crawl.postings import source_key
from outreach.sources.boards import normalize_token

__all__ = ["BoardKey", "board_key", "posting_key"]


@dataclass(frozen=True)
class BoardKey:
    """The board a link names and, when it names one, the job: the crawler's `ats`, normalized board token and the
    ATS's own id for the posting. `token` is None for a bare `gh_jid` link on a company site."""
    ats: str
    token: str | None
    native_id: str | None


_LOCALE = re.compile(r"[a-z]{2}(?:-[a-z]{2})?", re.IGNORECASE)  # en, en-US: a language segment before the real path
_DIGITS = re.compile(r"[0-9]+")
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")
_GREENHOUSE_HOST = re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io")
_LEVER_HOST = re.compile(r"jobs(?:\.eu)?\.lever\.co")
_WORKDAY_HOST = re.compile(r"(?P<tenant>[a-z0-9-]+)\.(?P<wd>wd[0-9]+)\.myworkday(?:jobs|site)\.com")
_WORKDAY_SITE_HOST = re.compile(r"(?P<wd>wd[0-9]+)\.myworkdaysite\.com")
_ICIMS_HOST = re.compile(r"[a-z0-9.-]+\.icims\.com")
_JAZZHR_HOST = re.compile(r"(?P<token>[a-z0-9][a-z0-9-]*)\.applytojob\.com")


def _segments(path: str) -> list[str]:
    return [p for p in path.split("/") if p]


def _skip_locale(segments: list[str]) -> list[str]:
    return segments[1:] if segments and _LOCALE.fullmatch(segments[0]) else segments


def _key(ats: str, token: str | None, native_id: str | None) -> BoardKey | None:
    """The key with its token normalized as a board row stores it; None when the token is not a valid one."""
    if token is not None:
        try:
            token = normalize_token(ats, token)
        except ValueError:
            return None
    if native_id is not None and not native_id.strip():
        native_id = None
    return BoardKey(ats, token, native_id)


def _id(segments: list[str], at: int, pattern: re.Pattern = _SAFE_ID) -> str | None:
    return segments[at] if len(segments) > at and pattern.fullmatch(segments[at]) else None


def _greenhouse(segments: list[str], query: dict[str, list[str]]) -> BoardKey | None:
    if segments[:1] == ["embed"]:
        token = (query.get("for") or [""])[0]
        job = (query.get("token") or [""])[0]
        if not token:
            return None
        return _key("greenhouse", token, job if _DIGITS.fullmatch(job) else None)
    if not segments:
        return None
    return _key("greenhouse", segments[0],
                _id(segments, 2, _DIGITS) if segments[1:2] == ["jobs"] else None)


def _workday(host: Mapping[str, str], segments: list[str]) -> BoardKey | None:
    segments = _skip_locale(segments)
    if not segments:
        return None
    tenant, wd = host["tenant"], host["wd"]
    if segments[0] == "recruiting" and len(segments) > 2:  # wdN.myworkdaysite.com/recruiting/<tenant>/<site>
        tenant, segments = segments[1], segments[2:]
    site, rest = segments[0], segments[1:]
    if rest[:1] != ["job"] or len(rest) < 2:
        return _key("workday", f"{tenant}.{wd}/{site}", None)
    if rest[-1] == "apply" and len(rest) > 2:
        rest = rest[:-1]
    return _key("workday", f"{tenant}.{wd}/{site}", "/" + "/".join(rest))


def _jazzhr(token: str, segments: list[str]) -> BoardKey | None:
    # The crawler's own link is /apply/jobs/details/<id>; the apply button's is /apply/<id>/<slug>.
    if segments[:3] == ["apply", "jobs", "details"]:
        return _key("jazzhr", token, _id(segments, 3))
    if segments[:2] == ["apply", "jobs"] or segments[:1] != ["apply"]:
        return _key("jazzhr", token, None)
    return _key("jazzhr", token, _id(segments, 1))


def board_key(url: str) -> BoardKey | None:
    """The board and job a link names, or None when the site is not one Knock reads.

    Any host with a numeric `gh_jid` query is a Greenhouse posting with no known board. Locale segments (`en-US`) are
    skipped. A link to a board with no job gives a key with `native_id` None.
    """
    if not isinstance(url, str) or not url.strip():
        return None
    text = url.strip()
    if "//" not in text and re.match(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:[/?#]|$)", text):
        text = "https://" + text
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    segments, query = _segments(parts.path), parse_qs(parts.query)
    found: BoardKey | None
    if _GREENHOUSE_HOST.fullmatch(host):
        found = _greenhouse(segments, query)
    elif host == "jobs.ashbyhq.com":
        found = _key("ashby", segments[0], _id(segments, 1)) if segments else None
    elif _LEVER_HOST.fullmatch(host):
        found = _key("lever", segments[0], _id(segments, 1)) if segments else None
    elif host == "apply.workable.com":
        segments = _skip_locale(segments)
        found = _key("workable", segments[0], _id(segments, 2) if segments[1:2] == ["j"] else None) if segments else None
    elif host == "ats.rippling.com":
        segments = _skip_locale(segments)
        found = _key("rippling", segments[0], _id(segments, 2) if segments[1:2] == ["jobs"] else None) if segments else None
    elif _ICIMS_HOST.fullmatch(host):
        found = _key("icims", host, _id(segments, 1, _DIGITS) if segments[:1] == ["jobs"] else None)
    elif m := _JAZZHR_HOST.fullmatch(host):
        found = _jazzhr(m["token"], segments)
    elif m := _WORKDAY_HOST.fullmatch(host):
        found = _workday(m, segments)
    elif m := _WORKDAY_SITE_HOST.fullmatch(host):
        found = _workday({"tenant": "", "wd": m["wd"]}, segments)
    else:
        found = None
    if found is not None:
        return found
    jid = (query.get("gh_jid") or [""])[0]
    return BoardKey("greenhouse", None, jid) if _DIGITS.fullmatch(jid) else None


def posting_key(key: BoardKey) -> str | None:
    """The crawler's `posting.source_key` for the job, lower-cased for comparison, or None when the token or the
    native id is not known."""
    if not isinstance(key, BoardKey) or not key.token or not key.native_id:
        return None
    return source_key(key.ats, key.token, key.native_id).lower()
