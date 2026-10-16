"""
New companies from Devanshu's Job Radar (DevVault/job-search/pipeline/radar.db).

radar.db holds his triage history, which cannot be recomputed, so it is opened READ-ONLY and nothing here
can write to it. Only Ashby, Greenhouse and Lever boards are taken, because those can be re-read reliably by
the rest of this refresh. A company is added only if it is small (under ~500 by the same evidence the rest of
the dashboard uses) and has an open, entry-shaped US engineering role first seen in the last 45 days.
Everything else about it (level, graduation window, citizenship) is checked by the normal sweep afterwards.
"""
import json, re, sqlite3, subprocess, datetime, collections, os

RADAR   = os.path.expanduser("~/Desktop/DevVault/job-search/pipeline/radar.db")
LCA     = ".research/h1bdata/h1b_full.db"
USCIS   = ".research/h1bdata/uscis.db"
DATA    = "dashboard/data.js"
TODAY   = datetime.date.today().isoformat()

SOC = ('15-1252','15-1251','15-1211','15-1221','15-1232','15-1241','15-1299','15-1132','15-1133','15-1131')
P   = [0.1529, 0.3058, 0.45, 0.61]

# Companies known to be well over 500 people, whatever the visa data says.
BIG = re.compile(r"^(jp ?morgan|jpmorgan|blackrock|dell|micron|morgan stanley|mongodb|palantir|amazon|datadog|databricks|"
                 r"mastercard|visa|cloudflare|brex|red hat|western digital|intel|toast|perplexity|cohere$|bloomberg|handshake|"
                 r"google|microsoft|apple|meta|nvidia|oracle|ibm|salesforce|adobe|stripe|notion|openai|anthropic|xai|scale ai|"
                 r"snowflake|doordash|uber|lyft|airbnb|coinbase|robinhood|ramp|rippling|gusto|figma|canva|samsara|okta|"
                 r"twilio|hubspot|zoom|atlassian|servicenow|workday|intuit|paypal|block|square|shopify|spotify|reddit|"
                 r"discord|duolingo|grammarly|superhuman|crowdstrike|palo alto|zscaler|elastic|confluent|gitlab|github|"
                 r"vercel|retool|airtable|plaid|chime|sofi|affirm|klaviyo|deel|navan|flexport|anduril|cerebras|coreweave|"
                 r"tenstorrent|crusoe|character|mistral|hugging ?face|together|fireworks|sierra|harvey|glean|writer|"
                 r"verkada|abnormal|wiz|cyera|snyk|1password|benchling|tempus|flatiron|ro$|hims|oscar|lattice|mercury)\b", re.I)
# Radar boards whose token belongs to a different company than the radar's label (checked by hand).
SKIP_BOARDS = {("lever", "ion")}          # labelled "ion design", but jobs.lever.co/ion is ION Group
UNSURE = re.compile(r"^(abridge|mercor|hightouch|datafold|alpaca|cohere health|lila sciences|sila)", re.I)

NG  = re.compile(r"new.?grad|university grad|college grad|early.?career|entry.?level|\bassociate\b|junior|graduate|"
                 r"\b(engineer|developer)\s*[-,]?\s*(i|1|ii|2)\b|forward.?deploy|member of technical staff|\bMTS\b", re.I)
ENG = re.compile(r"(engineer|developer|member of technical staff|\bMTS\b|\bswe\b|\bsde\b|programmer)", re.I)
SEN = re.compile(r"\b(senior|sr\.?|staff|principal|lead|head|director|manager|architect|vp|chief|iii|iv)\b", re.I)
INT = re.compile(r"\b(intern|internship|co-?op)\b", re.I)
OFF = re.compile(r"\b(gtm|sales|account|recruit|designer|mechanical|electrical|hardware|firmware|embedded|analog|fpga|"
                 r"manufactur|quality|\bqa\b|test engineer|technician|scientist|support engineer|solutions engineer|"
                 r"network engineer|it systems)\b", re.I)
NONUS = re.compile(r"(india|bangalore|bengaluru|hyderabad|canada|toronto|vancouver|london|united kingdom|\buk\b|ireland|"
                   r"germany|berlin|poland|warsaw|israel|tel aviv|singapore|australia|japan|tokyo|korea|china|mexico|brazil|"
                   r"argentina|netherlands|amsterdam|paris|france|spain|sweden|switzerland|zurich|portugal|estonia|emea|apac|latam)", re.I)
STATE = re.compile(r",\s*([A-Z]{2})\b")
CITY_STATE = {"san francisco":"CA","sf":"CA","palo alto":"CA","mountain view":"CA","san mateo":"CA","sunnyvale":"CA",
              "san jose":"CA","oakland":"CA","los angeles":"CA","new york":"NY","nyc":"NY","brooklyn":"NY","seattle":"WA",
              "austin":"TX","boston":"MA","cambridge":"MA","chicago":"IL","denver":"CO","miami":"FL","atlanta":"GA"}

def norm(s):
    s = re.sub(r"[^A-Z0-9 ]", " ", (s or "").upper()); s = re.sub(r"\s+", " ", s).strip()
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"\s+(INC|LLC|CORP|CORPORATION|CO|LTD|LP|LLP|COMPANY|TECHNOLOGIES|TECHNOLOGY|LABS|LAB|AI|HQ)$", "", s).strip()
    return s
def states_of(locs):
    out = set()
    for l in locs:
        out.update(STATE.findall(l or ""))
        for k, v in CITY_STATE.items():
            if k in (l or "").lower(): out.add(v)
    return out

def tracked():
    js = subprocess.run(["node", "-e", """global.window={};eval(require('fs').readFileSync('dashboard/data.js','utf8'));
      console.log(JSON.stringify(window.COMPANIES.map(c=>({n:c.name,b:c.board||null,u:(c.reqs||[]).map(r=>r.u||'')}))))"""],
      capture_output=True, text=True, check=True).stdout
    rows = json.loads(js)
    names = {re.sub(r"[^a-z0-9]", "", r["n"].lower()) for r in rows}
    tokens = set()
    for r in rows:
        if r["b"]: tokens.add((r["b"]["ats"], r["b"]["token"].lower()))
        for u in r["u"]:
            for ats, rx in (("ashby", r"jobs\.ashbyhq\.com/([^/?#]+)"), ("greenhouse", r"greenhouse\.io/(?:v1/boards/)?([^/?#]+)"),
                            ("lever", r"jobs\.lever\.co/([^/?#]+)")):
                m = re.search(rx, u)
                if m: tokens.add((ats, m.group(1).lower()))
    return names, tokens

def lca_lookup(lca, name, posting_states):
    key = norm(name)
    if len(key) < 3: return None
    ph = ",".join("?" * len(SOC))
    rows = lca.execute(f"""select employer_norm, PW_WAGE_LEVEL, WORKSITE_STATE, NAICS_CODE from lca
                           where employer_norm = ? and substr(SOC_CODE,1,7) in ({ph})
                             and CASE_STATUS in ('Certified','Certified - Withdrawn') and VISA_CLASS='H-1B'""",
                       (key,) + SOC).fetchall()
    if not rows: return {"n": 0, "verified": False}
    states = {r[2] for r in rows if r[2]}
    if posting_states and not (posting_states & states) and len(states) < 4:
        return {"collision": True, "states": sorted(states)}
    mix = [0, 0, 0, 0]
    for r in rows:
        i = {"I": 0, "II": 1, "III": 2, "IV": 3}.get((r[1] or "").strip())
        if i is not None: mix[i] += 1
    tot = sum(mix)
    return {"n": len(rows), "verified": True, "mix": mix, "key": key,
            "odds": round(sum(a * b for a, b in zip(mix, P)) / tot * 100, 1) if tot else None}

def main():
    if not os.path.exists(RADAR):
        print("radar.db not found, skipping discovery"); return []
    radar = sqlite3.connect(f"file:{RADAR}?mode=ro", uri=True)          # read-only, by construction
    lca   = sqlite3.connect(f"file:{LCA}?mode=ro", uri=True)
    usc   = sqlite3.connect(f"file:{USCIS}?mode=ro", uri=True)
    names, tokens = tracked()

    boards = radar.execute("""select board_id, ats, token, company from board
                              where active = 1 and ats in ('ashby','greenhouse','lever')""").fetchall()
    fresh = [b for b in boards
             if (b[1], (b[2] or "").lower()) not in tokens
             and re.sub(r"[^a-z0-9]", "", (b[3] or "").lower()) not in names
             and not BIG.search((b[3] or "").strip())
             and (b[1], (b[2] or "").lower()) not in SKIP_BOARDS]
    if not fresh: print("no untracked radar boards"); return []
    ids = tuple(b[0] for b in fresh)
    posts = radar.execute(f"""select board_id, title, location, apply_url, coalesce(posted_at, first_seen_at)
                              from posting where merged_into is null
                                and last_seen_at >= datetime('now','-1 day')
                                and first_seen_at >= datetime('now','-45 day')
                                and board_id in ({','.join('?'*len(ids))})""", ids).fetchall()
    by = collections.defaultdict(list)
    for bid, title, loc, url, when in posts:
        t = re.sub(r"member of (technical|product|engineering) staff", "MTS", title or "", flags=re.I)
        if not ENG.search(title or "") or SEN.search(t) or INT.search(title or "") or OFF.search(title or ""): continue
        if not NG.search(title or ""): continue
        if loc and NONUS.search(loc) and not re.search(r"(united states|\bUS\b|USA|remote)", loc, re.I): continue
        by[bid].append({"t": (title + (" - " + loc if loc else ""))[:100], "u": url, "d": (when or "")[:10], "loc": loc or ""})

    meta = {b[0]: b for b in fresh}
    added, skipped = [], collections.Counter()
    for bid, reqs in by.items():
        _, ats, token, company = meta[bid]
        lk = lca_lookup(lca, company, states_of([r["loc"] for r in reqs]))
        if lk and lk.get("collision"):
            n, size, why = None, "small", "the visa-data name match pointed at an unrelated employer, so size is unknown"
        elif lk and lk.get("verified"):
            n = lk["n"]
            if n > 30: skipped["over 500 by visa filings"] += 1; continue
            size, why = "small", f"{n} lifetime visa filings, consistent with under 500"
        else:
            n, size, why = None, "small", "no filing record and not a name I recognise as large: most likely an early startup"
        if UNSURE.search(company): size, why = "unsure", "size genuinely uncertain, somewhere around 300 to 700"
        um = {}
        if lk and lk.get("key"):
            um = {f: (x or 0) for f, x in usc.execute(
                "select fy, new_approval from uscis where employer_norm=? and fy in (2024,2025,2026)", (lk["key"],))}
        slug = re.sub(r"[^a-z0-9]", "", company.lower())[:26] or token
        loc0 = next((r["loc"] for r in reqs if r["loc"]), "")
        added.append({
            "id": "rd-" + slug, "name": company, "cat": "Software engineering", "hq": loc0,
            "board": {"ats": ats, "token": token}, "addedOn": TODAY, "addedBy": "Job Radar",
            "sizeClass": size, "sizeWhy": why, "apparatus": n,
            "odds": (lk or {}).get("odds"), "mix": (lk or {}).get("mix", [0, 0, 0, 0]),
            "uscis": [um.get(2024, 0), um.get(2025, 0), um.get(2026, 0)], "trend": "none", "lcaTrend": "none",
            "reqs": [{"t": r["t"], "u": r["u"], "d": r["d"]} for r in reqs[:10]],
            "visa": "Not reviewed yet. No sponsorship statement has been read for this company.",
            "blockers": [], "note": f"Found by your Job Radar on {TODAY}."})
    return added, skipped

if __name__ == "__main__":
    res = main()
    added, skipped = res if res else ([], {})
    if added:
        src = open(DATA, encoding="utf-8").read()
        i = src.index("window.RULED_OUT")
        j = src.rindex("];", 0, i)
        lit = lambda a: "{ " + ", ".join(f"{k}:{json.dumps(v, ensure_ascii=True)}" for k, v in a.items()) + "}"
        block = ",\n" + ",\n".join("  " + lit(a) for a in added) + "\n"
        head = src[:j].rstrip()
        if head.endswith(","): head = head[:-1]          # a trailing comma here would leave a hole in the array
        src = head + block + src[j:]
        open(DATA, "w", encoding="utf-8").write(src)
    json.dump({"added": [a["name"] for a in added], "skipped": dict(skipped)}, open("refresh/work/radar_added.json", "w"))
    print(f"radar: added {len(added)} new companies; skipped {dict(skipped)}")
