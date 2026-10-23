"""SQL seed helpers for the API view tests: each adds the smallest valid rows so a test states only what it is about.

They write through plain SQL and depend only on the store's tables, never on another stream's code.
"""
import itertools
import json
import sqlite3

_n = itertools.count(1)
T0 = "2026-11-01T12:00:00Z"


def company(conn: sqlite3.Connection, name: str = "Acme", status: str = "candidate", **cols) -> int:
    """A company. `cols` sets any other column (close_reason, remove_reason, merged_into, paused_at, ...)."""
    row = {"name": name, "status": status, "status_at": T0, "created_at": T0}
    if status == "closed":
        row.setdefault("close_reason", "other")
    if status == "removed":
        row.setdefault("remove_reason", "not_a_fit")
    row.update(cols)
    return _insert(conn, "company", row)


def alias(conn, company_id: int, text: str, source: str = "hand") -> None:
    conn.execute(
        "INSERT INTO company_alias(alias_key, company_id, alias, source, created_at) VALUES (?,?,?,?,?)",
        (text.lower().replace(" ", ""), company_id, text, source, T0),
    )


def evidence(conn, company_id: int, fact: str, value, *, confidence: str = "confirmed", locked: bool = False,
             source_kind: str | None = None, source_ref: str = "", quote: str = "", recorded_at: str = T0,
             observed_on: str | None = None, low: int | None = None, high: int | None = None) -> int:
    if fact in ("lca", "funding") and not isinstance(value, str):
        value = json.dumps(value)
    if fact == "everify" and observed_on is None:
        observed_on = "2026-10-21"
    if fact in ("size", "headcount") and low is None:
        low = int(value) if str(value).isdigit() else 1
    return _insert(conn, "evidence", {
        "company_id": company_id, "fact": fact, "value": value, "num_low": low, "num_high": high,
        "confidence": confidence, "locked": int(locked), "source_kind": source_kind or ("hand" if locked else "website"),
        "source_ref": source_ref, "quote": quote, "observed_on": observed_on, "recorded_at": recorded_at,
    })


def lca(fy: int = 2025, software: int = 3) -> dict:
    return {"fy": fy, "software": software, "jr": 1, "level1": 0, "newemp_jr": 0}


def board(conn, company_id: int, ats: str = "ashby", token: str | None = None, *, last_read: str | None = None,
          last_read_at: str | None = None, error_streak: int = 0, empty_streak: int = 0, active: int = 1) -> int:
    row = {"company_id": company_id, "ats": ats, "token": token or f"tok{next(_n)}", "active": active, "created_at": T0,
           "last_read": last_read, "last_read_at": last_read_at, "error_streak": error_streak,
           "empty_streak": empty_streak}
    if last_read:
        row["last_read_at"] = last_read_at or T0
        row["streak_since"] = T0
    return _insert(conn, "board", row)


def posting(conn, company_id: int, title: str = "Software Engineer", *, board_id: int | None = None,
            first_seen_at: str = T0, last_seen_at: str | None = None, closed_at: str | None = None,
            close_reason: str | None = None, url: str | None = None, location: str = "San Francisco, CA") -> int:
    n = next(_n)
    if closed_at and not close_reason:
        close_reason = "gone_from_board"
    return _insert(conn, "posting", {
        "company_id": company_id, "board_id": board_id, "source_key": f"url:seed-{n}", "title": title,
        "location": location, "url": url if url is not None else f"https://jobs.example/{n}",
        "first_seen_at": first_seen_at, "last_seen_at": last_seen_at or first_seen_at,
        "closed_at": closed_at, "close_reason": close_reason,
    })


def decision(conn, posting_id: int, **cols) -> None:
    version = conn.execute("SELECT ruleset_version FROM ruleset WHERE is_current = 1").fetchone()[0]
    row = {
        "posting_id": posting_id, "ruleset_version": version, "decided_at": T0, "lane": "engineering",
        "level_tier": "new_grad", "level_ok": 1, "level_basis": "Title says new grad", "us": "yes",
        "sponsorship": "silent", "citizenship": 0, "eligibility": "ok", "start_2027": 0,
    }
    row.update(cols)
    if row["sponsorship"] != "silent" and not row.get("sponsorship_quote"):
        row["sponsorship_quote"] = "We sponsor visas."
    if row["citizenship"] and not row.get("citizenship_quote"):
        row["citizenship_quote"] = "US citizens only."
    if row["eligibility"] == "excluded":
        row.setdefault("eligibility_reason", "grad_window")
        row.setdefault("eligibility_quote", "Graduating in 2025 only.")
    if row.get("years_asked") is not None and not row.get("years_quote"):
        row["years_quote"] = f"{row['years_asked']}+ years"
    _insert(conn, "decision", row)


def person(conn, company_id: int, name: str = "Pat Lee", role_kind: str = "engineer", *, email: str | None = "auto",
           channel: str = "email", title: str = "Software Engineer", linkedin_url: str | None = None) -> int:
    n = next(_n)
    if email == "auto":
        email = f"p{n}@acme.example" if channel == "email" else None
    row = {"company_id": company_id, "name": name, "title": title, "role_kind": role_kind, "email": email,
           "linkedin_url": linkedin_url, "channel": channel, "added_by": "devanshu", "created_at": T0, "updated_at": T0}
    if channel == "linkedin":
        row["linkedin_reason"] = "no_email"
    return _insert(conn, "person", row)


def touch(conn, person_id: int, state: str = "open", **cols) -> int:
    row = {"person_id": person_id, "state": state, "created_at": T0, "updated_at": T0}
    if state == "replied":
        row.update(replied_at="2026-11-09T10:00:00Z", reply_kind="interested")
    if state == "bounced":
        row.update(bounced_at="2026-11-06T10:00:00Z")
    if state == "closed":
        row.update(closed_at="2026-11-09T10:00:00Z", close_reason="no_reply")
    row.update(cols)
    return _insert(conn, "touch", row)


def send_first(conn, touch_id: int, at: str = "2026-11-05T15:00:00Z") -> int:
    """Mark the touch's first email sent (the thread columns the CHECKs want) and add the sent seq 0 message."""
    n = next(_n)
    conn.execute("UPDATE touch SET first_sent_at = ?, first_rfc_id = ?, gmail_thread_id = ? WHERE touch_id = ?",
                 (at, f"<rfc{n}@x>", f"thread{n}", touch_id))
    return message(conn, touch_id, 0, "sent", sent_at=at)


def message(conn, touch_id: int, seq: int, state: str = "drafted", *, subject: str = "Hello", body: str = "Hi there",
            sent_at: str | None = None) -> int:
    n = next(_n)
    row = {"touch_id": touch_id, "seq": seq, "state": "sent" if state == "sent" else "drafted", "subject": subject,
           "body": body, "created_at": T0, "updated_at": T0}
    if seq > 0:
        row["due_on"] = "2026-11-12"
    if state == "sent":
        row.update(sent_at=sent_at or T0, rfc_message_id=f"<m{n}@x>")
    mid = _insert(conn, "message", row)
    if state == "approved":
        conn.execute("UPDATE message SET state = 'approved', approved_at = ? WHERE message_id = ?", (T0, mid))
    return mid


def inbound(conn, touch_id: int, received_at: str, from_addr: str = "pat@acme.example", body: str = "Sure!",
            kind: str = "human") -> int:
    return _insert(conn, "inbound_message", {
        "touch_id": touch_id, "gmail_message_id": f"g{next(_n)}", "from_addr": from_addr, "subject": "Re: Hello",
        "body_text": body, "kind": kind, "received_at": received_at, "recorded_at": received_at,
    })


def linkedin(conn, person_id: int, kind: str, at: str, **cols) -> int:
    return _insert(conn, "linkedin_log", {"person_id": person_id, "kind": kind, "at": at, "recorded_at": at, **cols})


def event(conn, company_id: int | None, kind: str, at: str, *, actor: str = "system", detail: dict | None = None,
          entity: str = "company") -> int:
    return _insert(conn, "event", {
        "at": at, "recorded_at": at, "entity": entity, "company_id": company_id, "kind": kind, "actor": actor,
        "detail": json.dumps(detail or {}),
    })


def review(conn, company_id: int, kind: str, *, board_id: int | None = None, posting_id: int | None = None,
           reason: str = "Needs a look.", evidence_key: str = "k1", created_at: str = "2026-11-08T12:00:00Z",
           state: str = "open") -> int:
    row = {"kind": kind, "company_id": company_id, "board_id": board_id, "posting_id": posting_id, "reason": reason,
           "evidence_key": evidence_key, "raised_by": "rules", "state": state, "created_at": created_at}
    if state == "resolved":
        row.update(resolution="marked_ok", resolved_at=created_at)
    return _insert(conn, "review_item", row)


def mx(conn, domain: str, status: str) -> None:
    conn.execute("INSERT INTO mx_check(domain, status, checked_at) VALUES (?,?,?)", (domain, status, T0))


def _insert(conn, table: str, row: dict) -> int:
    cols = list(row)
    cur = conn.execute(
        f"INSERT INTO {table}({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [row[c] for c in cols]
    )
    return cur.lastrowid
