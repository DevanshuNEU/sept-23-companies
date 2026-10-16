import json, re, datetime, collections
B="refresh/work/"
R=json.load(open(B+"finalcheck.json")); E=json.load(open(B+"eligibility.json"))
# hand-verified calls, each read on the live posting on 2026-09-29
OVERRIDE={
 'https://job-boards.greenhouse.io/perpay/jobs/4034578007': ("ok", "Currently pursuing a Bachelor's degree or higher or a recent graduate studying Computer Science, Mathematics, or a related technical field."),
 "https://ats.rippling.com/edgehog-trading/jobs/0d6b0e5c-be84-4324-8ee3-b875504e01d8": ("ok", None),
}
HAND_START27={"Perpay":"join our Software Engineering team in 2027","DV Trading":"expected to graduate by Summer 2027",
              "Together AI":"earned or expected by Summer 2027","Replit":"Recent graduate (2027)"}
HAND_NOTE={"Replit":"The posting asks for a “Recent graduate (2027)”, which reads as the Class of 2027. You finished in May 2026, so ask before spending a message here."}
T={t["id"]:t for t in json.load(open(B+"final_targets.json"))}
TODAY=datetime.date(2026,9,29)
def age(d):
    try: return (TODAY-datetime.date.fromisoformat(d)).days
    except Exception: return 9999
TN={1:"New grad",2:"Early career",3:"SE1",4:"SDE2",5:"Level not stated"}
T27=re.compile(r"(\(2027\)|new grad[s]? 2027|2027 (new )?grad|2027 start|\b2027\b[^|]*(start|cohort|program))", re.I)
CS=re.compile(r"customer success|\(\s*contract|contract\s*[,)]|\bcontractor\b", re.I)
def tidy(q):
    """Trim a quote to whole words so it never starts or ends mid-word."""
    if not q: return None
    q=q.strip()
    if q and q[0].isalpha() and q[0].islower():
        sp=q.find(" "); q=q[sp+1:] if sp>0 else q
    if len(q)>200:
        q=q[:200]; q=q[:q.rfind(" ")]+"\u2026"
    return q[0].upper()+q[1:] if q else None
def basis(j):
    t=j["title"]; y=j["minYears"]
    if j["tier"]==1: return "Title says new grad"
    if j["tier"]==2:
        if re.search(r"(early.?career|associate|junior|entry.?level)", t, re.I): return "Title says early career"
        if y is not None and y<=1: return "Posting asks 0 to 1 years"
        return "Posting says entry level"
    if j["tier"]==3: return "Title says Engineer I" + (f", asks {y} years" if y else "")
    if j["tier"]==4: return (f"Asks {y} years" if y is not None else "Title says Engineer II")
    return "Posting does not state a level"
out={}; cnt=collections.Counter()
for cid,v in R.items():
    name=T[cid]["name"]; el=E.get(cid,{}); ok=[]; why=collections.Counter()
    for j in v["jobs"]:
        e,q=OVERRIDE.get(j["url"], (el.get(j["url"],{}).get("elig","ok"), el.get(j["url"],{}).get("eligQuote")))
        if j["tier"] is None:  why["ask 4+ years"]+=1; continue
        if e=="excluded":      why["graduation window or current-student rule"]+=1; continue
        if j["citizen"]:       why["citizenship or clearance"]+=1; continue
        if j.get("phd"):       why["PhD only"]+=1; continue
        if CS.search(j["title"]): why["contract or customer-success role"]+=1; continue
        if re.search(r"\b(experienced|mid.?level|mid.?senior)\b", j["title"], re.I): why["experienced-level title"]+=1; continue
        j=dict(j); j["eligQuote"]=q
        j["start2027"]=bool(j["start2027"] or (T27.search(j["title"]) and not j.get("immediate")))
        if name in HAND_START27 and j["tier"]==1: j["start2027"]=True
        ok.append(j)
    rec={"reqsRead":len(v["jobs"])}
    if not ok:
        rec.update(noRoleInBand=True, noRoleWhy=("; ".join(f"{n} {k}" for k,n in why.most_common(3)) if why
                    else ("every posting link is closed" if v.get("closedUrls") else "no open software role in your lane")))
        cnt["none"]+=1
    else:
        ok.sort(key=lambda j:(j["tier"], 1 if j["start2027"] else 0, age(j["posted"]) if j["posted"] else 9999))
        b=ok[0]
        rec.update(entryReq=b["title"], entryTier=b["tier"], entryTierName=TN[b["tier"]], entryBasis=basis(b),
                   entryPosted=b["posted"], entryUrl=b["url"], entryLoc=b["loc"], lowestBar=b["minYears"],
                   inBand=len(ok), levelOk=True, levelChecked=True, start2027=b["start2027"],
                   comp=b.get("comp"), unverified=bool(b.get("unverified")),
                   eligQuote=(HAND_NOTE.get(name) if (name in HAND_NOTE and "2027" in b["title"]) else tidy(b.get("eligQuote"))),
                   reqs=[{"t":(j["title"]+(" - "+j["loc"] if j["loc"] else ""))[:100],"u":j["url"],"d":j["posted"],"tier":j["tier"]} for j in ok[:10]])
        cnt[TN[b["tier"]]+(" (2027 start)" if b["start2027"] else "")]+=1
    out[cid]=rec
json.dump(out, open(B+"final_merge.json","w"))
print("merged:", len(out)); print(dict(cnt))
