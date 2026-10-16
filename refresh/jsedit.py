"""
Brace-aware edits to single fields of dashboard/data.js entries. Never use a regex across entries: one bad
match once ate 73 companies. Each edit finds the entry by id, walks its braces with string awareness, and
sets one top-level field.
"""
import json

def entry_span(src, cid):
    """(start, end) of the `{ id:"cid", ... }` object literal."""
    i = src.find('{ id:' + json.dumps(cid))
    if i < 0: return None
    return i, _close(src, i)

def _close(src, i):
    """Index just past the bracket/brace that opens at src[i]."""
    depth, q, esc, j = 0, None, False, i
    while True:
        ch = src[j]
        if q:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == q: q = None
        elif ch in "\"'": q = ch
        elif ch in "{[": depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0: return j + 1
        j += 1

def _field(seg, key):
    """(start of `key:`, end of its value) at the entry's top level, or None."""
    depth, q, esc, j = 0, None, False, 0
    while j < len(seg):
        ch = seg[j]
        if q:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == q: q = None
        elif ch in "\"'": q = ch
        elif ch in "{[": depth += 1
        elif ch in "}]": depth -= 1
        elif depth == 1 and seg.startswith(key + ":", j) and seg[j - 1] in " ,{\n":
            v = j + len(key) + 1
            while seg[v] == " ": v += 1
            if seg[v] in "{[": end = _close(seg, v)
            elif seg[v] in "\"'":
                k = v + 1
                while seg[k] != seg[v]: k += 2 if seg[k] == "\\" else 1
                end = k + 1
            else:
                end = v
                while seg[end] not in ",}\n": end += 1
            return j, end
        j += 1
    return None

def set_field(src, cid, key, value):
    """Set (or add) one top-level field on one entry. value is any JSON-serialisable object."""
    sp = entry_span(src, cid)
    if not sp: raise KeyError(cid)
    a, b = sp
    seg = src[a:b]
    lit = f"{key}:{json.dumps(value, ensure_ascii=True)}"
    f = _field(seg, key)
    if f: seg = seg[:f[0]] + lit + seg[f[1]:]
    else:
        nf = _field(seg, "name")                  # the real top-level name field, never text inside a string
        seg = seg[:nf[0]] + lit + ", " + seg[nf[0]:] if nf else seg[:-1].rstrip() + ", " + lit + " }"
    return src[:a] + seg + src[b:]

def remove_entry(src, cid):
    """Delete one entry and the comma that joins it to its neighbour."""
    sp = entry_span(src, cid)
    if not sp: return src
    a, b = sp
    rest = src[b:].lstrip()
    if rest.startswith(","):                       # entry, next entry: drop the entry and its trailing comma
        return src[:a].rstrip() + "\n  " + rest[1:].lstrip()
    head = src[:a].rstrip()                        # last entry: drop the comma before it
    if head.endswith(","): head = head[:-1]
    return head + "\n" + rest
