"""The mail server lookup: DNS over HTTPS (Cloudflare, then Google) and the `mx_check` cache.

A request carries only the domain name and the record type: no cookies, no contact details, nothing about Devanshu.
`ok` and `none` are stored for 24 hours; `unknown` (no answer anywhere) is never stored and only warns, so he
decides. Every failure is `unknown`, never `ok`. The lookup runs outside any transaction: a slow resolver never holds
the store. Tests pass resolvers (`app.state.mx_resolvers` replaces the network).
"""
import json
import sqlite3
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from typing import Literal

from outreach.api.db import is_busy
from outreach.api.settings import Settings
from outreach.drafts.checks import cached_mx, normalise_domain
from outreach.store.db import transaction

__all__ = ["DOH_ENDPOINTS", "TIMEOUT_SECONDS", "Resolver", "doh", "default_resolvers", "check_domain", "ensure"]

DOH_ENDPOINTS = ("https://cloudflare-dns.com/dns-query", "https://dns.google/resolve")
TIMEOUT_SECONDS = 5
MAX_ANSWER_BYTES = 65_536
MX_TYPE = 15
NXDOMAIN = 3

# A resolver takes a normalised domain and returns its MX record data ("10 mx.example.com."), [] for an answer with
# no mail server (no MX, or the domain does not exist), or None (or raises) when it has no answer.
Resolver = Callable[[str], list[str] | None]
MxStatus = Literal["ok", "none", "unknown"]


def _opener() -> urllib.request.OpenerDirector:
    """urllib's plain opener: no cookie handler, and its only default header is the generic Python-urllib agent."""
    return urllib.request.build_opener()


def _parse(raw: bytes) -> list[str] | None:
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    status = data.get("Status")
    if type(status) is not int:
        return None
    if status == NXDOMAIN:
        return []
    if status != 0:
        return None
    answers = data.get("Answer", [])
    if not isinstance(answers, list):
        return None
    out = []
    for answer in answers:
        if not isinstance(answer, dict):
            return None
        if answer.get("type") != MX_TYPE:
            continue  # a CNAME on the way
        record = answer.get("data")
        if not isinstance(record, str):
            return None
        out.append(record)
    return out


def doh(base_url: str, *, opener=None, timeout: float = TIMEOUT_SECONDS) -> Resolver:
    """A resolver for one DoH JSON endpoint. Any transport or format failure is None (no answer)."""

    def resolve(domain: str) -> list[str] | None:
        query = urllib.parse.urlencode({"name": domain, "type": "MX"})
        request = urllib.request.Request(f"{base_url}?{query}", headers={"Accept": "application/dns-json"},
                                         method="GET")
        try:
            with (opener or _opener()).open(request, timeout=timeout) as response:
                raw = response.read(MAX_ANSWER_BYTES + 1)
        except Exception:  # noqa: BLE001 - every failure is no answer, so the next resolver is asked
            return None
        if len(raw) > MAX_ANSWER_BYTES:
            return None
        return _parse(raw)

    return resolve


def default_resolvers() -> list[Resolver]:
    return [doh(url) for url in DOH_ENDPOINTS]


def _verdict(answer) -> MxStatus | None:
    """`ok` when any record names a host, `none` for no records or only a null MX (`0 .`), None when malformed."""
    if not isinstance(answer, list):
        return None
    real = False
    for record in answer:
        parts = record.split() if isinstance(record, str) else []
        if len(parts) != 2 or not parts[0].isdigit():
            return None
        real = real or parts[1] != "."
    return "ok" if real else "none"


def check_domain(domain: str, resolvers: Sequence[Resolver] | None = None) -> MxStatus:
    """Ask each resolver in turn until one answers. An invalid domain asks nobody and is `unknown`."""
    name = normalise_domain(domain)
    if name is None:
        return "unknown"
    for resolver in default_resolvers() if resolvers is None else resolvers:
        try:
            answer = resolver(name)
        except Exception:  # noqa: BLE001 - a failing resolver is no answer
            continue
        verdict = _verdict(answer)
        if verdict is not None:
            return verdict
    return "unknown"


def ensure(settings: Settings, conn_factory: Callable[[Settings], sqlite3.Connection], domain: str, now: str, *,
           resolvers: Sequence[Resolver] | None = None) -> MxStatus:
    """The domain's mail server status: the cached row when under 24 hours old, else a lookup.

    The cache is read on one connection, which is closed before the lookup; the answer is stored on a new connection
    in its own short transaction, so nothing is held while DNS is slow. `unknown` is never stored (an older row is
    left as it was). A busy store skips the cache write and still answers."""
    name = normalise_domain(domain)
    if name is None:
        return "unknown"
    conn = conn_factory(settings)
    try:
        cached = cached_mx(conn, name, now)
    finally:
        conn.close()
    if cached != "unknown":
        return cached
    status = check_domain(name, resolvers)
    if status == "unknown":
        return status
    conn = conn_factory(settings)
    try:
        with transaction(conn):
            conn.execute(
                "INSERT INTO mx_check(domain, status, checked_at) VALUES (?, ?, ?) ON CONFLICT(domain) "
                "DO UPDATE SET status = excluded.status, checked_at = excluded.checked_at", (name, status, now))
    except sqlite3.OperationalError as err:
        if not is_busy(err):
            raise
    finally:
        conn.close()
    return status
