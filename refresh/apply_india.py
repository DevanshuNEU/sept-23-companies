"""
Write India-connection facts (refresh/work/india_results.json) onto tracked companies as c.india.

Only stated, public facts are recorded: a founder who studied or worked in India, or an engineering team or
office in India. Nothing is inferred from names. Run from the project root:  python3 refresh/apply_india.py
"""
import json, os, sys, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jsedit import set_field

RES, DATA = "refresh/work/india_results.json", "dashboard/data.js"

def main():
    if not os.path.exists(RES): print("no India results"); return
    res = json.load(open(RES))
    ids = set(json.loads(subprocess.run(["node", "-e", "global.window={};eval(require('fs').readFileSync('dashboard/data.js','utf8'));"
                                         "console.log(JSON.stringify(window.COMPANIES.map(c=>c.id)))"], capture_output=True, text=True, check=True).stdout))
    src = open(DATA, encoding="utf-8").read(); n = 0
    for cid, r in res.items():
        if cid not in ids or not isinstance(r, dict): continue
        v = {k: r.get(k) for k in ("founderIndia", "founderNote", "indiaOffice", "officeNote") if r.get(k) not in (None, "")}
        v["checked"] = r.get("checked") or "2026-10-15"
        src = set_field(src, cid, "india", v); n += 1
    open(DATA, "w", encoding="utf-8").write(src)
    ok = subprocess.run(["node", "-e", "global.window={};eval(require('fs').readFileSync('dashboard/data.js','utf8'));"
                         "if(window.COMPANIES.some(c=>!c||!c.id))throw 1"], capture_output=True)
    if ok.returncode: raise SystemExit("data.js no longer loads after apply_india; restore the latest backup")
    print(f"india: wrote {n}")

if __name__ == "__main__":
    main()
