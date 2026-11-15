"""The API side of the mail server lookup: the lookup itself lives in `outreach.drafts.mx` (shared with the MCP draft
tools) and is re-exported here; `ensure` adds the `mx_check` cache write. `unknown` is never stored, and the lookup runs
outside any transaction."""
import sqlite3
from collections.abc import Callable, Sequence

from outreach.api.db import is_busy
from outreach.api.settings import Settings
from outreach.drafts.checks import cached_mx, normalise_domain
from outreach.drafts.mx import (DOH_ENDPOINTS, MAX_ANSWER_BYTES, MX_TYPE, NXDOMAIN, TIMEOUT_SECONDS, MxStatus,  # noqa: F401
                                Resolver, _opener, _parse, _verdict, check_domain, default_resolvers, doh)
from outreach.store.db import transaction

__all__ = ["DOH_ENDPOINTS", "TIMEOUT_SECONDS", "Resolver", "doh", "default_resolvers", "check_domain", "ensure"]


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
