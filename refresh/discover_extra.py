"""
New companies found by hand research (HN Who's Hiring, GitHub new-grad lists, YC, funding news, Reddit).

Reads the research outputs (refresh/work/research/{hn,github,yc,funding_reddit}.json, plus any leads_*.json), merges them by company, and adds a
company only if: it is not already tracked, it is not a known large company, its visa-data size check says
under ~500, and its board answers right now with at least one in-lane US engineering role. Leads with no
ATS board are kept only if the posting URL itself loads. Sponsorship claims from the sources are written
as unverified notes, never as verdicts. Level, graduation window and citizenship are then checked by the
normal refresh sweep (refresh/refresh.py).

Run from the project root:  python3 refresh/discover_extra.py
"""
import json, re, sqlite3, glob, os, sys, datetime, urllib.request, urllib.error, concurrent.futures as cf
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from discover_radar import BIG, SKIP_BOARDS, UNSURE, ENG, SEN, INT, OFF, NONUS, LCA, USCIS, DATA, tracked, lca_lookup, states_of

TODAY = datetime.date.today().isoformat()
clean_name = lambda s: re.sub(r"\s*\((?:YC|yc)\b[^)]*\)", "", s or "").strip()
key = lambda s: (lambda s: re.sub(r"(inc|llc|ai|labs|hq|technologies)$", "", re.sub(r"[^a-z0-9]", "", s.lower())) or s.lower())(clean_name(s))
# Hand calls on research leads: dropped outright, or kept with size marked unsure.
SKIP_LEAD = {"fiverings": "trading firm, not a startup engineering team",
             "giftogram": "not a venture-backed tech startup", "theanywherecompany": "not a venture-backed tech startup",
             "serpapi": "junior role is contractor-only outside Austin, which does not work for STEM OPT",
             # 2026-10-08 hiring-history list: over the size cap, trading firms, consultancies, staffing or gov services
             **{key(n): "hand call: over the cap, a trading firm, or a consultancy/staffing shop" for n in (
                 "Cambridge Mobile Telematics|ExtraHop|Vermont Information Processing|SandboxAQ|Equip Health|"
                 "Boerboel Trading|Sunwater Capital|Old Mission Capital|Cook Systems|Codazen|Blueprint Technologies|FocusKPI|"
                 "Adswerve|Harmonia Holdings|PVM|TalentWerx|MLabs|Trend Health Partners").split("|")},
             "notability": "same company as Notability (Ginger Labs), already tracked",
             # 2026-10-15 expanded crawl, pruned by hand
             **{key(n): 'over 500 or a large company' for n in ['Mozilla', 'Precisely US Jobs', 'Base Power', 'Thought Machine', 'M3USA', 'Rockstar', 'Circle Logistics', 'Tiger Analytics Inc.']},
             **{key(n): 'consultancy, agency or services firm' for n in ['66degrees', 'FormativGroup', 'Robots and Pencils', 'Praxent', 'Datavations', 'Indicium AI', 'Sand Tech Holdings Limited', 'Aimpoint Digital', 'RHONDOS Inc', 'Bolder Apps', 'bluprintx']},
             **{key(n): 'staffing, recruiting or job board' for n in ['GoFasti', 'Raydar', 'United Placement Group', 'Continuum Resource Network', 'OpenDataJobs']},
             **{key(n): 'government contractor' for n in ['Ivyhill Technologies LLC', 'i360technologies, Inc.', 'Zantech', 'Ignite IT', 'Link Solutions, Inc.']},
             **{key(n): 'not a tech startup (investment firm, publisher, logistics, healthcare operator, foreign parent)' for n in ['forum-ventures', 'BIP Capital', 'Tax Analysts', 'BEST Logistics Group', 'ramhealth', 'CWILL', 'Giga Energy']},
             **{key(n): "government contractor or over 500 people (2026-10-15 crawl)" for n in
                ("Ivyhill Technologies", "TechSur Solutions", "Prosync", "iFIT")},
             "vvd": "remote role is EU/North America time zones, US eligibility not stated",
             # from the 2026-10-08 board crawl
             'amplitude': 'public or well over 500 people',
             'aurorainnovation': 'public or well over 500 people',
             'poshmark': 'public or well over 500 people',
             'whoop': 'public or well over 500 people',
             'xero': 'public or well over 500 people',
             'applovin': 'public or well over 500 people',
             'esri': 'public or well over 500 people',
             'newrelic': 'public or well over 500 people',
             'nice': 'public or well over 500 people',
             'nuro': 'public or well over 500 people',
             'yext': 'public or well over 500 people',
             'appen': 'public or well over 500 people',
             'pointclickcare': 'public or well over 500 people',
             'oppfi': 'public or well over 500 people',
             'olo': 'public or well over 500 people',
             'impactcom': 'public or well over 500 people',
             'thinkcell': 'public or well over 500 people',
             'blend': 'public or well over 500 people',
             'opengov': 'public or well over 500 people',
             'icapital': 'public or well over 500 people',
             'hpiq': 'public or well over 500 people',
             'rivianvwtech': 'public or well over 500 people',
             'clearcorporate': 'public or well over 500 people',
             'lightcast': 'public or well over 500 people',
             'fin': 'public or well over 500 people',
             'saronic': 'public or well over 500 people',
             'shield': 'public or well over 500 people',
             'grahamcapitalmanagementlp': 'trading or investment firm, not a startup engineering team',
             'voloridgeinvestmentmanagement': 'trading or investment firm, not a startup engineering team',
             'racapitalmanagement': 'trading or investment firm, not a startup engineering team',
             'myfundedfutures': 'trading or investment firm, not a startup engineering team',
             'mthreerecruitingportal': 'staffing, consulting or government-services firm',
             'sanmarinternal': 'staffing, consulting or government-services firm',
             'healthservicesadvisorygroup': 'staffing, consulting or government-services firm',
             'redhorse': 'staffing, consulting or government-services firm',
             'guidepointsecurity': 'staffing, consulting or government-services firm',
             'sparksoftcorporation': 'staffing, consulting or government-services firm',
             'nisc': 'staffing, consulting or government-services firm',
             'kobie': 'staffing, consulting or government-services firm',
             'grvty': 'staffing, consulting or government-services firm',
             'clera': 'staffing, consulting or government-services firm',
             'truezero': 'staffing, consulting or government-services firm'}
SERVICES = re.compile(r"\b(staffing|placement|recruit\w*|talent (group|partners|solutions)|consult\w*|resource network|"
                      r"jobs|careers? portal|it services|federal|government solutions)\b", re.I)
UNSURE_LEAD = re.compile(r"^(clay|provectus|trm labs)\b", re.I)
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
LABEL = {"hn": "Hacker News", "github": "GitHub new-grad list", "yc": "Y Combinator", "funding_reddit": "Funding news / Reddit",
         "leads_yc_optonly": "Y Combinator (OPT only)", "seasonal": "Hiring history",
         "leads_builtin": "Built In", "leads_ats_crawl": "Job board crawl",
         "leads_vcboards": "VC portfolio job board", "leads_delta_lists": "New-grad lists, HN, YC",
         "leads_delta_news": "Built In / funding news", "leads_ats_more": "Job board crawl",
         "leads_india": "India-connected startups"}

def get(u, t=25):
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t) as f:
            return f.status, f.read(30_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e: return e.code, ""
    except Exception: return 0, ""

def board_titles(ats, token):
    """(title, location, url) for every posting on the board, or None if the board does not answer."""
    if ats == "ashby":
        st, d = get(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
        if st != 200: return None
        return [(j.get("title", ""), j.get("location", ""), j.get("jobUrl", "")) for j in json.loads(d).get("jobs", [])]
    if ats == "greenhouse":
        st, d = get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
        if st != 200: return None
        return [(j.get("title", ""), (j.get("location") or {}).get("name", ""), j.get("absolute_url", ""))
                for j in json.loads(d).get("jobs", [])]
    if ats == "lever":
        st, d = get(f"https://api.lever.co/v0/postings/{token}?mode=json")
        if st != 200: return None
        return [(j.get("text", ""), (j.get("categories") or {}).get("location", ""), j.get("hostedUrl", "")) for j in json.loads(d)]
    return None

def in_lane(title, loc):
    t = re.sub(r"member of (technical|product|engineering) staff", "MTS", title or "", flags=re.I)
    if not ENG.search(title or "") or SEN.search(t) or INT.search(title or "") or OFF.search(title or ""): return False
    if loc and NONUS.search(loc) and not re.search(r"(united states|\bUS\b|USA|remote)", loc, re.I): return False
    return True

def load():
    merged = {}
    files = sorted(set([f"refresh/work/research/{n}.json" for n in LABEL] + glob.glob("refresh/work/research/leads_*.json")))
    for f in [f for f in files if os.path.exists(f)]:
        src = os.path.basename(f)[:-5]
        try: items = json.load(open(f))
        except Exception as e: print(f"  could not read {f}: {e}"); continue
        for it in items if isinstance(items, list) else []:
            if not it.get("company"): continue
            it = {**it, "company": clean_name(it["company"])}
            m = merged.setdefault(key(it["company"]), {**it, "roles": [], "sources": [], "srcfiles": set()})
            if not m.get("token") and it.get("token"): m["ats"], m["token"] = it.get("ats"), it["token"]
            for k in ("stage", "headcount", "careersUrl"):
                if (not m.get(k) or m.get(k) == "unknown") and it.get(k): m[k] = it[k]
            if it.get("india") and not m.get("india"): m["india"] = it["india"]
            if it.get("optOnly"): m["optOnly"] = True
            elif str(it.get("visa", "")).lower().startswith("no"): m["visaNo"] = it.get("visa")
            elif str(it.get("visa", "")).lower().startswith("yes") and not m.get("visaYes"): m["visaYes"] = it.get("visa")
            seen = {r["url"] for r in m["roles"]}
            m["roles"] += [r for r in it.get("roles", []) if r.get("url") and r["url"] not in seen]
            if it.get("source"): m["sources"].append(it["source"])
            m["srcfiles"].add(src)
    return merged

def check(c):
    """Returns (company, live roles, reason-if-dropped)."""
    ats, tok = (c.get("ats") or "").lower(), c.get("token")
    if ats in ("ashby", "greenhouse", "lever") and tok:
        try: rows = board_titles(ats, tok)
        except Exception: rows = None
        if rows is None: return c, [], "board did not answer"
        live = [{"t": (t + (" - " + l if l else ""))[:100], "u": u, "d": None, "loc": l} for t, l, u in rows if in_lane(t, l)]
        posted = {r["url"]: r.get("posted") for r in c["roles"]}
        for r in live: r["d"] = posted.get(r["u"])
        return c, live, None if live else "no in-lane US engineering role open on the board"
    live = []
    for r in c["roles"][:6]:
        if not in_lane(r.get("title", ""), r.get("location", "")): continue
        st, body = get(r["url"])
        if st in (404, 410) or re.search(r"no longer (accepting|available|open)|position (has been )?filled|job not found", body[:5000], re.I):
            continue
        live.append({"t": (r["title"] + (" - " + r["location"] if r.get("location") else ""))[:100], "u": r["url"],
                     "d": r.get("posted"), "loc": r.get("location", "")})
    return c, live, None if live else "posting page gone or not an in-lane role"

def main():
    merged = load()
    names, tokens = tracked()
    tracked_keys = {key(n) for n in names}
    lca = sqlite3.connect(f"file:{LCA}?mode=ro", uri=True)
    usc = sqlite3.connect(f"file:{USCIS}?mode=ro", uri=True)
    drop, todo, seen_tok = {}, [], set()
    for k, c in merged.items():
        tok = ((c.get("ats") or "").lower(), (c.get("token") or "").lower())
        if k in tracked_keys or tok in tokens:     drop[c["company"]] = "already tracked"; continue
        if BIG.search(c["company"].strip()):      drop[c["company"]] = "large company"; continue
        if tok in SKIP_BOARDS:                     drop[c["company"]] = "board belongs to a different company"; continue
        if tok[1] and tok in seen_tok:             drop[c["company"]] = "same board as another lead"; continue
        if tok[1]: seen_tok.add(tok)
        if k in SKIP_LEAD:                         drop[c["company"]] = SKIP_LEAD[k]; continue
        if SERVICES.search(c["company"]):         drop[c["company"]] = "staffing, recruiting or consulting firm by name"; continue
        if c.get("visaNo") and not c.get("optOnly"):                      drop[c["company"]] = f"source says no sponsorship: {c['visaNo'][:80]}"; continue
        todo.append(c)
    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        checked = list(ex.map(check, todo))
    added = []
    for c, live, why_not in checked:
        if why_not: drop[c["company"]] = why_not; continue
        lk = lca_lookup(lca, c["company"], states_of([r["loc"] for r in live]))
        if lk and lk.get("collision"):
            n, size, why = None, "small", "the visa-data name match pointed at an unrelated employer, so size is unknown"
        elif lk and lk.get("verified"):
            n = lk["n"]
            if n > 30: drop[c["company"]] = f"{n} visa filings, likely over 500 people"; continue
            size, why = "small", f"{n} lifetime visa filings, consistent with under 500"
        else:
            n, size, why = None, "small", "no filing record: most likely an early startup"
        if c.get("headcount") and c["headcount"] != "unknown": why += f"; source estimate {c['headcount']}"
        if UNSURE.search(c["company"]) or UNSURE_LEAD.search(c["company"]): size, why = "unsure", "size genuinely uncertain, somewhere around 300 to 700"
        um = {}
        if lk and lk.get("key"):
            um = {f: (x or 0) for f, x in usc.execute(
                "select fy, new_approval from uscis where employer_norm=? and fy in (2024,2025,2026)", (lk["key"],))}
        by = " + ".join(LABEL.get(s, s) for s in sorted(c["srcfiles"]))
        visa = "Not reviewed yet. No sponsorship statement has been read for this company."
        if c.get("optOnly"):
            visa = ("Their YC listing says \u201cUS citizen/visa only\u201d: they will not sponsor, but OPT counts as authorized today. "
                    "STEM OPT later needs them enrolled in E-Verify.")
        elif c.get("visaYes"): visa = f"Unverified: {by} reports sponsorship ({str(c['visaYes'])[:140]}). Not yet read on their own posting."
        entry = {"id": "rx-" + (re.sub(r"[^a-z0-9]", "", c["company"].lower())[:26]), "name": c["company"],
                 "cat": "Software engineering", "hq": next((r["loc"] for r in live if r["loc"]), ""),
                 "addedOn": TODAY, "addedBy": by, "sizeClass": size, "sizeWhy": why, "apparatus": n,
                 "odds": (lk or {}).get("odds"), "mix": (lk or {}).get("mix", [0, 0, 0, 0]),
                 "uscis": [um.get(2024, 0), um.get(2025, 0), um.get(2026, 0)], "trend": "none", "lcaTrend": "none",
                 "reqs": [{"t": r["t"], "u": r["u"], "d": r["d"]} for r in live[:10]], "visa": visa, "blockers": [],
                 **({"optOnly": True} if c.get("optOnly") else {}),
                 **({"india": {k: c["india"].get(k) for k in ("founderIndia", "founderNote", "indiaOffice", "officeNote")}} if c.get("india") else {}),
                 "note": f"Found {TODAY} via {by}." + (f" Stage: {c['stage']}." if c.get("stage") and c["stage"] != "unknown" else "")
                         + (f" Source: {c['sources'][0]}" if c["sources"] else "")}
        # an HN lead with no posting date takes the date of the HN comment that announced it
        hn_id = next((m.group(1) for m in (re.search(r"news\.ycombinator\.com/item\?id=(\d+)", x) for x in c["sources"]) if m), None)
        if hn_id and any(not r["d"] for r in entry["reqs"]):
            st, body = get(f"https://hn.algolia.com/api/v1/items/{hn_id}")
            day = (json.loads(body).get("created_at") or "")[:10] if st == 200 else ""
            for r in entry["reqs"]: r["d"] = r["d"] or day or None
        if (c.get("ats") or "").lower() in ("ashby", "greenhouse", "lever") and c.get("token"):
            entry["board"] = {"ats": c["ats"].lower(), "token": c["token"]}
        added.append(entry)
    return added, drop

if __name__ == "__main__":
    added, drop = main()
    if added:
        src = open(DATA, encoding="utf-8").read()
        i = src.index("window.RULED_OUT"); j = src.rindex("];", 0, i)
        lit = lambda a: "{ " + ", ".join(f"{k}:{json.dumps(v, ensure_ascii=True)}" for k, v in a.items()) + "}"
        head = src[:j].rstrip()
        if head.endswith(","): head = head[:-1]
        src = head + ",\n" + ",\n".join("  " + lit(a) for a in added) + "\n" + src[j:]
        open(DATA, "w", encoding="utf-8").write(src)
    json.dump({"added": [a["name"] for a in added], "dropped": drop}, open("refresh/work/extra_added.json", "w"), indent=1)
    print(f"research: added {len(added)}, dropped {len(drop)}")
