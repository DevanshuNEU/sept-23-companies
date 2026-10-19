"""
Twice-weekly refresh of the outreach dashboard (Mon and Thu, 8am, via launchd).

  1. back up dashboard/data.js
  2. add new small companies from the Job Radar (read-only) and from any research files in refresh/work/research/
  3. re-read every tracked company's live postings: open or closed, real dates, level, graduation window,
     citizenship, 2027 starts
  4. write the results into data.js; hand-checked sponsorship verdicts are never overwritten
  5. refuse to publish anything that does not load; restore the backup instead
  6. publish to localhost:3001 and write refresh/LAST_RUN.md

Run by hand from the project root:  python3 refresh/refresh.py
"""
import json, os, shutil, subprocess, sys, datetime, glob, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
NOW = datetime.datetime.now()
STAMP = NOW.strftime("%Y-%m-%d_%H%M")
DATA = "dashboard/data.js"
W = "refresh/work/"

def log(msg): print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)
def run(cmd, timeout=900):
    log("$ " + " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    tail = (r.stdout.strip().splitlines() or [""])[-1]
    if tail: log("  " + tail)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[1] if len(cmd) > 1 else cmd[0]} failed:\n{r.stderr[-1500:]}")
    return r.stdout
def snapshot():
    js = subprocess.run(["node", "-e", """global.window={};eval(require('fs').readFileSync('dashboard/data.js','utf8'));
      const C=window.COMPANIES; if(C.some(c=>!c||!c.id||!c.name)) throw new Error('hole or nameless entry');
      console.log(JSON.stringify(Object.fromEntries(C.map(c=>[c.id,{n:c.name,none:!!c.noRoleInBand,
        req:(c.level&&c.level.entryReq)||null,tier:(c.level&&c.level.entryTier)||null,
        urls:(c.reqs||[]).map(r=>r.u),yes:!!c.visaConfirmed,no:!!c.visaDenied,added:c.addedOn||null,by:c.addedBy||null}]))))"""],
      capture_output=True, text=True)
    if js.returncode != 0: raise RuntimeError("data.js does not load: " + js.stderr[-600:])
    return json.loads(js.stdout)

def main():
    sys.path.insert(0, ROOT); import live_checkout
    live_checkout.require_live("refresh", ROOT)        # never crawl or publish from a worktree
    os.makedirs(W, exist_ok=True); os.makedirs("refresh/backups", exist_ok=True)
    backup = f"refresh/backups/data_{STAMP}.js"
    shutil.copy2(DATA, backup)
    for old in sorted(glob.glob("refresh/backups/data_*.js"))[:-8]: os.remove(old)     # keep the last 8
    before = snapshot()
    log(f"backup {backup}; {len(before)} companies before")

    try:
        run([sys.executable, "refresh/discover_radar.py"])
        run([sys.executable, "refresh/crawl_boards.py", "--days", "45"], timeout=1800)   # ~2,300 startup boards
        run([sys.executable, "refresh/crawl_more.py", "--days", "45", "--skip", "dover"], timeout=3600)   # ~34,000 more boards, 8 more ATSes
        run([sys.executable, "refresh/discover_extra.py"])      # hand research in refresh/work/research/, if any
        run([sys.executable, "refresh/apply_seasonal.py"])      # companies that hired new grads last Oct/Nov
        run(["node", "refresh/build_targets.js"])
        run([sys.executable, "refresh/finalcheck.py"])
        run([sys.executable, "refresh/eligibility.py"])
        run([sys.executable, "refresh/final_merge.py"])
        run(["node", "refresh/write_level.js"])
        # stamp the run so the dashboard can say how fresh it is
        src = open(DATA, encoding="utf-8").read()
        src = re.sub(r"\nwindow\.META = \{.*?\};\n", "\n", src, flags=re.S)
        src += f"\nwindow.META = {json.dumps({'refreshedAt': NOW.isoformat(timespec='minutes')})};\n"
        open(DATA, "w", encoding="utf-8").write(src)
        after = snapshot()                                   # raises if the new file does not load
    except Exception as e:
        shutil.copy2(backup, DATA)
        log(f"FAILED, restored {backup}: {e}")
        write_report(None, None, error=str(e)); raise

    # new ?v= so the browser fetches the fresh data.js instead of a cached one
    html = open("dashboard/index.html", encoding="utf-8").read()
    open("dashboard/index.html", "w", encoding="utf-8").write(re.sub(r"\?v=\d+", f"?v={int(NOW.timestamp())}", html))
    run(["bash", "dashboard/publish.sh"])
    write_report(before, after)
    log("done")

def write_report(before, after, error=None):
    lines = [f"# Refresh, {NOW:%a %b %d %Y, %H:%M}", ""]
    if error:
        lines += ["**Failed. The dashboard was left exactly as it was.**", "", "```", error[-1500:], "```"]
        open("refresh/LAST_RUN.md", "w").write("\n".join(lines) + "\n"); return
    added  = [f'{v["n"]} ({v.get("by") or "?"})' for k, v in after.items() if k not in before]
    closed = [v["n"] for k, v in after.items() if k in before and not before[k]["none"] and v["none"]]
    back   = [v["n"] for k, v in after.items() if k in before and before[k]["none"] and not v["none"]]
    newreq = [f'{v["n"]}: {v["req"]}' for k, v in after.items()
              if k in before and not v["none"] and v["req"] and v["req"] != before[k]["req"]]
    # sponsorship wording found on companies with no reviewed verdict: listed for a human, never applied
    review = []
    try:
        fc = json.load(open(W + "finalcheck.json")); names = {k: v["n"] for k, v in after.items()}
        for cid, v in fc.items():
            if cid not in after or after[cid]["yes"] or after[cid]["no"]: continue
            for j in v["jobs"]:
                sn = (j.get("snip") or {})
                q = sn.get("noSponsor") or sn.get("yesSponsor")
                if q:
                    review.append(f'{names.get(cid, cid)}: {"no" if sn.get("noSponsor") else "yes"}? "{q[:180]}"'); break
    except Exception as e:
        review.append(f"(could not build the review list: {e})")
    # boards the last run could not read, or read empty once: kept as they were, listed for a human
    try:
        sys.path.insert(0, os.path.join(ROOT, "refresh")); import board_status
        in_run = json.load(open(W + "finalcheck.json"))
        state = {k: v for k, v in board_status.load(W + "board_status.json").items() if k in in_run}
        unread = board_status.report_lines(state, {k: v["n"] for k, v in after.items()})
    except Exception as e:
        unread = ["## Boards that could not be read this run", "", f"(could not build this list: {e})", ""]
    def section(title, items, empty):
        return [f"## {title} ({len(items)})", ""] + ([f"- {x}" for x in items] if items else [empty]) + [""]
    lines += [f"{len(after)} companies tracked. Backup kept in refresh/backups/.", ""]
    lines += section("New companies", added, "None this run.")
    lines += section("Nothing open for you any more", closed, "None.")
    lines += section("Open again", back, "None.")
    lines += section("Role to name changed", newreq, "None.")
    lines += unread
    lines += section("Sponsorship wording to review by hand (not applied)", review, "Nothing new.")
    open("refresh/LAST_RUN.md", "w").write("\n".join(lines) + "\n")

if __name__ == "__main__":
    main()
