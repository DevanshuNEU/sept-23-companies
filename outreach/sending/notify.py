"""The notice to Devanshu: a macOS banner, once per kind per day.

`Notifier` is anything that takes a title and a text; the sender passes `mac_notifier`, tests pass a list's append.
"""
import subprocess
from collections.abc import Callable
from datetime import date
from sqlite3 import Connection

import live_checkout
from outreach.store.writes import append_event

__all__ = ["Notifier", "mac_notifier", "notify_once"]

Notifier = Callable[[str, str], None]


def _quote(text: str) -> str:
    """An AppleScript string literal: backslash and double quote escaped, line breaks turned into spaces."""
    return '"' + " ".join(str(text).split()).replace("\\", "\\\\").replace('"', '\\"') + '"'


def mac_notifier(title: str, text: str) -> None:
    """Show a macOS notification. Does nothing outside the live checkout, and never raises: a missing or slow
    `osascript` must not undo the pause the notice is about."""
    if not live_checkout.is_live():
        return
    script = f"display notification {_quote(text)} with title {_quote(title)}"
    try:
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        pass


def notify_once(conn: Connection, notifier: Notifier, kind: str, day: date, title: str, text: str) -> bool:
    """Call `notifier` unless this kind was already announced for this New York day (the event key
    `notify:<kind>:<day>`). Returns True when it was called. A notifier that raises is recorded as not delivered
    and the error is not passed on, so the caller's transaction (the pause) still commits."""
    key = f"notify:{kind}:{day.isoformat()}"
    if conn.execute("SELECT 1 FROM event WHERE idempotency_key = ?", (key,)).fetchone():
        return False
    delivered, error = True, ""
    try:
        notifier(title, text)
    except Exception as err:  # a notice is best effort; the pause it reports is not
        delivered, error = False, f"{type(err).__name__}: {err}"[:200]
    append_event(conn, entity="sender", entity_id=1, kind="notified", actor="sender",
                 detail={"kind": kind, "day": day.isoformat(), "title": title, "delivered": delivered,
                         "error": error},
                 idempotency_key=key)
    return True
