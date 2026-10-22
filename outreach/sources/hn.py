"""Hacker News "Ask HN: Who is hiring?" threads as a company source (#31 Task 12).

One Algolia search lists the whoishiring threads; one item call per wanted thread returns its comments. Each
top-level comment is a post: its header (the text before the first paragraph break) names the company, a stage and a
place, and its links give a domain and an ATS board. A post with a non-US place and no US marker is dropped and
counted. Every kept post is also a `PostRecord`, so the discover job can store it in `company_post`.

Identity stays safe by construction. The `hq` evidence is a `guess`, which identity never turns into a state. The
domain is taken from the header's links (or from a body link whose label is the company's name), reduced to its
registrable domain the way the LCA source reduces a mailbox domain, and a platform (a host, a shortener, a form, a
press or job site) is never one.

`source_ref` is `hn:<name key>@<domain>` when a post gives a company domain, else `hn:<name key>`; a name with any
non-ASCII letter uses `sha256:<16 hex>` of its NFC form for the key (the ASCII key would drop the letter and make
"Muller" and "Mller" one company). The domain is in the ref so two companies that share a name stay two. Within one
pass, a post with no domain joins the one domain candidate of its name; with several domain candidates it joins none
(counted in `name_conflicts`, and it has no `PostRecord`). Across passes there is no such join: a repeat in a later
month that leaves the link out is a name-only candidate `hn:<name key>`, which identity can only match weakly (a new
company plus an item for him to merge). That cost is accepted: guessing which company a domain-less post means would
be a silent merge.

Error is not empty: an Algolia error, a non-200 answer, a body that is not the expected JSON, a month with no
thread, or a thread with no readable comments raises `HnError`. A month whose comments hold no parsable post is a
real answer (it is counted in `summary()`), not an error.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import tomllib
import unicodedata
import urllib.parse
from dataclasses import dataclass
from functools import cache
from html.parser import HTMLParser
from pathlib import Path

from outreach.identity import US_STATES
from outreach.net import HttpClient, UrllibClient
from outreach.rules.facts import EvidenceDraft
from outreach.sources.ats_detect import detect_all
from outreach.sources.base import Candidate
from outreach.sources.boards import SUPPORTED_ATS
from outreach.sources.capabilities import PostRecord
from outreach.sources.keys import classify_domain, registrable_domain
from outreach.store import iso_utc, name_key

__all__ = ["HnError", "HnThreadNotOut", "HnConfig", "HnSource", "Post", "load_hn_config", "parse_post", "is_us", "plain_text",
           "SEARCH_URL"]

CONFIG_PATH = Path(__file__).with_name("hn.toml")
SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=20"
ITEM_URL = "https://hn.algolia.com/api/v1/items/{}"
POST_URL = "https://news.ycombinator.com/item?id={}"
PAGE_SIZE = 20  # hitsPerPage in SEARCH_URL
QUOTE_CAP = 300

_MONTHS = {m: i for i, m in enumerate(
    "january february march april may june july august september october november december".split(), 1)}
_HIRING_TITLE = re.compile(r"^\s*Ask HN: Who is hiring\?\s*\((\w+)\s+(\d{4})\)\s*$", re.I)
_ANY_MONTH = re.compile(r"\((\w+)\s+(\d{4})\)\s*$")
_SINCE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
# Spaced separators only: a hyphen with no spaces is part of a name. The dash characters are built, not typed.
_SEP = re.compile(r"\s*\|\s*|\s+[" + chr(0x2014) + chr(0x2013) + r"-]\s+")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_IS_HIRING = re.compile(r"\s+is\s+hiring\W*$", re.I)
_AT_WORD = re.compile(r"^(.+?)\s+at\s+(.+)$", re.I)
_PAREN = re.compile(r"\([^)]*\)")
_NAME_BAD = re.compile(r"[:?!]")
_SERIES = re.compile(r"\bseries\s+([a-z])\b", re.I)
_SERIES_STAGE = {"a": "series_a", "b": "series_b", "c": "series_c", "d": "series_d"}


class HnError(Exception):
    """The HN source could not give an answer: a bad HTTP status, a body that is not the expected JSON, a month with no
    thread, or a thread with nothing in it. The message names the URL and what was wrong. (A transport failure is the
    client's `FetchError` and passes through unchanged.)"""


class HnThreadNotOut(HnError):
    """No "Who is hiring?" thread from the month asked for (`since`) on: it is not posted yet, or `since` is in the
    future."""


@dataclass(frozen=True)
class HnConfig:
    max_name_words: int
    non_us_places: re.Pattern
    non_us_codes: re.Pattern
    us_markers: re.Pattern  # US codes and city codes, case as written
    us_phrases: re.Pattern  # case blind
    us_places: re.Pattern
    us_city_codes: re.Pattern
    state_code: re.Pattern  # ", XX" for every state code
    ambiguous_state_codes: frozenset[str]
    reject_names: frozenset[str]
    generic_segments: frozenset[str]
    role_words: frozenset[str]
    role_vocabulary: frozenset[str]  # role words and the words that only describe a role
    asks_email: re.Pattern
    digest: str  # the first 8 hex digits of sha256 over the settings: any edit changes the source version


_REQUIRED = ("max_name_words", "non_us_places", "non_us_codes", "us_codes", "us_city_codes", "us_phrases", "us_places",
             "ambiguous_state_codes", "reject_names", "generic_segments", "role_words", "role_modifiers",
             "email_phrases", "email_phrases_with_address")
_OBFUSCATED_TLD = "com|io|ai|org|net|co|dev|app|xyz|tech|me|us|so|sh"
_AT = r"(?:\s*[\[(]at[\])]\s*|\s+at\s+)"
_DOT = r"(?:\s*[\[(]dot[\])]\s*|\s+dot\s+)"
_ADDRESS = (r"(?:[\w.+-]+@[\w-]+(?:\.[\w-]+)+"  # jobs@acme.io
            r"|(?<![\w.@])[\w.+-]+" + _AT + r"[\w-]+(?:" + _DOT + r"[\w-]+)*" + _DOT + r"(?:" + _OBFUSCATED_TLD +
            r")(?!\w))")  # jobs at acme dot com, jobs [at] acme [dot] io


def _phrase(phrase: str) -> str:
    """A phrase as a whole-word pattern: spaces are any whitespace, and `email` also matches `e-mail`."""
    parts = [r"e-?mail" if w == "email" else re.escape(w) for w in phrase.lower().split()]
    return r"(?<!\w)" + r"\s+".join(parts) + r"(?!\w)"


def _asks_email_pattern(plain: list[str], with_address: list[str]) -> re.Pattern:
    alternatives = [_ADDRESS, *(_phrase(p) for p in plain), *(_phrase(p) + r"\s*[:,]?\s*" + _ADDRESS
                                                              for p in with_address)]
    return re.compile("|".join(alternatives), re.I)


def _norm(text: str) -> str:
    """Case blind, typographic apostrophes plain, punctuation other than hyphens and apostrophes dropped."""
    text = text.lower().replace(chr(0x2019), "'")
    return _squash(re.sub(r"[^a-z0-9&'\s-]", " ", text))


def _words(words, flags=0) -> re.Pattern:
    """Whole words or phrases: no word character touches either end."""
    body = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(r"(?<!\w)(?:" + body + r")(?!\w)", flags)


def load_hn_config(path: Path | None = None) -> HnConfig:
    """Read `hn.toml` (or `path`). A missing key raises KeyError naming it, so a typo cannot turn a rule off."""
    path = Path(path) if path else CONFIG_PATH
    data = tomllib.loads(path.read_bytes().decode("utf-8"))
    for key in _REQUIRED:
        if key not in data:
            raise KeyError(f"{key} is missing from {path.name}")
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()[:8]
    states = "|".join(sorted(US_STATES))
    return HnConfig(
        max_name_words=int(data["max_name_words"]),
        non_us_places=_words(data["non_us_places"], re.I), non_us_codes=_words(data["non_us_codes"]),
        us_markers=_words([*data["us_codes"], *data["us_city_codes"]]),
        us_phrases=_words(data["us_phrases"], re.I),
        us_places=_words(data["us_places"], re.I), us_city_codes=_words(data["us_city_codes"]),
        state_code=re.compile(r",\s*(" + states + r")(?![A-Za-z])"),
        ambiguous_state_codes=frozenset(data["ambiguous_state_codes"]),
        reject_names=frozenset(_norm(n) for n in data["reject_names"]),
        generic_segments=frozenset(_norm(n) for n in data["generic_segments"]),
        role_words=frozenset(_norm(w) for w in data["role_words"]),
        role_vocabulary=frozenset(_norm(w) for w in [*data["role_words"], *data["role_modifiers"]]),
        asks_email=_asks_email_pattern(data["email_phrases"], data["email_phrases_with_address"]), digest=digest)


@cache
def _default_config() -> HnConfig:
    return load_hn_config()


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ---- HTML ------------------------------------------------------------------------------------------------------


class _Blocks(HTMLParser):
    """Splits a comment into blocks at every `<p>` and `<br>`: each block has its text, its hrefs and the separator
    that precedes it in the plain text (a blank line for `<p>`, a newline for `<br>`)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = [{"sep": "", "text": [], "links": []}]

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "br"):
            self.blocks.append({"sep": "\n\n" if tag == "p" else "\n", "text": [], "links": []})
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.blocks[-1]["links"].append(href.strip())

    def handle_data(self, data):
        self.blocks[-1]["text"].append(data)


def _blocks(html: str) -> list[dict]:
    parser = _Blocks()
    parser.feed(html or "")
    parser.close()
    out = []
    for b in parser.blocks:
        out.append({"sep": b["sep"], "text": re.sub(r"[ \t\r\f\v]+", " ", "".join(b["text"])).strip(),
                    "links": tuple(b["links"])})
    return out


def plain_text(html: str) -> str:
    """The comment as plain text: tags gone, entities decoded, a blank line between paragraphs. Link text is kept as
    shown (HN shortens long URLs there); use the hrefs for the real addresses."""
    out = ""
    for b in _blocks(html):
        if not b["text"]:
            continue
        out += (b["sep"] if out else "") + b["text"]
    return out


# ---- one post --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Post:
    """One parsed comment. `links` are the header's hrefs, `more_links` the rest of the comment's. `name_index` is
    the header segment the name came from (0 unless the first segment was a title, a place or a remark)."""
    name: str
    header: str
    links: tuple[str, ...]
    stage: str | None
    location: str | None
    more_links: tuple[str, ...] = ()
    name_index: int = 0


def _stage(parens: list[str]) -> str | None:
    """The stage a parenthetical names: a Series round beats Seed, which beats YC (`YC W20, Series B` is series_b)."""
    text = " ".join(parens)
    series = _SERIES.findall(text)
    if series:
        return _SERIES_STAGE.get(max(series, key=str.lower).lower(), "series_e_plus")
    if re.search(r"\bpre[- ]?seed\b", text, re.I):
        return "pre_seed"
    if re.search(r"\bseed\b", text, re.I):
        return "seed"
    if re.search(r"\byc\b|\by combinator\b", text, re.I):
        return "yc"
    return None


def _is_place(text: str, cfg: HnConfig) -> bool:
    """The whole text is one place or region from the lists (`NYC`, `London`, `United States`)."""
    return any(p.fullmatch(text) for p in (cfg.non_us_places, cfg.non_us_codes, cfg.us_places, cfg.us_city_codes,
                                           cfg.us_markers, cfg.us_phrases))


def _words_of(text: str) -> list[str]:
    return [w for w in _norm(text).split() if w]


def _role_ish(text: str, cfg: HnConfig) -> bool:
    """Only role words and words that describe one (`Senior Software Engineer`, `Backend`, `Head of Design`)."""
    words = _words_of(text)
    return bool(words) and all(w in cfg.role_vocabulary or w.rstrip("s") in cfg.role_vocabulary for w in words)


def _has_role_word(text: str, cfg: HnConfig) -> bool:
    return any(w in cfg.role_words or w.rstrip("s") in cfg.role_words for w in _words_of(text))


def _segment_name(seg: str, cfg: HnConfig) -> tuple[str | None, str | None, str, bool]:
    """(name, stage, status, is_hiring) for one header segment. Status `ok` (a name), `weak` (a title, a remark or
    a place: try a later segment) or `bad` (empty, too long or has `:`, `?`, `!`). The name has its parentheticals
    and any URL removed, a trailing `is hiring` dropped and, for `<title> at <company>`, only the company kept."""
    stage = _stage(re.findall(r"\(([^)]*)\)", seg))
    text = _squash(re.sub(r"[()]", "", _URL.sub(" ", _PAREN.sub(" ", seg)))).strip(" ,;")
    stripped = _IS_HIRING.sub("", text)
    hiring = stripped != text
    text = stripped.strip(" ,;")
    at = _AT_WORD.match(text)
    if at and _has_role_word(at.group(1), cfg):
        text = at.group(2).strip(" ,;")
    if not text or len(text.split()) > cfg.max_name_words or _NAME_BAD.search(text):
        return None, stage, "bad", hiring
    if _norm(text) in cfg.reject_names or _is_place(text, cfg) or _role_ish(text, cfg):
        return None, stage, "weak", hiring
    return text, stage, "ok", hiring


def _later_name(seg: str, cfg: HnConfig) -> tuple[str | None, str | None]:
    """A name from a segment after a title or remark: it must look like one (a capital first, no comma, not a
    generic word)."""
    name, stage, status, _ = _segment_name(seg, cfg)
    if status != "ok" or "," in name or not name[0].isupper() or _norm(name) in cfg.generic_segments:
        return None, None
    return name, stage


def _location(segments: list[str], cfg: HnConfig) -> str | None:
    """The first segment that holds a city, state code or country. 'Remote' and 'Remote (US)' alone are not one."""
    for seg in segments:
        if (cfg.non_us_places.search(seg) or cfg.us_places.search(seg) or cfg.us_city_codes.search(seg)
                or cfg.state_code.search(seg)):
            return seg[:80]
    return None


def parse_post(html: str, config: HnConfig | None = None) -> Post | None:
    """The post in a comment's HTML, or None when its header is not a company header. The header is the text before
    the first paragraph break; the name is its first segment before `|`, a spaced em dash, en dash or hyphen.

    When that segment is a title (`Senior Software Engineer`), a remark or a bare place (`Remote`, `NYC`), the name is
    the first later segment that looks like one, else the post is unparsed. A header of one segment needs a second
    signal (a parenthetical, a link, or `is hiring`): `Thanks A Lot` and `Looking For Work` are title case too.
    """
    cfg = config or _default_config()
    blocks = _blocks(html)
    header = blocks[0]["text"]
    if not header:
        return None
    segments = [s.strip() for s in _SEP.split(header)]
    name, stage, status, hiring = _segment_name(segments[0], cfg)
    index = 0
    if status == "weak":
        for i, seg in enumerate(segments[1:], 1):
            name, stage = _later_name(seg, cfg)
            if name:
                index = i
                break
    if name is None:
        return None
    if len(segments) == 1 and "(" not in header and not blocks[0]["links"] and not hiring:
        return None
    return Post(name=name, header=header, links=blocks[0]["links"], stage=stage,
                location=_location(segments[index + 1:], cfg),
                more_links=tuple(link for b in blocks[1:] for link in b["links"]), name_index=index)


def _follows_non_us_place(text: str, pos: int, cfg: HnConfig) -> bool:
    before = text[:pos].rstrip()
    return any(m.end() == len(before) for m in cfg.non_us_places.finditer(before))


def is_us(header: str, config: HnConfig | None = None, *, name_index: int = 0) -> bool:
    """False only when a segment of the header, other than the name's, names a non-US place and no segment has a US
    marker. A header with no other segment, or none that names a place, keeps the post: unknown is never grounds to
    hide. `Canada Drives | Eng` is a US-neutral post (the name is not a location).

    A state code that also reads as a country code (`CA`, `DE`, `IN`...) is a US marker unless it comes straight
    after a non-US place (`Toronto, CA`, `Berlin, DE`); `Culver City, CA | London, UK` and `Irvine, CA or London` stay.
    """
    cfg = config or _default_config()
    segments = _SEP.split(_URL.sub(" ", header))
    non_us = us = False
    for i, seg in enumerate(segments):
        if i == name_index:
            continue
        non_us = non_us or bool(cfg.non_us_places.search(seg) or cfg.non_us_codes.search(seg))
        us = us or bool(cfg.us_markers.search(seg) or cfg.us_phrases.search(seg) or cfg.us_places.search(seg))
        for m in cfg.state_code.finditer(seg):
            if not (m.group(1) in cfg.ambiguous_state_codes and _follows_non_us_place(seg, m.start(), cfg)):
                us = True
    return us or not non_us


def _domain_of(url: str) -> str | None:
    """The registrable company domain of a link, or None for a mail link, an IP address, or a platform (a code host,
    a social site, a job board, a shortener, a form, a press site, hosting such as vercel.app)."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or host.rsplit(".", 1)[-1].isdigit():
        return None
    domain = registrable_domain(host)  # lower case, no www., the same reduction the LCA source applies
    return domain if domain and classify_domain(domain) == "company" else None


def _post_domain(post: Post) -> str | None:
    for link in post.links:
        d = _domain_of(link)
        if d:
            return d
    key = name_key(post.name)
    if len(key) < 4:
        return None
    for link in post.more_links:  # a body link counts only when its label is the company's (not a press link)
        d = _domain_of(link)
        label = re.sub(r"[^a-z0-9]", "", d.split(".")[0]) if d else ""
        if label and (label == key or label.startswith(key) or (len(label) >= 5 and key.startswith(label))):
            return d
    return None


def _ref_key(name: str) -> str:
    """The name part of `source_ref`: the name key, or for a name with a non-ASCII letter `sha256:` and 16 hex digits of
    the NFC, case-folded name (the name key would drop the letter, so `Muller` and `Mller` would collide)."""
    nfc = unicodedata.normalize("NFC", name)
    if any(ord(ch) > 127 and ch.isalpha() for ch in nfc):
        return "sha256:" + hashlib.sha256(_squash(nfc).casefold().encode("utf-8")).hexdigest()[:16]
    return name_key(nfc)


def _asks_email(text: str, cfg: HnConfig) -> bool:
    return bool(cfg.asks_email.search(text))


# ---- the source ------------------------------------------------------------------------------------------------


@dataclass
class _Kept:
    post: Post
    comment_id: int
    url: str
    posted_at: str | None
    text: str
    domain: str | None
    boards: list


@dataclass
class _Pass:
    candidates: list[Candidate]
    posts: list[PostRecord]
    summary: dict[str, int]


class HnSource:
    """Companies that posted in "Who is hiring?". `discover(since)` takes `since` as `YYYY-MM`: every thread from that
    month on, oldest first; with no `since` it is the latest thread, whatever month that is (a caller that means "this
    month's" passes `since` and so gets `HnThreadNotOut` until that month's thread is out). A whole pass is read before the first candidate is
    yielded, so a failed thread never leaves a half month behind. `posts()` and `summary()` describe the pass
    `discover` just ran (no second fetch); `posts()` raises before any pass has finished."""
    name = "hn"
    schedule = "monthly"

    def __init__(self, client: HttpClient | None = None, config: HnConfig | None = None):
        self.client = client if client is not None else UrllibClient()
        self.config = config if config is not None else _default_config()
        self.version = f"hn-1-{self.config.digest}"
        self._pass: _Pass | None = None

    def discover(self, since: str | None = None):
        self._pass = None
        if since is not None and not _SINCE.match(since):
            raise ValueError(f"since {since!r} is not YYYY-MM")
        self._pass = self._read(since)
        yield from self._pass.candidates

    def thread_out(self, since: str) -> bool:
        """True when a thread from `since` (`YYYY-MM`) on is listed. One search request; nothing else is read."""
        if not _SINCE.match(since):
            raise ValueError(f"since {since!r} is not YYYY-MM")
        try:
            self._threads(since)
        except HnThreadNotOut:
            return False
        return True

    def posts(self) -> list[PostRecord]:
        if self._pass is None:
            raise RuntimeError("posts() describes a finished discover pass; none has finished")
        return list(self._pass.posts)

    def summary(self) -> dict[str, int]:
        """threads, comments (top-level, deleted ones included), dead (deleted), kept, unparsed (not a company
        header), non_us (dropped by the US filter), multi_board (posts naming more than one supported ATS board) and
        name_conflicts (a post whose domain differs from the one an earlier post of the same name gave). Empty before
        a pass has finished."""
        return dict(self._pass.summary) if self._pass is not None else {}

    # -- fetching

    def _get_json(self, url: str):
        resp = self.client.get(url)
        if resp.status != 200:
            raise HnError(f"{url}: HTTP {resp.status}")
        try:
            return resp.json()
        except ValueError as e:
            raise HnError(f"{url}: {e}") from e

    def _threads(self, since: str | None) -> list[tuple[tuple[int, int], int, str]]:
        """(year and month, thread id, title) for the threads wanted, oldest first."""
        data = self._get_json(SEARCH_URL)
        hits = data.get("hits") if isinstance(data, dict) else None
        if not isinstance(hits, list) or not all(isinstance(h, dict) for h in hits):
            raise HnError(f"{SEARCH_URL}: not a search answer with a list of hits")
        found: list[tuple[tuple[int, int], int, str]] = []
        listed: list[tuple[int, int]] = []  # the month of every thread listed, of any kind
        for hit in hits:
            title = str(hit.get("title") or "")
            any_month = _ANY_MONTH.search(title)
            if any_month and any_month.group(1).lower() in _MONTHS:
                listed.append((int(any_month.group(2)), _MONTHS[any_month.group(1).lower()]))
            m = _HIRING_TITLE.match(title)
            if not m or m.group(1).lower() not in _MONTHS:
                continue
            try:
                thread_id = int(hit.get("objectID"))
            except (TypeError, ValueError):
                raise HnError(f"{SEARCH_URL}: thread {title!r} has no usable objectID") from None
            found.append(((int(m.group(2)), _MONTHS[m.group(1).lower()]), thread_id, title))
        if not found:
            raise HnError(f"no 'Who is hiring?' thread among the {len(hits)} newest whoishiring stories")
        found.sort()
        if since is None:
            return [found[-1]]
        want = (int(since[:4]), int(since[5:]))
        if len(hits) >= PAGE_SIZE and listed and want < min(listed):
            raise HnError(f"since {since} is older than the {PAGE_SIZE} newest whoishiring stories "
                          f"(back to {min(listed)[0]}-{min(listed)[1]:02d}): the listing cannot cover it")
        chosen = [t for t in found if t[0] >= want]
        if not chosen:
            raise HnThreadNotOut(f"no 'Who is hiring?' thread from {since} on")
        return chosen

    def _comments(self, thread_id: int) -> list[dict]:
        url = ITEM_URL.format(thread_id)
        item = self._get_json(url)
        children = item.get("children") if isinstance(item, dict) else None
        if not isinstance(children, list):
            raise HnError(f"{url}: not a thread with a list of comments")
        comments = [c for c in children if isinstance(c, dict)]
        if not comments:
            raise HnError(f"{url}: thread {thread_id} has no comments")
        if not any(isinstance(c.get("text"), str) and c["text"].strip() for c in comments):
            raise HnError(f"{url}: thread {thread_id} has {len(comments)} comments and none has text")
        return comments

    # -- reading

    def _read(self, since: str | None) -> _Pass:
        cfg = self.config
        s = {"threads": 0, "comments": 0, "dead": 0, "kept": 0, "unparsed": 0, "non_us": 0, "multi_board": 0,
             "name_conflicts": 0}
        threads = self._threads(since)
        fetched = [(tid, self._comments(tid)) for _, tid, _ in threads]  # every thread, before any state is built
        s["threads"] = len(fetched)
        kept: list[_Kept] = []
        for _, comments in fetched:
            for c in comments:
                s["comments"] += 1
                text_html, cid = c.get("text"), c.get("id")
                if not isinstance(text_html, str) or not text_html.strip() or not isinstance(cid, int):
                    s["dead"] += 1
                    continue
                post = parse_post(text_html, cfg)
                if post is None:
                    s["unparsed"] += 1
                    continue
                if not is_us(post.header, cfg, name_index=post.name_index):
                    s["non_us"] += 1
                    continue
                s["kept"] += 1
                boards = [b for b in detect_all("\n".join(post.links + post.more_links)) if b.ats in SUPPORTED_ATS]
                s["multi_board"] += len(boards) > 1
                kept.append(_Kept(post, cid, POST_URL.format(cid), _posted_at(c), plain_text(text_html),
                                  _post_domain(post), boards))
        return self._assemble(kept, s)

    def _assemble(self, kept: list[_Kept], s: dict[str, int]) -> _Pass:
        """Candidates by ref. A name with no domain, or one, is one candidate (a domain-less post joins it); a name
        with several domains is one candidate per domain and its domain-less posts join none."""
        cfg = self.config
        groups: dict[str, list[_Kept]] = {}
        for k in kept:
            groups.setdefault(_ref_key(k.post.name), []).append(k)
        candidates: list[Candidate] = []
        ref_of: dict[int, str] = {}  # id of a kept post -> the ref of the candidate it belongs to
        for key, posts in groups.items():
            domains = list(dict.fromkeys(k.domain for k in posts if k.domain))
            if len(domains) > 1:
                buckets = {d: [k for k in posts if k.domain == d] for d in domains}
                s["name_conflicts"] += sum(not k.domain for k in posts)
            else:
                buckets = {domains[0] if domains else None: posts}
            for domain, members in buckets.items():
                ref = f"hn:{key}@{domain}" if domain else f"hn:{key}"
                evidence: list[EvidenceDraft] = []
                for k in members:
                    ref_of[id(k)] = ref
                    quote = k.post.header[:QUOTE_CAP]
                    if k.domain:
                        evidence.append(EvidenceDraft("domain", k.domain, "estimate", "hn", k.url, quote))
                    if k.post.stage:
                        evidence.append(EvidenceDraft("stage", k.post.stage, "estimate", "hn", k.url, quote))
                    if k.post.location:
                        evidence.append(EvidenceDraft("hq", k.post.location, "guess", "hn", k.url, quote))
                board = next((k.boards[0] for k in members if k.boards), None)
                keys = {"ats": f"{board.ats}:{board.token}"} if board else {}
                candidates.append(Candidate(name=members[0].post.name, domain=domain, keys=keys,
                                            evidence=tuple(evidence), source_ref=ref))
        records = [PostRecord(ref_of[id(k)], k.url, k.posted_at, k.text, _asks_email(k.text, cfg))
                   for k in kept if id(k) in ref_of]
        return _Pass(candidates, records, s)


def _posted_at(comment: dict) -> str | None:
    """The comment's time as canonical UTC: `created_at`, else `created_at_i` (epoch seconds); None when neither is
    readable (a missing time does not make the post unusable)."""
    try:
        if comment.get("created_at"):
            return iso_utc(comment["created_at"])
        epoch = comment.get("created_at_i")
        if isinstance(epoch, int) and epoch > 0:
            return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError, OverflowError):
        pass
    return None
