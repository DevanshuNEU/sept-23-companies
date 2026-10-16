"""
Mark companies that opened new-grad roles in October or November of past years (refresh/work/research/seasonal.json).

Tracked companies get a `seasonal` field. Untracked small companies with nothing open right now are added with
their board, so the twice-weekly refresh notices the day they post; untracked ones that do have an open role
are imported by discover_extra.py first and then marked here. Idempotent: safe to run every refresh.

Run from the project root, after discover_extra.py:  python3 refresh/apply_seasonal.py
"""
import json, re, os, sys, sqlite3, subprocess, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jsedit import set_field
from discover_radar import BIG, LCA, DATA, lca_lookup
from discover_extra import key, clean_name, SKIP_LEAD
from jsedit import remove_entry

SEASON_SKIP = set()      # hand calls live in discover_extra.SKIP_LEAD, shared by both importers

SRC = "refresh/work/research/seasonal.json"
TODAY = datetime.date.today().isoformat()
MON = {10: "Oct", 11: "Nov"}
BOARD = {"ashby": "https://jobs.ashbyhq.com/{}", "greenhouse": "https://job-boards.greenhouse.io/{}", "lever": "https://jobs.lever.co/{}"}

def field(it):
    h = sorted(it.get("history") or [], key=lambda x: (x.get("year", 0), x.get("month", 0)))
    if not h: return None
    last = h[-1]
    ats, tok = (it.get("ats") or "").lower(), it.get("token")
    return {"title": re.sub(r"\s+-\s+[^-]*$", "", last.get("title") or "New grad software engineer"),
            "last": f'{MON.get(last.get("month"), "Fall")} {last.get("year")}',
            "years": sorted({x["year"] for x in h if x.get("year")}), "repeat": bool(it.get("repeat")),
            "url": last.get("source"), "board": BOARD[ats].format(tok) if ats in BOARD and tok else it.get("careersUrl")}

def main():
    if not os.path.exists(SRC): print("no seasonal research"); return
    items = json.load(open(SRC))
    rows = json.loads(subprocess.run(["node", "-e", """global.window={};eval(require('fs').readFileSync('dashboard/data.js','utf8'));
        console.log(JSON.stringify(window.COMPANIES.map(c=>[c.id,c.name,c.board||null])))"""], capture_output=True, text=True, check=True).stdout)
    by_name = {key(n): cid for cid, n, _ in rows}
    by_tok = {(b["ats"], b["token"].lower()): cid for cid, _, b in rows if b}
    lca = sqlite3.connect(f"file:{LCA}?mode=ro", uri=True)
    src = open(DATA, encoding="utf-8").read()
    marked, added, skipped = 0, [], 0
    for it in items:
        f = field(it)
        name = clean_name(it.get("company") or "")
        if not f or not name: skipped += 1; continue
        if key(name) in SEASON_SKIP or key(name) in SKIP_LEAD:
            gone = by_name.get(key(name))
            if gone and gone.startswith("ss-"): src = remove_entry(src, gone)     # added by an earlier run
            skipped += 1; continue
        cid = by_name.get(key(name)) or by_tok.get(((it.get("ats") or "").lower(), (it.get("token") or "").lower()))
        if cid:
            src = set_field(src, cid, "seasonal", f); marked += 1; continue
        if BIG.search(name): skipped += 1; continue
        lk = lca_lookup(lca, name, set())
        if lk and lk.get("verified") and lk["n"] > 30: skipped += 1; continue
        ats, tok = (it.get("ats") or "").lower(), it.get("token")
        entry = {"id": "ss-" + re.sub(r"[^a-z0-9]", "", name.lower())[:26], "name": name, "cat": "Software engineering", "hq": "",
                 "addedOn": TODAY, "addedBy": "Hiring history", "sizeClass": "small",
                 "sizeWhy": (f'{lk["n"]} lifetime visa filings, consistent with under 500' if lk and lk.get("verified")
                             else "no filing record: most likely an early startup") + (f'; source estimate {it["headcount"]}' if it.get("headcount") not in (None, "", "unknown") else ""),
                 "apparatus": lk["n"] if lk and lk.get("verified") else None, "reqs": [], "noRoleInBand": True,
                 "visa": "Not reviewed yet. No sponsorship statement has been read for this company.", "blockers": [],
                 "seasonal": f, "note": f"Opened new-grad roles in {', '.join(map(str, f['years']))} around October or November. Nothing open yet."}
        if ats in BOARD and tok: entry["board"] = {"ats": ats, "token": tok}
        added.append(entry)
    if added:
        i = src.index("window.RULED_OUT"); j = src.rindex("];", 0, i)
        lit = lambda a: "{ " + ", ".join(f"{k}:{json.dumps(v, ensure_ascii=True)}" for k, v in a.items()) + "}"
        head = src[:j].rstrip()
        if head.endswith(","): head = head[:-1]
        src = head + ",\n" + ",\n".join("  " + lit(a) for a in added) + "\n" + src[j:]
    open(DATA, "w", encoding="utf-8").write(src)
    print(f"seasonal: marked {marked} tracked, added {len(added)} with nothing open yet, skipped {skipped}")

if __name__ == "__main__":
    main()
