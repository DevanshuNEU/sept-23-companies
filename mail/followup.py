"""
Replace the automatic follow-up of one contact with a better one (status followup_ready only).

  python3 mail/followup.py <contact id> < '{"subject": "Re: ...", "body": "..."}'
"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store

cid, msg = sys.argv[1], json.load(sys.stdin)
if not msg.get("subject") or not msg.get("body"): raise SystemExit("follow-up needs a subject and a body")
with store.locked():
    db = store.load()
    c = next((x for x in db["contacts"] if x["id"] == cid), None)
    if not c: raise SystemExit(f"no contact {cid}")
    if c["status"] != "followup_ready": raise SystemExit(f"{c['name']} is {c['status']}, not waiting for a follow-up")
    c["followup"] = {"subject": msg["subject"], "body": msg["body"]}; store.log(c, "follow-up rewritten")
    store.save(db)
print(f"follow-up for {c['name']} replaced; approve it in the Outbox")
