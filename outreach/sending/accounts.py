"""Sender accounts in the store: sync from config, the sponsor or general lane of a company, and routing.

A company's first email goes from its pin when that account sends, else from the sending account of its lane.
A follow-up always goes from the account that sent the first email. Routing only reads; `pin` and `sync` write.
"""
import sqlite3
from dataclasses import dataclass
from typing import Literal

from outreach.drafts.followups import canonical_company, company_map, resolve_company
from outreach.sending.config import AccountConfig, SendConfig
from outreach.store.writes import append_event


@dataclass(frozen=True)
class AccountRow:
    """One sender_account row joined with its AccountConfig."""
    account_id: int
    cfg: AccountConfig
    paused: bool
    auth_state: str
    next_send_at: str | None
    revision: int


def sync(conn: sqlite3.Connection, cfg: SendConfig, now: str) -> dict[str, AccountRow]:
    """Make sure every configured account has a row. Matches by address ignoring case; never deletes or renames.

    Returns the rows keyed by the address as written in config."""
    out: dict[str, AccountRow] = {}
    for acct in cfg.accounts:
        # sender_account.address is COLLATE NOCASE (0017), so `=` ignores case.
        row = conn.execute("SELECT * FROM sender_account WHERE address = ?", (acct.address,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO sender_account(address, kind, created_at, updated_at) VALUES (?, ?, ?, ?)",
                         (acct.address, acct.kind, now, now))
            row = conn.execute("SELECT * FROM sender_account WHERE address = ?", (acct.address,)).fetchone()
        out[acct.address] = AccountRow(account_id=row["account_id"], cfg=acct, paused=bool(row["paused"]),
                                       auth_state=row["auth_state"], next_send_at=row["next_send_at"],
                                       revision=row["revision"])
    return out


# A company fact that puts a company in the sponsor lane.
_SPONSOR_FACT = ("(fact = 'everify' AND value = 'enrolled') OR (fact = 'sponsorship' AND value IN ('yes', 'opt_only')) "
                 "OR fact = 'lca'")


def lane_of(conn: sqlite3.Connection, company_id: int) -> Literal["sponsor", "general"]:
    """Sponsor when the company is enrolled in E-Verify, sponsors (or takes OPT only), or has any LCA row. The whole
    merge family counts: the survivor and every company merged into it, since a merge does not move evidence."""
    row = conn.execute(
        "WITH RECURSIVE family(id) AS (SELECT ? UNION SELECT c.company_id FROM company c JOIN family f "
        f"ON c.merged_into = f.id) SELECT 1 FROM company_fact WHERE company_id IN family AND ({_SPONSOR_FACT}) "
        "LIMIT 1", (canonical_company(conn, company_id),)).fetchone()
    return "sponsor" if row else "general"


def route(conn: sqlite3.Connection, cfg: SendConfig, touch_id: int,
          accounts: dict[str, AccountRow]) -> tuple[AccountRow | None, str]:
    """The account that sends this touch, and why: thread, pinned, lane, (None, thread_missing) or (None, lane_waits).

    thread_missing: the touch's account is not in `accounts`. A touch with a Gmail thread but no account was sent
    before touches carried one, so its thread is account 1's (as everywhere else a NULL account is). A pin to an
    account that does not send (or is not in config) counts as no pin. The company is followed through
    `merged_into`; a pin on the raw id still counts, and the lane reads the sponsor facts of the whole merge family."""
    return routes(conn, cfg, [touch_id], accounts)[touch_id]


def routes(conn: sqlite3.Connection, cfg: SendConfig, touch_ids, accounts: dict[str, AccountRow]
           ) -> dict[int, tuple[AccountRow | None, str]]:
    """`route` for many touches with a fixed number of queries (the queue routes every approved message)."""
    ids = sorted(set(touch_ids))
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    touches = conn.execute(
        "SELECT t.touch_id, t.account_id, t.gmail_thread_id, p.company_id FROM touch t "
        f"JOIN person p ON p.person_id = t.person_id WHERE t.touch_id IN ({marks})", ids).fetchall()
    companies = company_map(conn)
    pins = {row["company_id"]: row["account_id"] for row in conn.execute(
        "SELECT company_id, account_id FROM company_sender")}
    # Each sponsor fact counts for its company's survivor: a merge does not move evidence.
    sponsors = {resolve_company(companies, row[0])[-1] if row[0] in companies else row[0] for row in conn.execute(
        f"SELECT DISTINCT company_id FROM company_fact WHERE {_SPONSOR_FACT}")}
    by_id = {a.account_id: a for a in accounts.values()}
    out: dict[int, tuple[AccountRow | None, str]] = {}
    for touch in touches:
        thread_account = touch["account_id"]
        if thread_account is None and touch["gmail_thread_id"]:
            thread_account = 1
        if thread_account is not None:
            found = by_id.get(thread_account)
            out[touch["touch_id"]] = (found, "thread") if found is not None else (None, "thread_missing")
            continue
        raw = touch["company_id"]
        cid = resolve_company(companies, raw)[-1] if raw in companies else raw
        pin = pins.get(cid, pins.get(raw))
        pinned = by_id.get(pin) if pin is not None else None
        if pinned is not None and pinned.cfg.sends:
            out[touch["touch_id"]] = (pinned, "pinned")
            continue
        lane_cfg = cfg.lane_account("sponsor" if cid in sponsors else "general")
        if lane_cfg is not None and lane_cfg.address in accounts:
            out[touch["touch_id"]] = (accounts[lane_cfg.address], "lane")
        else:
            out[touch["touch_id"]] = (None, "lane_waits")
    return out


def pin(conn: sqlite3.Connection, company_id: int, account_id: int, by: Literal["rule", "you"], now: str) -> None:
    """Pin a company to an account, replacing any earlier pin, and log it. The company is resolved through
    `merged_into` first, so the pin lands on the surviving company whatever id the caller passes."""
    company_id = canonical_company(conn, company_id)
    prev = conn.execute("SELECT account_id FROM company_sender WHERE company_id = ?", (company_id,)).fetchone()
    conn.execute(
        "INSERT INTO company_sender(company_id, account_id, pinned_by, pinned_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(company_id) DO UPDATE SET account_id = excluded.account_id, pinned_by = excluded.pinned_by, "
        "pinned_at = excluded.pinned_at", (company_id, account_id, by, now))
    append_event(conn, entity="company", entity_id=company_id, company_id=company_id, kind="company_pinned",
                 actor="rules" if by == "rule" else "devanshu", at=now,
                 detail={"account_id": account_id, "by": by,
                         "previous_account_id": prev["account_id"] if prev else None})


def _row(row: sqlite3.Row, acct: AccountConfig) -> AccountRow:
    return AccountRow(account_id=row["account_id"], cfg=acct, paused=bool(row["paused"]),
                      auth_state=row["auth_state"], next_send_at=row["next_send_at"], revision=row["revision"])


def in_play(conn: sqlite3.Connection, cfg: SendConfig) -> dict[str, AccountRow]:
    """Read only: every configured account by its address as written in config, in config order, as `sync` would
    return it. An account with no row yet gets the id `sync` will give it (the next free id, in config order) and a
    fresh row's state, so a reader (status, the Outbox) sees the same routing before the first tick as after it."""
    out: dict[str, AccountRow] = {}
    next_id = (conn.execute("SELECT MAX(account_id) FROM sender_account").fetchone()[0] or 0) + 1
    for acct in cfg.accounts:
        row = conn.execute("SELECT * FROM sender_account WHERE address = ?", (acct.address,)).fetchone()
        if row is not None:
            out[acct.address] = _row(row, acct)
            continue
        out[acct.address] = AccountRow(account_id=next_id, cfg=acct, paused=False, auth_state="unknown",
                                       next_send_at=None, revision=1)
        next_id += 1
    return out


def sending(rows: dict[str, AccountRow]) -> list[AccountRow]:
    """The accounts that send (`sends = true`), in config order."""
    return [row for row in rows.values() if row.cfg.sends]


def primary(conn: sqlite3.Connection, cfg: SendConfig) -> AccountRow:
    """Read only: the row of the first account block (account 1, the old single sender, which rests now). For the
    readers that still show one account and for tests; the tick and the queue route every message to its own account.
    Falls back to account id 1 when the address has no row yet."""
    acct = cfg.accounts[0]
    row = conn.execute("SELECT * FROM sender_account WHERE address = ?", (acct.address,)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM sender_account WHERE account_id = 1").fetchone()
    if row is None:
        return AccountRow(account_id=1, cfg=acct, paused=False, auth_state="unknown", next_send_at=None, revision=1)
    return AccountRow(account_id=row["account_id"], cfg=acct, paused=bool(row["paused"]),
                      auth_state=row["auth_state"], next_send_at=row["next_send_at"], revision=row["revision"])


def by_id(conn: sqlite3.Connection, cfg: SendConfig, account_id: int | None) -> AccountRow | None:
    """Read only: the row of `account_id` (None counts as account 1, a touch sent before accounts existed), or None
    when it has no row or its address is not in the config."""
    row = conn.execute("SELECT * FROM sender_account WHERE account_id = ?",
                       (1 if account_id is None else account_id,)).fetchone()
    if row is None:
        return None
    for acct in cfg.accounts:
        if acct.address.lower() == row["address"].lower():
            return AccountRow(account_id=row["account_id"], cfg=acct, paused=bool(row["paused"]),
                              auth_state=row["auth_state"], next_send_at=row["next_send_at"],
                              revision=row["revision"])
    return None


@dataclass(frozen=True)
class Pause:
    kind: str  # 'by_you' (sender row 1) or 'hard_block'
    reason: str
    at: str | None
    own: bool  # True when the account's own row holds it, False when it is sender row 1


def pause_of(conn: sqlite3.Connection, account_id: int) -> Pause | None:
    """Read only: why this account may not send, or None. The account's own hard block comes first (it needs his
    acknowledgement and he must see its reason), then sender row 1 (his button, or an old hard block), which
    pauses every account."""
    own = conn.execute("SELECT paused, pause_kind, paused_reason, paused_at FROM sender_account "
                       "WHERE account_id = ?", (account_id,)).fetchone()
    if own is not None and own["paused"]:
        return Pause(own["pause_kind"], own["paused_reason"], own["paused_at"], own=True)
    row = conn.execute("SELECT paused, pause_kind, paused_reason, paused_at FROM sender WHERE sender_id = 1").fetchone()
    if row["paused"]:
        return Pause(row["pause_kind"] or "by_you", row["paused_reason"], row["paused_at"], own=False)
    return None


def hard_pause(conn: sqlite3.Connection, account_id: int, reason: str, now: str) -> bool:
    """Hard-block this account's own row. False (nothing written) when it is already paused."""
    return conn.execute(
        "UPDATE sender_account SET paused = 1, pause_kind = 'hard_block', paused_reason = ?, paused_at = ?, "
        "updated_at = ?, revision = revision + 1 WHERE account_id = ? AND paused = 0",
        (reason, now, now, account_id)).rowcount > 0


def clear_hard_blocks(conn: sqlite3.Connection, now: str) -> int:
    """Lift every account's hard block (his click). Returns how many rows were cleared."""
    return conn.execute(
        "UPDATE sender_account SET paused = 0, pause_kind = NULL, paused_reason = '', paused_at = NULL, "
        "updated_at = ?, revision = revision + 1 WHERE paused = 1", (now,)).rowcount
