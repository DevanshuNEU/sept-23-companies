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
    """`since` is the date the current streak of `read` began."""
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
    since = prev.get("since", today) if read == prev.get("last") else today
    return {"empty": empty, "error": error, "last": read, "since": since}


def action(entry):
    last = entry.get("last")
    if last == "ok" or (last == "empty" and entry.get("empty", 0) >= 2):
        return "write"
    return "keep"


def needs_review(entry):
    return entry.get("error", 0) >= 3


def report_lines(state, names):
    """The three LAST_RUN sections for boards that were not read cleanly."""
    def name(cid):
        return names.get(cid, cid)

    entries = sorted(state.items(), key=lambda kv: name(kv[0]).lower())
    unread = [name(c) for c, e in entries if e.get("last") == "error"]
    empty_once = [name(c) for c, e in entries
                  if e.get("last") == "empty" and action(e) == "keep"]
    review = ["%s (since %s)" % (name(c), e.get("since", "?"))
              for c, e in entries if needs_review(e)]

    def section(title, items):
        return ["## %s (%d)" % (title, len(items)), ""] + (
            ["- " + x for x in items] if items else ["None."]) + [""]

    return (section("Boards that could not be read this run, kept as before", unread)
            + section("Empty once, kept; closes if empty again next run", empty_once)
            + section("Needs review: unreadable 3 runs in a row", review))
