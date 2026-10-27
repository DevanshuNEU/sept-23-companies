"""Where a judgment is written: one `event` row, nothing else (no migration, no fact row).

Entity `company`, kind `judged`, actor `claude` (the store's actors have no `judge`; a model wrote it, so it is
Claude's, and `detail.by` says `judge`). `detail` holds the checked fields, the version, the cache key, the pages
read (with their text, so no page is fetched twice inside the cache window) and `text`, the one line the company's
History shows. `outreach.ranking.judged.load_judgments` is the reader.

A judgment never writes a decision, a no-go, an email, a note, a fact or a score: only this event.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence

from outreach.ranking.judged import JUDGED_KIND, judge_line, judgment_from_detail
from outreach.store import append_event

__all__ = ["posting_key", "pages_hash", "cache_key", "record_judgment"]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def posting_key(posting: dict | None) -> str:
    """A hash of the best posting's title and url ('' with no posting)."""
    if posting is None:
        return ""
    return _sha(f"{posting.get('title') or ''}\0{posting.get('url') or ''}")[:16]


def pages_hash(pages: Sequence[dict]) -> str:
    """A hash of what was read: each page's kind, url, status and text, in kind order."""
    parts = sorted(f"{p.get('kind')}\0{p.get('url')}\0{p.get('status')}\0{p.get('text') or ''}" for p in pages)
    return _sha("\0\0".join(parts))[:16]


def cache_key(version: str, posting: str, pages: str) -> str:
    """The cache key: the judge version, the posting hash and the page hash. The same key means nothing the model
    would read has changed."""
    return _sha(f"{version}\0{posting}\0{pages}")[:16]


def record_judgment(conn: sqlite3.Connection, company_id: int, fields: dict, *, version: str, key: str,
                    posting: str, pages: Sequence[dict], reused_from: int | None = None,
                    at: str | None = None) -> int:
    """Append the `judged` event of a checked judgment (`validate.check_judgment`'s fields) and return its id.
    `reused_from` is the event a judgment was copied from when nothing it read had changed (no model call).
    `ValueError` when the fields are not a usable judgment (the reader would skip the row)."""
    detail = {"by": "judge", "version": version, "key": key, "posting_key": posting, "pages": list(pages),
              "reused": reused_from is not None, "reused_from": reused_from,
              **{k: fields[k] for k in ("fit", "builds", "domains", "why", "risk", "level_ok", "quotes", "evidence",
                                        "thin")}}
    j = judgment_from_detail(json.loads(json.dumps(detail)), company_id=company_id, event_id=0, at="")
    if j is None:
        raise ValueError("not a usable judgment")
    detail["text"] = judge_line(j)
    event_id = append_event(conn, entity="company", kind=JUDGED_KIND, actor="claude", entity_id=company_id,
                            company_id=company_id, detail=detail, at=at)
    assert event_id is not None  # no idempotency key, so the insert always lands
    return event_id
