"""Per-company job-board read streaks and the write-or-keep decision.

A read is "ok", "empty" or "error". An error never closes a company. An
empty board closes it only on the second empty read; an error in between
leaves the empty count alone.
"""
import json
import os
import tempfile


def load(path):
    try:
        with open(path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save(path, state):
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def update(prev, read, today):
    empty = prev.get("empty", 0)
    error = prev.get("error", 0)
    if read == "ok":
        empty = error = 0
    elif read == "empty":
        empty += 1
        error = 0
    elif read == "error":
        error += 1
    else:
        raise ValueError("unknown read: %r" % (read,))
    return {"empty": empty, "error": error, "last": read, "since": today}


def action(entry):
    last = entry.get("last")
    if last == "ok" or (last == "empty" and entry.get("empty", 0) >= 2):
        return "write"
    return "keep"


def needs_review(entry):
    return entry.get("error", 0) >= 3
