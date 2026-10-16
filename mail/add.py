"""
Add one researched contact with its first draft (used by Claude while drafting; see .claude/skills/cold-email).

  python3 mail/add.py < contact.json

contact.json:
  {"company": "Retell AI", "name": "Jane Doe", "email": "jane@retellai.com", "title": "Software Engineer",
   "linkedinUrl": "...", "profileText": "<pasted LinkedIn text>", "kind": "engineer|manager|founder|recruiter|alum",
   "tz": "America/Los_Angeles", "hooks": ["Northeastern MS 2019", "..."], "role": {"title": "...", "url": "..."},
   "draft": {"subject": "...", "body": "..."}}

Refuses a malformed address, a duplicate address, or a draft without a subject and body. The address must
come from Devanshu; nothing here guesses one.
"""
import json, re, sys, subprocess, os, uuid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def company_id(name):
    js = subprocess.run(["node", "-e", "global.window={};eval(require('fs').readFileSync('dashboard/data.js','utf8'));"
                         "console.log(JSON.stringify(window.COMPANIES.map(c=>[c.id,c.name])))"],
                        capture_output=True, text=True, cwd=ROOT)
    if js.returncode: return None
    k = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    return next((cid for cid, n in json.loads(js.stdout) if k(n) == k(name)), None)

def main():
    c = json.load(sys.stdin)
    for f in ("company", "name", "email"):
        if not c.get(f): raise SystemExit(f"missing {f}")
    c["email"] = c["email"].strip()
    if not EMAIL.match(c["email"]): raise SystemExit(f"not an email address: {c['email']}")
    d = c.get("draft") or {}
    if not d.get("subject") or not d.get("body"): raise SystemExit("draft needs a subject and a body")
    with store.locked():
        db = store.load()
        if any(x["email"].lower() == c["email"].lower() for x in db["contacts"]):
            raise SystemExit(f"already in the list: {c['email']}")
        c.update({"id": uuid.uuid4().hex[:10], "companyId": company_id(c["company"]), "status": "drafted",
                  "createdAt": store.now(), "followup": None})
        store.log(c, "drafted")
        db["contacts"].append(c); store.save(db)
    print(f"added {c['name']} at {c['company']} ({c['id']}), waiting for your approval in the dashboard")

if __name__ == "__main__":
    main()
