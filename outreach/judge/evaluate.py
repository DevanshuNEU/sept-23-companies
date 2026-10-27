"""How far the judge agrees with the golden set (`golden.toml`): `python -m outreach.judge eval`.

Two numbers, so Devanshu can see whether to trust the judge before he turns it on:
- yes/no agreement: over the golden companies marked yes or no that have a judgment, the share where the judge says
  the same (the judge's yes is a fit of at least `[eval] yes_at`). A maybe is not counted.
- rank correlation: Spearman's rho (ties take their average rank) between the golden score (the verdict's confidence,
  plus for yes, minus for no, 0 for maybe) and the judge's fit, over every golden company that has a judgment. None
  with fewer than 3, or when either list has no order.

A golden company is found by one of its domains (a domain fact that is not a guess), else by its name when exactly one
listed company has that name key. In the live checkout `eval` first judges the golden companies that have no
judgment under the current judge version, one call each, through the same code as the nightly run (even while the
job is off: eval is how he checks it); anywhere else, or with a stub, it scores only what is stored.
"""
from __future__ import annotations

import math
import sqlite3
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import live_checkout
from outreach.judge.config import JudgeConfig, load_judge_config
from outreach.judge.prompt import judge_version, read_me, render_prompt
from outreach.judge.run import Judging, judge_runner, targets_for
from outreach.ranking.common import canonical_ids
from outreach.ranking.judged import load_judgments
from outreach.store import connect
from outreach.store.writes import name_key

__all__ = ["GOLDEN_PATH", "VERDICTS", "Golden", "Row", "Report", "load_golden", "golden_score", "spearman", "score",
           "resolve", "run_eval", "eval_command"]

GOLDEN_PATH = Path(__file__).with_name("golden.toml")
VERDICTS = ("yes", "maybe", "no")


@dataclass(frozen=True)
class Golden:
    name: str
    domains: tuple[str, ...]
    verdict: str  # yes, maybe or no
    confidence: int  # percent


@dataclass(frozen=True)
class Row:
    golden: Golden
    fit: int | None
    judge_verdict: str | None  # yes or no, from the fit; None when not judged
    agree: bool | None  # None for a maybe or a company with no judgment
    note: str = ""


@dataclass(frozen=True)
class Report:
    rows: tuple[Row, ...]
    agree: int
    counted: int  # golden yes and no companies with a judgment
    judged: int  # golden companies with a judgment
    rho: float | None
    yes_at: int
    version: str = ""

    def text(self) -> str:
        lines = [f"judge eval{', version ' + self.version if self.version else ''}: a fit of {self.yes_at} or more "
                 "is the judge's yes"]
        width = max((len(r.golden.name) for r in self.rows), default=10)
        for r in self.rows:
            mine = f"{r.golden.verdict} {r.golden.confidence}"
            if r.fit is None:
                theirs = "not judged" + (f" ({r.note})" if r.note else "")
            else:
                said = {True: "agrees", False: "disagrees", None: "maybe, not counted"}[r.agree]
                theirs = f"fit {r.fit:3d}, {r.judge_verdict}, {said}" + (f" ({r.note})" if r.note else "")
            lines.append(f"  {r.golden.name:<{width}}  you: {mine:<9} judge: {theirs}")
        share = f" ({round(100 * self.agree / self.counted)}%)" if self.counted else ""
        lines.append(f"yes/no agreement: {self.agree} of {self.counted}{share}")
        rho = "too few to tell" if self.rho is None else f"{self.rho:.2f}"
        lines.append(f"rank correlation (Spearman, your verdict and confidence against the fit): {rho}, "
                     f"over {self.judged} judged companies")
        return "\n".join(lines)


def load_golden(path: Path = GOLDEN_PATH) -> list[Golden]:
    """The golden companies. `ValueError` on an entry with no name, a verdict that is not yes, maybe or no, or a
    confidence that is not a whole number from 0 to 100."""
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for i, entry in enumerate(data.get("company", []), start=1):
        name, verdict, conf = entry.get("name"), entry.get("verdict"), entry.get("confidence")
        domains = entry.get("domains", [])
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"golden entry {i} has no name")
        if verdict not in VERDICTS:
            raise ValueError(f"golden entry {name!r}: verdict must be yes, maybe or no, got {verdict!r}")
        if isinstance(conf, bool) or not isinstance(conf, int) or not 0 <= conf <= 100:
            raise ValueError(f"golden entry {name!r}: confidence must be a whole number from 0 to 100")
        if not isinstance(domains, list) or not all(isinstance(d, str) and d for d in domains):
            raise ValueError(f"golden entry {name!r}: domains must be a list of host names")
        out.append(Golden(name=name.strip(), domains=tuple(d.lower() for d in domains), verdict=verdict,
                          confidence=conf))
    return out


def golden_score(g: Golden) -> int:
    return g.confidence if g.verdict == "yes" else -g.confidence if g.verdict == "no" else 0


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Spearman's rho with average ranks for ties (Pearson's r of the ranks); None with fewer than 3 pairs or when
    either side has no order."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx, vy = sum((a - mx) ** 2 for a in rx), sum((b - my) ** 2 for b in ry)
    if vx == 0 or vy == 0:
        return None
    return cov / math.sqrt(vx * vy)


def score(golden: Sequence[Golden], fits: dict[str, int | None], *, yes_at: int,
          notes: dict[str, str] | None = None, version: str = "") -> Report:
    """The report for `fits` (golden name to the judge's fit, None when not judged)."""
    notes = notes or {}
    rows, xs, ys = [], [], []
    agree = counted = 0
    for g in golden:
        fit = fits.get(g.name)
        if fit is None:
            rows.append(Row(g, None, None, None, notes.get(g.name, "")))
            continue
        said = "yes" if fit >= yes_at else "no"
        same = None if g.verdict == "maybe" else said == g.verdict
        if same is not None:
            counted += 1
            agree += same
        xs.append(golden_score(g))
        ys.append(fit)
        rows.append(Row(g, fit, said, same, notes.get(g.name, "")))
    return Report(tuple(rows), agree, counted, len(xs), spearman(xs, ys), yes_at, version)


def resolve(conn: sqlite3.Connection, canon: dict[int, int], golden: Sequence[Golden]) -> list[int | None]:
    """The surviving company of each golden entry: by a domain that is not a guess, else by a name key exactly one
    listed company has; None when neither finds one company."""
    by_domain: dict[str, set[int]] = {}
    for r in conn.execute("SELECT company_id, value FROM evidence WHERE fact = 'domain' AND confidence <> 'guess'"):
        by_domain.setdefault(r["value"].lower(), set()).add(canon.get(r["company_id"], r["company_id"]))
    by_name: dict[str, set[int]] = {}
    for r in conn.execute("SELECT company_id, name FROM company WHERE merged_into IS NULL "
                          "AND status IN ('candidate', 'backlog', 'working')"):
        by_name.setdefault(name_key(r["name"]), set()).add(r["company_id"])
    out = []
    for g in golden:
        hits = set().union(*(by_domain.get(d, set()) for d in g.domains)) if g.domains else set()
        if len(hits) != 1:
            hits = by_name.get(name_key(g.name), set())
        out.append(next(iter(hits)) if len(hits) == 1 else None)
    return out


def run_eval(conn: sqlite3.Connection, golden: Sequence[Golden], *, cfg: JudgeConfig, runner, reader, me_text: str,
             now: datetime | None = None, judge_missing: bool) -> Report:
    """The report. With `judge_missing`, each golden company with no judgment under the current version is judged
    first (one call each; a session limit or a login problem stops the judging, and what is stored is scored)."""
    now = now or datetime.now(timezone.utc)
    canon = canonical_ids(conn)
    ids = resolve(conn, canon, golden)
    version = judge_version(render_prompt(me_text, cfg), cfg)
    notes: dict[str, str] = {}
    if judge_missing:
        latest = load_judgments(conn, canon)
        todo = [cid for cid in dict.fromkeys(i for i in ids if i is not None)
                if latest.get(cid) is None or latest[cid].version != version]
        work = Judging(conn, runner, reader, cfg, me_text, now)
        for t in targets_for(conn, canon, cfg, todo, now=now, version=version):
            outcome, detail = work.one(t)
            if outcome in ("session_limit", "auth"):
                break
            if outcome not in ("judged", "reused"):
                notes[t.name] = f"{outcome}: {detail}"
    latest = load_judgments(conn, canon)
    fits: dict[str, int | None] = {}
    for g, cid in zip(golden, ids):
        if cid is None:
            fits[g.name] = None
            notes.setdefault(g.name, "not found in the store")
            continue
        j = latest.get(cid)
        fits[g.name] = j.fit if j is not None else None
        if j is not None and j.version != version:
            notes.setdefault(g.name, "judged under an older judge version")
        if j is not None and j.thin:
            notes[g.name] = (notes.get(g.name, "") + "; " if g.name in notes else "") + "thin evidence"
    return score(golden, fits, yes_at=cfg.yes_at, notes=notes, version=version)


def eval_command(args, stdout, *, runner=None, reader=None, me_text: str | None = None) -> int:
    """`eval`: judge what is missing (live checkout only), then print the report."""
    from outreach.crawl.config import load_crawl_config
    from outreach.judge.fetch import build_reader
    cfg = load_judge_config()
    golden = load_golden(Path(args.file) if args.file else GOLDEN_PATH)
    live = live_checkout.is_live()
    if live:
        runner = runner or judge_runner(cfg)
        reader = reader or build_reader(cfg, load_crawl_config())
    else:
        print("eval: not the live checkout, so nothing is judged now; scoring what is stored", file=stdout)
    conn = connect()
    try:
        report = run_eval(conn, golden, cfg=cfg, runner=runner, reader=reader,
                          me_text=me_text if me_text is not None else read_me(), judge_missing=live)
    finally:
        conn.close()
    print(report.text(), file=stdout)
    return 0
