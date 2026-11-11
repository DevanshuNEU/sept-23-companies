"""Blind judge for two sets of cold-email drafts (#211). Pure library: it builds prompts, parses and scores replies.

Set "a" is the baseline, set "b" the candidate. The judge never learns which is which: versions and inbox rows are
shuffled with random.Random(seed), and the key maps each shuffled slot back to its set. Prompts live in the package
as files so scores stay comparable across runs.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).parent
METRICS = ("human", "personal", "warm", "reply")
PREVIEW_CHARS = 110
DECOYS_PER_ITEM = 4
MIN_COMPARED = 8
# Neutral made-up senders shown for the a and b rows in inbox mode.
SENDER_NAMES = [
    "Alex Morgan", "Jordan Blake", "Taylor Quinn", "Casey Reed", "Riley Hart", "Morgan Ellis",
    "Jamie Cole", "Drew Parker", "Avery Stone", "Cameron Lane", "Skyler Wade", "Reese Holt",
]


@dataclass(frozen=True)
class Draft:
    id: str
    recipient: str
    subject: str
    body: str


def load_set(path) -> list[Draft]:
    rows = json.loads(Path(path).read_text())
    drafts = [Draft(str(r["id"]), r["recipient"], r["subject"], r["body"]) for r in rows]
    seen = set()
    for x in drafts:
        if x.id in seen:
            raise ValueError(f"{path}: duplicate id {x.id}")
        seen.add(x.id)
    return drafts


def load_decoys() -> list[dict]:
    return json.loads((_HERE / "decoys.json").read_text())


def pair_sets(a: list[Draft], b: list[Draft]):
    """Pairs for ids in both sets (in a's order), and the ids found in only one set."""
    bmap = {x.id: x for x in b}
    amap = {x.id: x for x in a}
    pairs = [(x, bmap[x.id]) for x in a if x.id in bmap]
    only = [x.id for x in a if x.id not in bmap] + [x.id for x in b if x.id not in amap]
    return pairs, only


def _is_tie(x: Draft, y: Draft) -> bool:
    return x.subject == y.subject and x.body == y.body


def _prompt(name: str, items: list[dict]) -> str:
    head = (_HERE / name).read_text().rstrip("\n")
    return head + "\n" + json.dumps(items, indent=1, ensure_ascii=False) + "\n"


def body_prompt(pairs, *, seed: int):
    """Prompt text and key {id: {"A": "a"|"b", "B": "a"|"b"}}. Each item's order is shuffled."""
    rng = random.Random(seed)
    items, key = [], {}
    for x, y in pairs:
        order = [("a", x), ("b", y)]
        rng.shuffle(order)
        key[x.id] = {"A": order[0][0], "B": order[1][0]}
        items.append({
            "id": x.id,
            "recipient": x.recipient,
            "versions": {lab: {"subject": dr.subject, "body": dr.body}
                         for lab, (_, dr) in zip(("A", "B"), order)},
        })
    return _prompt("prompt_body.md", items), key


def _preview(body: str) -> str:
    lines = body.strip().split("\n", 1)
    rest = lines[1] if len(lines) > 1 else lines[0]
    return " ".join(rest.split())[:PREVIEW_CHARS]


def inbox_prompt(pairs, *, seed: int, decoys: list[dict]):
    """Prompt text and key {id: {row number: "a"|"b"|"ab"|"decoy"}}. Identical a and b show one shared "ab" row."""
    rng = random.Random(seed)
    items, key = [], {}
    for x, y in pairs:
        names = rng.sample(SENDER_NAMES, 2)
        rows = [("a", names[0], x)]
        if _is_tie(x, y) or (x.subject == y.subject and _preview(x.body) == _preview(y.body)):
            rows = [("ab", names[0], x)]
        else:
            rows.append(("b", names[1], y))
        entries = [(src, {"from": n, "subject": dr.subject, "preview": _preview(dr.body)}) for src, n, dr in rows]
        for dec in rng.sample(decoys, DECOYS_PER_ITEM):
            entries.append(("decoy", {"from": dec["from"], "subject": dec["subject"], "preview": dec["preview"]}))
        rng.shuffle(entries)
        inbox, k = [], {}
        for n, (src, e) in enumerate(entries, 1):
            inbox.append({"row": str(n), **e})
            k[str(n)] = src
        key[x.id] = k
        items.append({"id": x.id, "you_are": x.recipient, "inbox": inbox})
    return _prompt("prompt_inbox.md", items), key


def parse_reply(text: str) -> dict:
    """The first JSON object in the text; the judge may wrap it in prose."""
    dec = json.JSONDecoder()
    pos = text.find("{")
    while pos != -1:
        try:
            obj, _ = dec.raw_decode(text, pos)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            return obj
        pos = text.find("{", pos + 1)
    raise ValueError("no JSON object in the reply")


def _num(v):
    """A score: an int or float (not a bool), finite, from 1 to 10. Anything else is None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 1 <= v <= 10:
        return None
    return float(v)


def _list(v) -> list:
    return v if isinstance(v, list) else []


def _items(reply) -> dict:
    """Items of a reply keyed by id. A non-dict reply, a non-list "items" and non-dict items give nothing."""
    if not isinstance(reply, dict):
        return {}
    return {str(it.get("id")): it for it in _list(reply.get("items")) if isinstance(it, dict)}


def _clean(text) -> str:
    return " ".join(str(text).split())


@dataclass
class BodyResult:
    compared: int = 0
    wins_a: int = 0
    wins_b: int = 0
    ties: int = 0
    tie_ids: list = field(default_factory=list)
    avg_a: dict = field(default_factory=dict)
    avg_b: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    malformed: list = field(default_factory=list)
    why: dict = field(default_factory=dict)
    repeats: list = field(default_factory=list)


def score_body(reply, key: dict, pairs) -> BodyResult:
    """Score a reply. An item missing from the reply goes in `missing`; an item with a non-numeric or absent score
    or an invalid "best" goes in `malformed` and counts nowhere else. Averages use only items where A and B are
    both numeric for that metric, so both sets are averaged over the same items."""
    res = BodyResult()
    ties = {x.id for x, y in pairs if _is_tie(x, y)}
    got = _items(reply)
    sums = {"a": {m: [] for m in METRICS}, "b": {m: [] for m in METRICS}}
    for x, _ in pairs:
        i = x.id
        if i in ties:
            res.ties += 1
            res.tie_ids.append(i)
            continue
        it = got.get(i)
        if it is None or i not in key:
            res.missing.append(i)
            continue
        best = key[i].get(str(it.get("best", "")).strip().upper())
        scored = {}
        for m in METRICS:
            sc = it.get(m)
            va, vb = (_num(sc.get("A")), _num(sc.get("B"))) if isinstance(sc, dict) else (None, None)
            if va is None or vb is None:
                scored = None
                break
            scored[m] = (va, vb)
        if best not in ("a", "b") or scored is None:
            res.malformed.append(i)
            continue
        res.compared += 1
        if best == "a":
            res.wins_a += 1
        else:
            res.wins_b += 1
        for m, (va, vb) in scored.items():
            for lab, v in (("A", va), ("B", vb)):
                sums[key[i][lab]][m].append(v)
        res.why[i] = _clean(it.get("why", ""))
    for src, out in (("a", res.avg_a), ("b", res.avg_b)):
        for m in METRICS:
            vals = sums[src][m]
            out[m] = sum(vals) / len(vals) if vals else 0.0
    res.repeats = [_clean(r) for r in _list(reply.get("repeats"))] if isinstance(reply, dict) else []
    return res


def verdict(result: BodyResult) -> str:
    """INCOMPLETE when any item is missing from the reply or malformed (the bar needs every item judged), or when
    fewer than MIN_COMPARED non-tie items were compared (too few to pass on). Otherwise PASS when b wins at least two thirds of the compared non-tie items (8 of 12) and b's average scores
    for "human" and "reply" are not below a's, else FAIL. No compared items is INCOMPLETE if something was
    missing or malformed, else FAIL."""
    if result.missing or result.malformed:
        return "INCOMPLETE"
    if result.compared == 0:
        return "FAIL"
    if result.compared < MIN_COMPARED:
        return "INCOMPLETE"
    needed = math.ceil(2 * result.compared / 3)
    ok = (result.wins_b >= needed
          and result.avg_b["human"] >= result.avg_a["human"]
          and result.avg_b["reply"] >= result.avg_a["reply"])
    return "PASS" if ok else "FAIL"


@dataclass
class InboxResult:
    items: int = 0
    first_a: int = 0
    first_b: int = 0
    first_decoy: int = 0
    opened_a: int = 0
    opened_b: int = 0
    opened_decoy: int = 0
    archived_a: int = 0
    archived_b: int = 0
    archived_decoy: int = 0
    missing: list = field(default_factory=list)
    malformed: list = field(default_factory=list)
    why: dict = field(default_factory=dict)


def score_inbox(reply, key: dict) -> InboxResult:
    """Counts per set. A shared "ab" row counts for both a and b. An item whose "open" or "never" is not a list
    is `malformed`; repeated rows in "open" count once."""
    res = InboxResult()
    got = _items(reply)

    def bump(prefix, src):
        for s in ("a", "b") if src == "ab" else (src,):
            attr = f"{prefix}_{s}"
            setattr(res, attr, getattr(res, attr) + 1)

    for i, rows in key.items():
        it = got.get(i)
        if it is None:
            res.missing.append(i)
            continue
        if not isinstance(it.get("open"), list) or not isinstance(it.get("never", []), list):
            res.malformed.append(i)
            continue
        res.items += 1
        opened = list(dict.fromkeys(str(r) for r in it["open"]))[:3]
        for n, r in enumerate(opened):
            if r in rows:
                bump("opened", rows[r])
                if n == 0:
                    bump("first", rows[r])
        for r in dict.fromkeys(str(r) for r in it.get("never", [])):
            if r in rows:
                bump("archived", rows[r])
        res.why[i] = _clean(it.get("why", ""))
    return res


def _problems(r) -> list:
    out = []
    if r.missing:
        out += ["", f"Missing from the reply ({len(r.missing)}): " + ", ".join(r.missing)]
    if r.malformed:
        out += ["", f"Malformed items ({len(r.malformed)}): " + ", ".join(r.malformed)]
    return out


def report(result, *, seed: int, mode: str) -> str:
    """Markdown report. Body mode ends with the PASS or FAIL line."""
    lines = [f"# Draft lab: {mode} mode", "", f"Seed: {seed} (same seed, same shuffle)", ""]
    if mode == "body":
        r = result
        lines += [f"Compared: {r.compared}. Wins: a {r.wins_a}, b {r.wins_b}. Ties: {r.ties}.", ""]
        if r.tie_ids:
            lines += ["Identical in both sets (a tie, counted for neither): " + ", ".join(r.tie_ids), ""]
        lines += ["| score | a | b |", "|---|---|---|"]
        lines += [f"| {m} | {r.avg_a[m]:.2f} | {r.avg_b[m]:.2f} |" for m in METRICS]
        lines += _problems(r)
        if r.why:
            lines += ["", "Why, per item:"] + [f"- {i}: {w}" for i, w in r.why.items()]
        if r.repeats:
            lines += ["", "Repeats:"] + [f"- {x}" for x in r.repeats]
        v = verdict(r)
        if v == "INCOMPLETE":
            v += f" ({len(r.missing)} missing, {len(r.malformed)} malformed)"
        lines += ["", f"Verdict: {v}"]
    else:
        r = result
        lines += [
            f"Items answered: {r.items}.",
            "",
            "| | a | b | decoy |",
            "|---|---|---|---|",
            f"| first pick | {r.first_a} | {r.first_b} | {r.first_decoy} |",
            f"| opened | {r.opened_a} | {r.opened_b} | {r.opened_decoy} |",
            f"| archived on sight | {r.archived_a} | {r.archived_b} | {r.archived_decoy} |",
        ]
        lines += _problems(r)
        if r.why:
            lines += ["", "Why, per item:"] + [f"- {i}: {w}" for i, w in r.why.items()]
    return "\n".join(lines) + "\n"
