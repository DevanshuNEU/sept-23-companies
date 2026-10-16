"""
Write E-Verify lookups (refresh/work/everify_results.json) into dashboard/data.js as c.everify.

Only clear answers are written: enrolled (an Open record tied to this company), none, or terminated (stored as
none, with the note). Ambiguous results are left off the dashboard. A hand-checked entry already on the
dashboard is kept unless the new lookup says enrolled for the same entity.

Run from the project root:  python3 refresh/apply_everify.py
"""
import json, re, subprocess, sys, os, shutil, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jsedit import set_field

DATA, RES = "dashboard/data.js", "refresh/work/everify_results.json"
key = lambda s: re.sub(r"[^a-z0-9]", "", re.sub(r"\s*\(.*?\)", "", (s or "").lower()))

def main():
    if not os.path.exists(RES): print("no E-Verify results"); return
    res = json.load(open(RES))
    rows = json.loads(subprocess.run(["node", "-e", """global.window={};eval(require('fs').readFileSync('dashboard/data.js','utf8'));
        console.log(JSON.stringify(window.COMPANIES.map(c=>[c.id,c.name,c.everify||null])))"""],
        capture_output=True, text=True, check=True).stdout)
    by_id = {r[0]: r for r in rows}
    by_name = {key(r[1]): r for r in rows}
    shutil.copy2(DATA, f"refresh/backups/data_{datetime.datetime.now():%Y-%m-%d_%H%M}_pre_everify.js")
    src = open(DATA, encoding="utf-8").read()
    n = {"enrolled": 0, "none": 0, "skipped": 0}
    for rid, r in res.items():
        row = by_id.get(rid) or by_name.get(key(rid.split(":", 1)[-1])) or by_name.get(key(r.get("name")))
        st = r.get("status")
        if not row or st not in ("enrolled", "none", "terminated"): n["skipped"] += 1; continue
        cid, _, have = row
        if have and have.get("checked") and have.get("checked") < "2026-10-07" and st != "enrolled": n["skipped"] += 1; continue
        ev = {"status": "enrolled" if st == "enrolled" else "none", "entity": r.get("entity"), "enrolled": r.get("enrolled"),
              "sites": r.get("sites"), "plus": r.get("plus"), "checked": r.get("checked") or datetime.date.today().isoformat(),
              "note": ("Only terminated records found. " if st == "terminated" else "") + (r.get("note") or "")}
        if st != "enrolled":
            ev["searched"] = (r.get("searched") or [])[:6]
        src = set_field(src, cid, "everify", {k: v for k, v in ev.items() if v not in (None, "")})
        n["enrolled" if st == "enrolled" else "none"] += 1
    open(DATA, "w", encoding="utf-8").write(src)
    chk = subprocess.run(["node", "-e", "global.window={};eval(require('fs').readFileSync('dashboard/data.js','utf8'));"
                          "if(window.COMPANIES.some(c=>!c||!c.id))throw 1;console.log(window.COMPANIES.length)"], capture_output=True, text=True)
    if chk.returncode: raise SystemExit("data.js no longer loads; restore the pre_everify backup")
    print(f"E-Verify written: {n}; {chk.stdout.strip()} companies")

if __name__ == "__main__":
    main()
