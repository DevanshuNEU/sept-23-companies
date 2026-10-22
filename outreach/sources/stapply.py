"""The stapply company lists (#31 Task 14): a free lookup from a company's names to the ATS board it may have.

stapply publishes one CSV per ATS (`name,slug,url`) and a manifest with the sha256 of each. `StapplyDirectory` reads
them from a folder and finds boards by exact normalized name (the key the research measured, about 95% right), by
the list's slug, or by the row's URL host being the company's own domain. `refresh` keeps the folder current.

Requests carry the shared declared User-Agent and nothing else that names anyone (a bare client gets a 403).
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import urllib.parse
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from outreach.net import USER_AGENT, HttpClient
from outreach.sources.boards import BoardRef, normalize_token
from outreach.sources.keys import classify_domain, registrable_domain

__all__ = ["MANIFEST_URL", "STAPPLY_ATS", "BoardHit", "StapplyDirectory", "board_match_key", "board_token"]

MANIFEST_URL = "https://storage.stapply.ai/jobhive/v1/manifest.json"
MAX_HITS = 3
MIN_KEY = 4

# stapply list name -> board.ats. Lists with no board.ats (jobvite, adp, ukg, taleo, paylocity...) are absent and
# never read.
STAPPLY_ATS: dict[str, str] = {
    **{name: name for name in (
        "ashby", "bamboohr", "breezy", "cornerstone", "eightfold", "gem", "greenhouse", "icims", "jazzhr", "join_com",
        "lever", "paycom", "personio", "pinpoint", "recruitee", "rippling", "smartrecruiters", "successfactors",
        "teamtailor", "workable", "workday")},
    "oracle": "oracle_orc",
}

_SUFFIXES = re.compile(r"\b(inc|llc|l l c|corp|corporation|co|ltd|limited|the|usa|us|incorporated|company|holdings|"
                       r"group|pbc|plc|lp|llp)\b")


def board_match_key(name: str) -> str:
    """The key the research measured: lower case, parentheses and a `dba` or `f/k/a` tail dropped, punctuation removed,
    company-form words removed, spaces removed. A key under 4 characters never matches (`StapplyDirectory`)."""
    s = (name or "").lower()
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"\b(d/?b/?a|f/k/a)\b.*", "", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = _SUFFIXES.sub(" ", s)
    return re.sub(r"\s+", "", s)


_LOCALE = re.compile(r"en|[a-z]{2}-[A-Z]{2}")
_WORKDAY_HOST = re.compile(r"(?P<tenant>[a-z0-9-]+)\.(?P<wd>wd[0-9]+)\.myworkdayjobs\.com", re.I)


def _split(url: str) -> tuple[str, list[str]]:
    parts = urllib.parse.urlsplit((url or "").strip())
    return (parts.hostname or "").lower(), [p for p in parts.path.split("/") if p]


def _token(list_name: str, slug: str, url: str) -> str:
    """The raw token for a row; `ValueError` when the row has no usable one."""
    host, path = _split(url)
    if list_name == "workday":  # the CSV slug is not the Workday token: `<tenant>.wd<N>/<site>` comes from the URL
        m = _WORKDAY_HOST.fullmatch(host)
        sites = [p for p in path if not _LOCALE.fullmatch(p)][:1]
        if not m or not sites:
            raise ValueError(f"no Workday tenant and site in {url!r}")
        return f"{m['tenant']}.{m['wd']}/{sites[0]}"
    if list_name == "oracle":
        if "sites" not in path or path.index("sites") + 1 >= len(path):
            raise ValueError(f"no Oracle site in {url!r}")
        return f"{host}/{path[path.index('sites') + 1]}"
    if list_name == "icims":
        return host
    return slug.strip()


def board_token(list_name: str, slug: str, url: str) -> str | None:
    """The foundation's token for a stapply row, or None when the row has none that passes `normalize_token` (a slug
    that is itself a URL, a Workday or Oracle URL with no site)."""
    ats = STAPPLY_ATS.get(list_name)
    if ats is None:
        return None
    try:
        return normalize_token(ats, _token(list_name, slug, url))
    except ValueError:
        return None


@dataclass(frozen=True)
class BoardHit:
    ref: BoardRef
    url: str
    matched_on: Literal["name", "slug", "host"]


_STRENGTH = {"host": 0, "name": 1, "slug": 2}  # a row found two ways is reported by the strongest
_LIST_NAME = re.compile(r"[a-z0-9_]+")


class StapplyDirectory:
    """The supported lists of a stapply folder, indexed. `skipped_rows` counts rows whose token did not validate."""

    def __init__(self, version: str):
        self.version = version
        self.skipped_rows = 0
        self._order: dict[tuple[str, str], int] = {}
        self._rows: dict[str, list[tuple[BoardRef, str]]] = {"name": defaultdict(list), "slug": defaultdict(list),
                                                              "host": defaultdict(list)}

    @classmethod
    def load(cls, folder: Path) -> StapplyDirectory:
        """Read `manifest.json` and every supported `<list>.csv` in the folder. `FileNotFoundError` with no manifest;
        `ValueError` when the manifest is unreadable or no supported list is there: never an empty directory."""
        folder = Path(folder)
        try:
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"{folder / 'manifest.json'} is not JSON: {e}") from e
        generated = manifest.get("generated_at") if isinstance(manifest, dict) else None
        if not isinstance(generated, str) or not generated:
            raise ValueError(f"{folder / 'manifest.json'} has no generated_at")
        directory = cls("stapply-1-" + generated)
        lists = 0
        for list_name, ats in STAPPLY_ATS.items():
            path = folder / f"{list_name}.csv"
            if not path.is_file():
                continue
            lists += 1
            with open(path, encoding="utf-8", errors="replace", newline="") as f:
                for row in csv.DictReader(f):
                    directory._add(list_name, ats, (row.get("name") or "").strip(), (row.get("slug") or "").strip(),
                                   (row.get("url") or "").strip())
        if not lists:
            raise ValueError(f"no supported stapply list in {folder}")
        return directory

    def _add(self, list_name: str, ats: str, name: str, slug: str, url: str) -> None:
        token = board_token(list_name, slug, url)
        if token is None:
            self.skipped_rows += 1
            return
        ref = BoardRef(ats, token)
        self._order.setdefault((ats, token), len(self._order))
        for kind, key in (("name", board_match_key(name)), ("slug", board_match_key(slug.replace("-", " ")))):
            if len(key) >= MIN_KEY:
                self._rows[kind][key].append((ref, url))
        root = registrable_domain(_split(url)[0])
        if root and classify_domain(root) == "company":
            self._rows["host"][root].append((ref, url))

    def lookup(self, names: Iterable[str], domain: str | None) -> list[BoardHit]:
        """Boards whose row has the same normalized name (`name`) or slug (`slug`) as one of the names, or whose URL
        host is the company's registrable domain or a subdomain of it (`host`). Exact matches only. More than
        `MAX_HITS` distinct boards is too ambiguous to trust and returns none."""
        found: dict[BoardRef, BoardHit] = {}

        def take(ref: BoardRef, url: str, how: str) -> None:
            old = found.get(ref)
            if old is None or _STRENGTH[how] < _STRENGTH[old.matched_on]:
                found[ref] = BoardHit(ref, url, how)

        for key in {board_match_key(n) for n in names}:
            if len(key) < MIN_KEY:
                continue
            for how in ("name", "slug"):
                for ref, url in self._rows[how].get(key, ()):
                    take(ref, url, how)
        root = registrable_domain(domain)
        if root and classify_domain(root) == "company":
            for ref, url in self._rows["host"].get(root, ()):
                host = _split(url)[0]
                if host == root or host.endswith("." + root):
                    take(ref, url, "host")
        if len(found) > MAX_HITS:
            return []
        return sorted(found.values(), key=lambda h: self._order[(h.ref.ats, h.ref.token)])

    @staticmethod
    def refresh(client: HttpClient, folder: Path) -> list[str]:
        """Download the manifest and every list whose sha256 differs from the file in the folder (a missing file
        differs). Each download is checked against the manifest's hash before it replaces the old file, and the
        manifest is saved last, so the folder never holds a version that does not match its files. Returns the file
        names updated. A transport failure raises `FetchError`; an HTTP error, a malformed manifest or a hash
        mismatch raises `ValueError`. Nothing is half written."""
        folder = Path(folder)
        headers = {"User-Agent": USER_AGENT}
        resp = client.get(MANIFEST_URL, headers, follow_redirects=True)
        if resp.status != 200:
            raise ValueError(f"{MANIFEST_URL}: HTTP {resp.status}")
        try:
            manifest = resp.json()
        except ValueError as e:
            raise ValueError(f"{MANIFEST_URL}: {e}") from e
        lists = manifest.get("by_ats_companies") if isinstance(manifest, dict) else None
        if not isinstance(lists, dict) or not isinstance(manifest.get("generated_at"), str):
            raise ValueError(f"{MANIFEST_URL} has no generated_at and by_ats_companies")
        folder.mkdir(parents=True, exist_ok=True)
        updated: list[str] = []
        for name, entry in lists.items():
            if not _LIST_NAME.fullmatch(name):
                continue  # a list name is a file name here: never a path
            url, want = entry.get("csv") if isinstance(entry, dict) else None, \
                entry.get("sha256") if isinstance(entry, dict) else None
            if not isinstance(url, str) or not isinstance(want, str):
                raise ValueError(f"the manifest entry for {name!r} has no csv and sha256")
            dest = folder / f"{name}.csv"
            if dest.is_file() and _sha256(dest) == want.lower():
                continue
            tmp = folder / f"{name}.csv.download"
            try:
                client.download(url, tmp, headers)
                if _sha256(tmp) != want.lower():
                    raise ValueError(f"{url}: the download does not match the manifest's sha256")
                os.replace(tmp, dest)
            finally:
                tmp.unlink(missing_ok=True)
            updated.append(dest.name)
        saved = folder / "manifest.json.download"
        try:
            saved.write_bytes(resp.body)
            os.replace(saved, folder / "manifest.json")
        finally:
            saved.unlink(missing_ok=True)
        return updated


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
