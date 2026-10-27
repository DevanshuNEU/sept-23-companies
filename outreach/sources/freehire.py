"""freehire.me as a lead to US early-career boards (#115, Part of #47).

freehire is a free, open-source job aggregator with a documented public API. One pass asks its lean search for US
junior and intern engineering postings from the last `days` days, on the board platforms Knock's crawler reads, page
by page, and turns each posting into a company and its board. That is all it takes from freehire: identity matches or
makes the company, find-boards turns the `ats` key into a board, and the crawler reads the board itself, so every fact
Knock acts on (sponsorship, level, size) comes from its own crawl and rules. Nothing freehire says about size or
sponsorship is stored, so nothing is hidden on its word. Its terms allow the API without a plan for "your own job
search, research, or a tool you run for yourself" (checked 2026-10-27).

The board comes from the posting URL when it names exactly one board the crawler reads. A posting on the company's
own site (`stripe.com/jobs/search?gh_jid=...`) names none; for a platform whose token is a plain slug, freehire's
`external_id` is `<token>:<job id>`, so its prefix is the token, and the URL's host is the company's domain. Workday
and Oracle tokens are never taken from `external_id` (its shape differs there).

Skipped and counted in `summary()`: a platform that is not mapped or whose reader is off (`not_read`), a posting with
no board (`no_board`), an empty name or one holding a slash, which is a URL freehire used for a name (`bad_name`), a
board this pass already yielded (`repeat`) and a board the store already has (`known_board`, through
`bind_board_known`: identity does not match on a board, so a lead for a company Knock already crawls would otherwise
make a duplicate company) and a second board under a company name this pass already yielded (`same_name`: RTX
lists two Workday sites, and two candidates of one name would make two companies). Across passes a new board under a
known name is still yielded, and identity raises the usual merge item, as for HN. freehire's staffing company types
put a company in work tier 3, as the LCA staffing rule does.

Error is not empty: an HTTP error, a body that is not JSON or a page without a `data` list raises `FreehireError`. A
whole pass is read before the first candidate is yielded, so a failed page never leaves half a pass behind.
"""
from __future__ import annotations

import hashlib
import tomllib
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from outreach.crawl.config import load_crawl_config
from outreach.net import HttpClient, UrllibClient
from outreach.sources.ats_detect import detect_all
from outreach.sources.base import Candidate
from outreach.sources.boards import BoardRef
from outreach.sources.keys import classify_domain, registrable_domain
from outreach.store import name_key

__all__ = ["CONFIG_PATH", "API_PAGE_CAP", "FreehireError", "FreehireConfig", "FreehireSource", "load_freehire_config",
           "search_url"]

CONFIG_PATH = Path(__file__).with_name("freehire.toml")
API_PAGE_CAP = 100  # the lean search answers at most this many postings a page
# Platforms whose board token freehire's `external_id` prefix spells exactly (a slug). Workday and Oracle tokens are a
# host and a site, which the prefix does not give.
_SLUG_TOKENS = frozenset({"ashby", "bamboohr", "breezy", "greenhouse", "jazzhr", "lever", "pinpoint", "recruitee",
                          "rippling", "smartrecruiters", "teamtailor", "workable"})
_SUMMARY_KEYS = ("pages", "postings", "candidates", "repeat", "same_name", "known_board", "not_read", "no_board",
                 "bad_name", "staffing", "capped")


class FreehireError(Exception):
    """freehire did not give a usable answer: an HTTP error, a body that is not JSON, or no `data` list."""


@dataclass(frozen=True)
class FreehireConfig:
    enabled: bool
    search_url: str
    page_size: int
    max_pages: int
    days: int
    countries: tuple[str, ...]
    seniority: tuple[str, ...]
    categories: tuple[str, ...]
    staffing_types: tuple[str, ...]
    platforms: dict[str, str] = field(hash=False)  # freehire platform name -> board.ats
    digest: str = ""

    def __post_init__(self):
        if not 1 <= self.page_size <= API_PAGE_CAP:
            raise ValueError(f"page_size {self.page_size} is outside 1 to {API_PAGE_CAP}")
        if self.max_pages < 1 or self.days < 1:
            raise ValueError("max_pages and days are at least 1")


def load_freehire_config(path: Path = CONFIG_PATH) -> FreehireConfig:
    raw = Path(path).read_bytes()
    data = tomllib.loads(raw.decode("utf-8"))
    return FreehireConfig(
        enabled=bool(data["enabled"]), search_url=data["search_url"], page_size=int(data["page_size"]),
        max_pages=int(data["max_pages"]), days=int(data["days"]), countries=tuple(data["countries"]),
        seniority=tuple(data["seniority"]), categories=tuple(data["categories"]),
        staffing_types=tuple(data["staffing_types"]), platforms=dict(data["platforms"]),
        digest=hashlib.sha256(raw).hexdigest()[:12])


def search_url(cfg: FreehireConfig, *, offset: int, days: int, reads: Callable[[str], bool]) -> str:
    """The lean search for one page: the filters, the platforms whose reader is on (`reads`), the window, the page."""
    params = [("countries", c) for c in cfg.countries] + [("seniority", s) for s in cfg.seniority]
    params += [("category", c) for c in cfg.categories]
    params += [("source", name) for name, ats in cfg.platforms.items() if reads(ats)]
    params += [("posted_within_days", str(days)), ("limit", str(cfg.page_size)), ("offset", str(offset))]
    return cfg.search_url + "?" + urllib.parse.urlencode(params)


class FreehireSource:
    """Companies with a fresh US junior or intern engineering posting on a board Knock reads. `discover()` takes no
    `since`: the window is `days` (the config's, or the one given here). `summary()` describes the pass `discover`
    just ran."""
    name = "freehire"
    schedule = "daily"

    def __init__(self, client: HttpClient | None = None, config: FreehireConfig | None = None, *,
                 days: int | None = None):
        self.client = client if client is not None else UrllibClient()
        self.config = config if config is not None else load_freehire_config()
        self.days = days if days is not None else self.config.days
        if self.days < 1:
            raise ValueError(f"days {self.days} is under 1")
        self.version = f"freehire-1-{self.config.digest}"
        self._reads = load_crawl_config().reads
        self._board_known: Callable[[str, str], bool] = lambda ats, token: False
        self._tiers: dict[str, tuple[int, str]] = {}
        self._summary: dict[str, int] = {}

    def bind_board_known(self, known: Callable[[str, str], bool]) -> None:
        """`known(ats, token)`: whether the store already has the board (the discover job binds the store's)."""
        self._board_known = known

    def work_tier(self, source_ref: str) -> tuple[int, str] | None:
        return self._tiers.get(source_ref)

    def summary(self) -> dict[str, int]:
        return dict(self._summary)

    def discover(self, since: str | None = None):
        if since is not None:
            raise ValueError("freehire reads a window of days, not a since; give FreehireSource(days=...)")
        self._tiers, self._summary = {}, dict.fromkeys(_SUMMARY_KEYS, 0)
        postings = self._read_all()
        yield from self._candidates(postings)

    # -- fetching

    def _read_all(self) -> list[dict]:
        cfg, postings = self.config, []
        for number in range(cfg.max_pages):
            url = search_url(cfg, offset=number * cfg.page_size, days=self.days, reads=self._reads)
            resp = self.client.get(url)
            if resp.status != 200:
                raise FreehireError(f"{url}: HTTP {resp.status}")
            try:
                body = resp.json()
            except ValueError as e:
                raise FreehireError(f"{url}: {e}") from e
            data = body.get("data") if isinstance(body, dict) else None
            if not isinstance(data, list):
                raise FreehireError(f"{url}: no data list in the answer")
            self._summary["pages"] += 1
            postings += [p for p in data if isinstance(p, dict)]
            meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
            total = meta.get("total")
            seen = (number + 1) * cfg.page_size
            if len(data) < cfg.page_size or (isinstance(total, int) and seen >= total):
                break
        else:
            self._summary["capped"] = 1
        self._summary["postings"] = len(postings)
        return postings

    # -- reading

    def _board(self, posting: dict) -> BoardRef | None:
        url = posting.get("url") if isinstance(posting.get("url"), str) else ""
        found = [ref for ref in detect_all(url) if self._reads(ref.ats)]
        if len(found) == 1:
            return found[0]
        ats = self.config.platforms.get(posting.get("source"))
        external = posting.get("external_id")
        if found or ats not in _SLUG_TOKENS or not isinstance(external, str) or ":" not in external:
            return None
        try:
            return BoardRef(ats, external.split(":", 1)[0])
        except ValueError:
            return None

    def _candidates(self, postings: list[dict]):
        s, yielded, names = self._summary, set(), set()
        for posting in postings:
            ats = self.config.platforms.get(posting.get("source"))
            if ats is None or not self._reads(ats):
                s["not_read"] += 1
                continue
            name = posting.get("company").strip() if isinstance(posting.get("company"), str) else ""
            if not name or "/" in name:
                s["bad_name"] += 1
                continue
            ref = self._board(posting)
            if ref is None:
                s["no_board"] += 1
                continue
            source_ref = f"freehire:{ref.ats}:{ref.token}"
            if source_ref in yielded:
                s["repeat"] += 1
                continue
            yielded.add(source_ref)
            if self._board_known(ref.ats, ref.token):
                s["known_board"] += 1
                continue
            key = name_key(name)
            if key and key in names:
                s["same_name"] += 1
                continue
            names.add(key)
            kind = (posting.get("enrichment") or {}).get("company_type") if isinstance(
                posting.get("enrichment"), dict) else None
            if kind in self.config.staffing_types:
                self._tiers[source_ref] = (3, f"staffing or outsourcing firm (freehire company type: {kind})")
                s["staffing"] += 1
            s["candidates"] += 1
            yield Candidate(name=name, domain=self._domain(posting), keys={"ats": f"{ref.ats}:{ref.token}"},
                            source_ref=source_ref)

    @staticmethod
    def _domain(posting: dict) -> str | None:
        host = urllib.parse.urlsplit(posting.get("url") or "").hostname or ""
        root = registrable_domain(host.lower())
        return root if root and classify_domain(root) == "company" else None
