"""The Done-when check of #6 on real data (Task 15): "the top of the list differs visibly from the middle".

Run by hand, never discovered by `unittest`:

    uv run python -m tests.ranking_realcheck [--universe PATH] [--jobs PATH] [--sample N] [--store-dir DIR]

It reads the interim list's `universe.json` and `jobs.json` (read only) and builds a scratch store from them under
`~/Desktop/outreach-scratch/ranking-realcheck/` (never a system temp folder, never the live store): one company per
universe row with LCA evidence, staffing risk, E-Verify (enrolled only), cap-exempt (`likely` for the lottery_exempt
group) and its boards, and one posting per job with its body and date. The data was read on the day of the interim
run, so every board is `ok` and every posting last seen then, and that day is `today`. The seeding goes through
`tests.ranking_seed.seed_ranking_fixture` (the same insert helpers and the real `rebuild`), then `run_rank` writes the
scores. Two bands print (the top 50 and the 50 around the median rank), then the numbers are asserted. Exit code 1
when a threshold misses; the thresholds are never loosened here. Only company names, scores, lanes and reason lines
are printed, with anything that looks like an email or a phone number masked.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
import time
from datetime import date, datetime
from pathlib import Path

from outreach.crawl.stale import load_stale
from outreach.install import install
from outreach.ranking.confidence import LEVELS
from outreach.ranking.job import run_rank
from outreach.ranking.score import list_order
from outreach.ranking.scores import hidden_list, ranked_list
from outreach.store import connect, is_live_path
from tests.ranking_seed import READ_AT, seed_ranking_fixture

__all__ = ["band", "middle_start", "band_stats", "main"]

BAND = 50
SCRATCH_ROOT = Path.home() / "Desktop" / "outreach-scratch" / "ranking-realcheck"
INTERIM = Path.home() / "Desktop" / "outreach-scratch" / "interim-list"
LIVE_DIR = Path.home() / "Library" / "Application Support" / "outreach"
TODAY = date.fromisoformat(READ_AT[:10])  # the data was read on this day

# The thresholds are my starting numbers (the plan's Task 15). This script reports a miss and never changes one.
TOP_LEVEL_ROLE_MIN = 0.9
MIDDLE_LEVEL_ROLE_MAX = 0.25
SCORE_GAP_MIN = 25.0
TOP_REASONS_MIN = 3
MIDDLE_REASONS_MAX = 2
LOW_CONFIDENCE_MAX = 0.2
_RISK_MARKS = ("Staffing-firm risk", "Large later-stage")
_MASKS = ((re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
          (re.compile(r"\+?\d[\d ().-]{7,}\d"), "[number]"))


def band(rows, start: int, n: int) -> list:
    """`n` rows from index `start` (fewer at the end of the list, none past it)."""
    return list(rows[start:start + n])


def middle_start(total: int, n: int) -> int:
    """The first index of the `n` rows around the median rank of a list of `total` rows."""
    return max(0, total // 2 - n // 2)


def band_stats(rows) -> dict:
    """The numbers the check asserts on, for rows shaped like `Scored` (lanes, score, reasons, confidence,
    rank_reason, stale_postings)."""
    rows = list(rows)
    n = len(rows)
    confidence = {level: sum(1 for r in rows if r.confidence.level == level) for level in LEVELS}
    bad = sum(1 for r in rows if r.rank_reason.confidence.level not in LEVELS
              or not r.rank_reason.confidence.basis.strip())
    return {
        "count": n,
        "level_role_share": sum(1 for r in rows if "level_role" in r.lanes) / n if n else 0.0,
        "mean_score": sum(r.score for r in rows) / n if n else 0.0,
        "median_reasons": statistics.median(len(r.reasons) for r in rows) if n else 0,
        "confidence": confidence,
        "low_share": confidence["low"] / n if n else 0.0,
        "risk_rows": sum(1 for r in rows if any(m in reason for reason in r.reasons for m in _RISK_MARKS)),
        "bad_reason_rows": bad,
        "stale_ignored": sum(len(r.stale_postings) for r in rows),
    }


def _mask(text: str) -> str:
    for pattern, label in _MASKS:
        text = pattern.sub(label, text)
    return text


def _scratch_store(store_dir: Path | None) -> Path:
    base = (store_dir or SCRATCH_ROOT / datetime.now().strftime("run-%Y%m%d-%H%M%S")).expanduser().resolve()
    live = LIVE_DIR.resolve()
    if base == live or live in base.parents:
        sys.exit(f"refusing the live store folder {base}")
    if not str(base).startswith(str(Path.home() / "Desktop") + "/"):
        sys.exit(f"refusing {base}: the store folder must be under ~/Desktop (default {SCRATCH_ROOT})")
    base.mkdir(parents=True, exist_ok=True)
    path = base / "outreach.db"
    if path.exists():
        sys.exit(f"refusing to reuse {path}: pass a new --store-dir")
    assert not is_live_path(str(path)), f"{path} is the live store"
    return path


def _allowed_ats(conn) -> set[str]:
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'board'").fetchone()[0]
    return set(re.findall(r"'([a-z_]+)'", re.search(r"ats\s+TEXT NOT NULL CHECK \(ats IN \(([^)]*)\)", sql).group(1)))


def _build_inputs(universe: list[dict], jobs: list[dict], keep: list[int], allowed: set[str]):
    """The seed's employer and posting rows for the companies at the `keep` indexes of the universe."""
    employers, board_no, wanted = [], {}, set(keep)
    for i in keep:
        u = universe[i]
        wage = int(float(u["median_jr_wage"])) if u.get("median_jr_wage") not in (None, "") else None
        lca = []
        for fy, jr, level1, newemp, software in (
                (2025, u["jr_software_lcas"], u["level1_lcas"], u["newemp_jr"], u["total_lcas"]),
                (2026, u["fy26_jr"], 0, 0, u["fy26_jr"])):
            row = {"fy": fy, "jr": jr, "level1": level1, "newemp_jr": newemp, "software": software}
            if wage is not None:
                row["median_jr_wage"] = wage
            lca.append(row)
        facts = []
        if u.get("staffing_risk"):
            facts.append({"fact": "staffing_risk", "value": u["staffing_risk"], "confidence": "confirmed"})
        if u.get("everify") == "enrolled":
            facts.append({"fact": "everify", "value": "enrolled", "confidence": "confirmed",
                          "observed_on": READ_AT[:10], "source_kind": "everify"})
        if u["group"] == "lottery_exempt":
            facts.append({"fact": "cap_exempt", "value": "likely", "confidence": "estimate"})
        boards, seen = [], {}
        for ats, slug, _url in u["boards"]:
            if ats in allowed and (ats, slug.lower()) not in seen:
                seen[(ats, slug.lower())] = len(boards)
                boards.append({"read": "ok", "ats": ats})
        board_no[i] = seen
        hq = ", ".join(x for x in (u.get("city"), u.get("state")) if x)
        employers.append({"key": str(i), "name": u["name"], "status": "candidate", "hq": hq, "lca": lca,
                          "facts": facts, "boards": boards})
    postings = []
    for j in jobs:
        i = int(j["company_idx"])
        if i not in wanted:
            continue
        postings.append({
            "employer": str(i), "board": board_no[i].get((j["ats"], j["slug"].lower())),
            "title": j["title"] or "(untitled)", "location": j["location"], "country": j["country"] or None,
            "url": j["url"], "posted_on": j["posted_at"] or None, "pay": j["salary"], "body": j["description"]})
    return employers, postings


def _print_bands(top, middle, total: int, mid_at: int) -> None:
    heads = ((f"TOP {len(top)}", top, 1),
             (f"MIDDLE {len(middle)} (ranks {mid_at + 1} to {mid_at + len(middle)} of {total})", middle, mid_at + 1))
    for title, rows, first in heads:
        print(f"\n=== {title} ===")
        for n, s in enumerate(rows, first):
            lanes = ", ".join(s.lanes) if s.lanes else "no lane"
            print(f"{n:>5}. {_mask(s.name)} | {s.score:g} | {lanes} | {_mask(s.rank_reason.line)} "
                  f"| stale ignored {len(s.stale_postings)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tests.ranking_realcheck", description=__doc__.split("\n")[0])
    ap.add_argument("--universe", type=Path, default=INTERIM / "universe.json")
    ap.add_argument("--jobs", type=Path, default=INTERIM / "jobs.json")
    ap.add_argument("--sample", type=int, default=None, metavar="N",
                    help="rank a seeded random sample of N universe rows (and their jobs) instead of all")
    ap.add_argument("--store-dir", type=Path, default=None,
                    help=f"a new folder for the scratch store (default: a fresh one under {SCRATCH_ROOT})")
    args = ap.parse_args(argv)

    t0 = time.monotonic()
    path = _scratch_store(args.store_dir)
    universe = json.loads(args.universe.read_text(encoding="utf-8"))
    keep = list(range(len(universe)))
    if args.sample is not None:
        keep = sorted(random.Random(0).sample(keep, min(args.sample, len(keep))))
    jobs = json.loads(args.jobs.read_text(encoding="utf-8"))
    install(str(path))
    conn = connect(str(path))
    employers, postings = _build_inputs(universe, jobs, keep, _allowed_ats(conn))
    del jobs, universe
    work = path.parent
    (work / "employers.json").write_text(json.dumps(employers), encoding="utf-8")
    (work / "postings.json").write_text(json.dumps(postings), encoding="utf-8")
    print(f"store {path}\ncompanies {len(employers)}, postings {len(postings)}", flush=True)
    t1 = time.monotonic()
    seed_ranking_fixture(conn, work / "employers.json", work / "postings.json")
    t2 = time.monotonic()
    run = run_rank(conn, today=TODAY)
    t3 = time.monotonic()
    print(f"seconds: read and build {t1 - t0:.1f}, seed and rebuild {t2 - t1:.1f}, rank {t3 - t2:.1f}, "
          f"total {t3 - t0:.1f}")

    loaded = len(employers)
    ordered = list_order(run.scored)
    top = band(ordered, 0, BAND)
    mid_at = middle_start(len(ordered), BAND)
    middle = band(ordered, mid_at, BAND)
    _print_bands(top, middle, len(ordered), mid_at)
    st, sm = band_stats(top), band_stats(middle)

    stored = ranked_list(conn)
    hidden = hidden_list(conn)
    stale = load_stale(conn, TODAY)
    level_role_on_stale = [s.name for s in run.scored if "level_role" in s.lanes and s.best_posting_id in stale]
    top_on_stale = [s.name for s in top if s.best_posting_id in stale]
    no_reason = [h.company_id for h in hidden if not (h.signals.get("hide") or {}).get("reason")]
    ids = [r.company_id for r in stored]
    counts = {level: sum(1 for s in ordered if s.confidence.level == level) for level in LEVELS}

    print(f"\nlist {len(ordered)} ranked, {len(hidden)} hidden, {loaded} loaded; hidden by kind {run.hidden}")
    print(f"stale postings in the store {len(stale)}; ignored in ranking as not live {run.stale_postings}; "
          f"ignored as a doubtful board {run.ignored_postings}")
    print(f"rank confidence over the whole list {counts}; top {st['confidence']}; middle {sm['confidence']}")
    print(f"top: level_role {st['level_role_share']:.3f}, mean {st['mean_score']:.1f}, median reasons "
          f"{st['median_reasons']}, low {st['low_share']:.3f}")
    print(f"middle: level_role {sm['level_role_share']:.3f}, mean {sm['mean_score']:.1f}, median reasons "
          f"{sm['median_reasons']}, low {sm['low_share']:.3f}")

    gap = st["mean_score"] - sm["mean_score"]
    bad = st["bad_reason_rows"] + sm["bad_reason_rows"]
    checks = [
        ("top 50 level_role share >= 0.9", st["level_role_share"], st["level_role_share"] >= TOP_LEVEL_ROLE_MIN),
        ("middle level_role share <= 0.25", sm["level_role_share"], sm["level_role_share"] <= MIDDLE_LEVEL_ROLE_MAX),
        ("mean score gap (top minus middle) >= 25", gap, gap >= SCORE_GAP_MIN),
        ("top median reasons >= 3", st["median_reasons"], st["median_reasons"] >= TOP_REASONS_MIN),
        ("middle median reasons <= 2", sm["median_reasons"], sm["median_reasons"] <= MIDDLE_REASONS_MAX),
        ("top 50 companies with a staffing-risk or large-later-stage reason == 0", st["risk_rows"],
         st["risk_rows"] == 0),
        ("hidden entries with no reason == 0", len(no_reason), not no_reason),
        ("duplicate company ids in the list == 0", len(ids) - len(set(ids)), len(ids) == len(set(ids))),
        ("ranked + hidden minus companies loaded == 0", len(ids) + len(hidden) - loaded,
         len(ids) + len(hidden) == loaded),
        ("level_role companies resting on a stale best posting == 0", len(level_role_on_stale),
         not level_role_on_stale),
        ("top 50 companies with role credit from a stale posting == 0", len(top_on_stale), not top_on_stale),
        ("stale postings found > 0", len(stale), len(stale) > 0),
        ("band rows without a rank-reason level and basis == 0", bad, bad == 0),
        ("top 50 low rank-confidence share <= 0.2", st["low_share"], st["low_share"] <= LOW_CONFIDENCE_MAX),
    ]
    print("\nChecks")
    failed = 0
    for label, value, ok in checks:
        failed += not ok
        shown = f"{value:.3f}" if isinstance(value, float) else value
        print(f"  {'PASS' if ok else 'MISS'}  {label}: {shown}")
    conn.close()
    print(f"\n{'all checks passed' if not failed else f'{failed} check(s) missed; no threshold was changed'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
