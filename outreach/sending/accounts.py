"""Sender accounts in the store: sync from config, the sponsor or general lane of a company, and routing.

A company's first email goes from its pin when that account sends, else from the sending account of its lane.
A follow-up always goes from the account that sent the first email. Routing only reads; `pin` and `sync` write.
"""
import sqlite3
from dataclasses import dataclass
from typing import Literal

from outreach.drafts.followups import canonical_company
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
        # sender_account.address is COLLATE NOCASE (0015), so `=` ignores case.
        row = conn.execute("SELECT * FROM sender_account WHERE address = ?", (acct.address,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO sender_account(address, kind, created_at, updated_at) VALUES (?, ?, ?, ?)",
                         (acct.address, acct.kind, now, now))
            row = conn.execute("SELECT * FROM sender_account WHERE address = ?", (acct.address,)).fetchone()
        out[acct.address] = AccountRow(account_id=row["account_id"], cfg=acct, paused=bool(row["paused"]),
                                       auth_state=row["auth_state"], next_send_at=row["next_send_at"],
                                       revision=row["revision"])
    return out


def lane_of(conn: sqlite3.Connection, company_id: int) -> Literal["sponsor", "general"]:
    """Sponsor when the company is enrolled in E-Verify, sponsors (or takes OPT only), or has any LCA row."""
    row = conn.execute(
        "SELECT 1 FROM company_fact WHERE company_id = ? AND ("
        "(fact = 'everify' AND value = 'enrolled') OR (fact = 'sponsorship' AND value IN ('yes', 'opt_only')) "
        "OR fact = 'lca') LIMIT 1", (company_id,)).fetchone()
    return "sponsor" if row else "general"


def route(conn: sqlite3.Connection, cfg: SendConfig, touch_id: int,
          accounts: dict[str, AccountRow]) -> tuple[AccountRow | None, str]:
    """The account that sends this touch, and why: thread, pinned, lane, (None, thread_missing) or (None, lane_waits).

    thread_missing: the touch's account is not in `accounts`. A pin to an account that does not send (or is not in
    config) counts as no pin. The company is followed through `merged_into`; a pin on the raw id still counts."""
    touch = conn.execute(
        "SELECT t.account_id, p.company_id FROM touch t JOIN person p ON p.person_id = t.person_id "
        "WHERE t.touch_id = ?", (touch_id,)).fetchone()
    by_id = {a.account_id: a for a in accounts.values()}
    if touch["account_id"] is not None:
        found = by_id.get(touch["account_id"])
        return (found, "thread") if found is not None else (None, "thread_missing")
    cid = canonical_company(conn, touch["company_id"])
    pin = conn.execute("SELECT account_id FROM company_sender WHERE company_id IN (?, ?) "
                       "ORDER BY company_id = ? DESC", (cid, touch["company_id"], cid)).fetchone()
    if pin is not None:
        pinned = by_id.get(pin["account_id"])
        if pinned is not None and pinned.cfg.sends:
            return pinned, "pinned"
    lane_cfg = cfg.lane_account(lane_of(conn, cid))
    if lane_cfg is not None and lane_cfg.address in accounts:
        return accounts[lane_cfg.address], "lane"
    return None, "lane_waits"


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
