"""Slug probing: find a board for a company that has none by trying its guessed slug (#21, Task 26a, design 5.3 step 2).

A company with no active board gets up to four slugs made from its names, each tried once on Greenhouse, Lever and
Ashby (at most 12 requests). Only an `ok` read is a hit: an empty board proves nothing and an error is not an answer.
A slug is a guess, so a hit whose reported org name is none of the company's names is not added, and a board added
here carries `found_by = 'slug_probe'`, which gets the same first-read check as a name-only match (ruling 8).

Probing makes no EU request: the Greenhouse and Lever adapters retry a 404 on their EU host, which would double every
guessed-slug 404, so `_NoEuClient` answers an EU host with a 404 of its own and sends nothing. Normal reads are not
touched. A tripped host ends the pass for that host only. No transaction is open across a request.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import urllib.parse
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass

from outreach.crawl.ats import adapter_for
from outreach.crawl.base import Validators, valid_token
from outreach.crawl.config import CrawlConfig, load_crawl_config
from outreach.crawl.ingest import _company_name_keys
from outreach.crawl.polite import HostTripped, bucket_for
from outreach.net import HttpClient, Response
from outreach.sources.boards import BoardRef, add_board
from outreach.sources.tiers import effective_tier
from outreach.store import name_key

__all__ = ["slug_candidates", "ProbeReport", "probe_slugs", "PROBE_ATS", "MIN_SLUG", "MAX_SLUGS"]

# Probe order: the ATSs a small hiring company most often uses. The host is the adapter's US API host.
PROBE_ATS = {"greenhouse": "boards-api.greenhouse.io", "lever": "api.lever.co", "ashby": "api.ashbyhq.com"}
MIN_SLUG = 4  # a shorter slug is too likely to be another company's
MAX_SLUGS = 4
_LEGAL = frozenset({"inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "gmbh",
                    "plc", "lp", "llp", "pbc"})
_RAW_ALIAS_SOURCES = ("fein", "ats", "linkedin")


def _words(name: str) -> list[str]:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    return re.findall(r"[a-z0-9]+", ascii_name)


def _forms(words: list[str]) -> list[str]:
    return ["".join(words), "-".join(words)] if words else []


def slug_candidates(name_aliases: Iterable[str]) -> list[str]:
    """The lower-case slugs the ATSs use for a company, at most four, each valid on every probed ATS.

    Per name: the words joined with nothing, then with `-`, first without a trailing legal suffix ("Inc", "LLC") and
    then as written. A name's forms keep their order, names keep theirs, repeats are dropped, and a slug under four
    characters is never a candidate.
    """
    out: list[str] = []
    for name in name_aliases:
        words = _words(name)
        bare = list(words)
        while len(bare) > 1 and bare[-1] in _LEGAL:
            bare.pop()
        for slug in _forms(bare) + _forms(words):
            if len(slug) >= MIN_SLUG and slug not in out and all(valid_token(a, slug) for a in PROBE_ATS):
                out.append(slug)
    return out[:MAX_SLUGS]


@dataclass(frozen=True)
class ProbeReport:
    """`considered`: live companies with no active board, a candidate slug and a tier that is worked, before the limit.
    `probed`: those the pass started on. `added`, `conflicts` and `name_mismatch` count companies, one hit each.
    `errors` counts reads that failed in a way that says something is wrong (not a plain 404, the usual answer to a
    guessed slug): a 5xx, a transport failure, a wrong shape or an adapter crash."""
    considered: int
    probed: int
    added: int
    conflicts: int
    name_mismatch: int
    errors: int


class _NoEuClient:
    """Wraps a client so a request to an EU host is answered 404 and never sent (probing does no EU hop)."""

    def __init__(self, inner: HttpClient):
        self.inner = inner

    @staticmethod
    def _eu(url: str) -> bool:
        return "eu" in (urllib.parse.urlsplit(url).hostname or "").lower().split(".")

    def get(self, url: str, headers: Mapping[str, str] | None = None, *, follow_redirects: bool = False) -> Response:
        if self._eu(url):
            return Response(404, url, b"", {})
        return self.inner.get(url, headers, follow_redirects=follow_redirects)

    def post(self, url: str, body: bytes, headers: Mapping[str, str] | None = None) -> Response:
        if self._eu(url):
            return Response(404, url, b"", {})
        return self.inner.post(url, body, headers)

    def download(self, url, dest, headers=None) -> int:
        return self.inner.download(url, dest, headers)


def _names(conn: sqlite3.Connection, company_id: int, name: str) -> list[str]:
    marks = ", ".join(f"'{s}'" for s in _RAW_ALIAS_SOURCES)
    rows = conn.execute(f"SELECT alias FROM company_alias WHERE company_id = ? AND source NOT IN ({marks}) "
                        "ORDER BY created_at, alias_key", (company_id,)).fetchall()
    return [name] + [r[0] for r in rows if name_key(r[0]) != name_key(name)]


def _eligible(conn: sqlite3.Connection, company_ids: Collection[int]) -> list[tuple[int, list[str], list[str]]]:
    """`(company_id, names, slugs)` in work order: tier 1 (promoted counts as 1), tier 2, then company id. A company
    that is closed, removed, merged, has an active board, is tier 3 and not promoted, or has no usable slug is out."""
    rows = conn.execute(
        "SELECT c.company_id, c.name FROM company c WHERE c.company_id IN (SELECT value FROM json_each(?)) "
        "AND c.status NOT IN ('closed', 'removed') AND c.merged_into IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM board b WHERE b.company_id = c.company_id AND b.active = 1)",
        (json.dumps(sorted(set(company_ids))),)).fetchall()
    out = []
    for company_id, name in rows:
        tier = effective_tier(conn, company_id)
        names = _names(conn, company_id, name)
        slugs = slug_candidates(names)
        if tier < 3 and slugs:
            out.append((tier, company_id, names, slugs))
    out.sort(key=lambda t: (t[0], t[1]))
    return [(cid, names, slugs) for _, cid, names, slugs in out]


def probe_slugs(conn: sqlite3.Connection, company_ids: Collection[int], client: HttpClient, *, limit: int,
                cfg: CrawlConfig | None = None) -> ProbeReport:
    """Try each candidate slug of up to `limit` companies (of `company_ids`) on Greenhouse, Lever and Ashby.

    One `adapter.list` per slug and ATS, outermost the slug, so a company's best guess is tried everywhere before the
    next. The first hit of a company ends it: added, a `conflict` (counted, the board is never moved), or a name
    mismatch (counted, nothing written). A `HostTripped` takes that host's bucket out of the pass, the others go on, and
    the pass ends when none is left.
    """
    if limit < 1:
        raise ValueError(f"limit must be at least 1, got {limit}")
    cfg = cfg or load_crawl_config()
    work = _eligible(conn, company_ids)
    probing = _NoEuClient(client)
    adapters = {ats: adapter_for(ats, probing) for ats in PROBE_ATS}
    buckets = {ats: bucket_for(host, cfg) for ats, host in PROBE_ATS.items()}
    dead: set[str] = set()
    probed = added = conflicts = mismatches = errors = 0
    for company_id, names, slugs in work:
        if len(dead) == len(PROBE_ATS) or probed >= limit:
            break
        probed += 1
        keys = None
        done = False
        for slug in slugs:
            for ats in PROBE_ATS:
                if ats in dead or adapters[ats] is None:
                    continue
                try:
                    result = adapters[ats].list(slug, Validators())
                except HostTripped as tripped:
                    dead.update(a for a, b in buckets.items() if b == tripped.bucket or a == ats)
                    continue
                except Exception:
                    errors += 1
                    continue
                if result.result == "error":
                    errors += result.http_status != 404
                    continue
                if result.result != "ok":
                    continue
                keys = keys if keys is not None else _company_name_keys(conn, company_id)
                if result.board_name and name_key(result.board_name) not in keys:
                    mismatches += 1
                    continue
                outcome = add_board(conn, company_id, BoardRef(ats, slug), via="probe", matched_on="slug",
                                    found_by="slug_probe")
                added += outcome == "added"
                conflicts += outcome == "conflict"
                done = True
                break
            if done:
                break
    return ProbeReport(considered=len(work), probed=probed, added=added, conflicts=conflicts,
                       name_mismatch=mismatches, errors=errors)
