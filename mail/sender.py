"""
Runs every 10 minutes (launchd). Each run:
  1. checks the threads of sent emails for replies and bounces;
  2. turns sent emails with no reply after N business days into a follow-up draft (status followup_ready),
     which waits for Devanshu's approval like any other draft; closes threads silent after the follow-up;
  3. sends at most ONE approved email, and only if: Gmail is signed in, sending is not paused, it is a weekday
     inside the recipient's local window, today's cap is not reached, and the random gap since the last send
     has passed. A bounce rate over the limit pauses sending by itself.

  python3 mail/sender.py            normal run
  python3 mail/sender.py --dry-run  show what would happen, send nothing
Log: mail/send.log
"""
import datetime, os, random, re, sys
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store, gmail

DRY = "--dry-run" in sys.argv
LOGF = os.path.join(store.HERE, "send.log")
BOUNCE = re.compile(r"mailer-daemon|postmaster|mail delivery (sub)?system", re.I)
BOUNCE_SUBJ = re.compile(r"delivery status notification|undeliverable|undelivered|returned mail|delivery failure", re.I)
SENT_STAGES = ("sent", "followup_ready", "followup_approved", "followup_sent")

def say(msg):
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M}] {'(dry run) ' if DRY else ''}{msg}"
    if sys.stdout.isatty(): print(line)
    with open(LOGF, "a") as f: f.write(line + "\n")

def business_days_since(iso):
    d, n, today = datetime.date.fromisoformat(iso[:10]), 0, datetime.date.today()
    while d < today:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5: n += 1
    return n

def followup_text(c, cfg):
    first = c["name"].split()[0]
    return {"subject": "Re: " + c["draft"]["subject"],
            "body": f"Hi {first},\n\nBumping this up in case it got buried. Still very interested in "
                    f"{(c.get('role') or {}).get('title') or 'the role'} at {c['company']}, and happy to send anything that helps.\n\n"
                    f"Thanks,\n{cfg['signature']}"}

def sync(db, cfg, me):
    for c in db["contacts"]:
        if c["status"] not in SENT_STAGES or not c.get("threadId"): continue
        try: msgs = gmail.thread(c["threadId"])
        except Exception as e: say(f"could not read thread for {c['email']}: {e}"); continue
        others = [m for m in msgs if me.lower() not in m["from"].lower()]
        if any(BOUNCE.search(m["from"]) or BOUNCE_SUBJ.search(m["subject"]) for m in others):
            c["status"] = "bounced"; store.log(c, "bounced"); say(f"bounced: {c['email']}"); continue
        if others:
            c["status"] = "replied"; c["replyAt"] = store.now(); store.log(c, "replied"); say(f"reply from {c['email']}"); continue
        if c["status"] == "sent" and business_days_since(c["sentAt"]) >= cfg["followupBusinessDays"]:
            c["followup"] = c.get("followup") or followup_text(c, cfg)
            c["status"] = "followup_ready"; store.log(c, "follow-up drafted, waiting for approval")
        elif c["status"] == "followup_sent" and (datetime.date.today() - datetime.date.fromisoformat(c["followupSentAt"][:10])).days >= cfg["closeAfterDays"]:
            c["status"] = "closed"; store.log(c, "no reply after follow-up")

def check_bounces(db, cfg):
    sent = [c for c in db["contacts"] if c.get("sentAt")]
    if len(sent) >= cfg["minSendsForBounceCheck"]:
        rate = sum(c["status"] == "bounced" for c in sent) / len(sent)
        if rate > cfg["pauseIfBounceRate"] and not cfg["paused"]:
            cfg["paused"], cfg["pausedWhy"] = True, f"bounce rate {rate:.0%} over {cfg['pauseIfBounceRate']:.0%}"
            store.save_config(cfg); say("PAUSED: " + cfg["pausedWhy"])

def cap_today(db, cfg):
    if not db.get("firstSendDate"): return cfg["dailyCaps"][0][1]
    days = (datetime.date.today() - datetime.date.fromisoformat(db["firstSendDate"])).days
    return next(cap for upto, cap in cfg["dailyCaps"] if days < upto)

def sent_today(db):
    today = datetime.date.today().isoformat()
    return sum(1 for c in db["contacts"] for k in ("sentAt", "followupSentAt") if (c.get(k) or "")[:10] == today)

def in_window(c, cfg):
    tz = ZoneInfo(c.get("tz") or "America/New_York")
    t = datetime.datetime.now(tz)
    a, b = (datetime.time.fromisoformat(x) for x in cfg["window"])
    return t.weekday() in cfg["weekdays"] and a <= t.time() <= b

def pick(db, cfg):
    queue = [c for c in db["contacts"] if c["status"] in ("approved", "followup_approved")]
    queue.sort(key=lambda c: c.get("approvedAt") or "")
    return next((c for c in queue if in_window(c, cfg)), None), len(queue)

def main():
    if not gmail.authorized():
        today = datetime.date.today().isoformat()
        last = open(LOGF).read().splitlines()[-1] if os.path.exists(LOGF) and os.path.getsize(LOGF) else ""
        if not (last.startswith(f"[{today}") and "not signed in" in last):
            say("not signed in to Gmail yet (run: python3 mail/gmail.py auth); nothing sent")
        return
    with store.locked():
        db, cfg = store.load(), store.config()
        me = gmail.whoami()
        sync(db, cfg, me); check_bounces(db, cfg); store.save(db)
        if cfg["paused"]: say("paused: " + (cfg["pausedWhy"] or "by you")); return
        cap, done = cap_today(db, cfg), sent_today(db)
        if done >= cap: return
        last = max((c.get(k) or "" for c in db["contacts"] for k in ("sentAt", "followupSentAt")), default="")
        gap = random.uniform(*cfg["gapMinutes"])
        if last and (datetime.datetime.now().astimezone() - datetime.datetime.fromisoformat(last)).total_seconds() < gap * 60: return
        c, waiting = pick(db, cfg)
        if not c: return
        is_fu = c["status"] == "followup_approved"
        msg = c["followup"] if is_fu else c["draft"]
        if DRY: say(f"would send {'follow-up ' if is_fu else ''}to {c['email']}: {msg['subject']} ({done}/{cap} today, {waiting} waiting)"); return
        try:
            res = gmail.send(c["email"], msg["subject"], msg["body"], cfg["fromName"], me,
                             thread_id=c.get("threadId") if is_fu else None,
                             in_reply_to=c.get("rfcMessageId") if is_fu else None)
        except Exception as e:
            c["status"] = "failed"; c["error"] = str(e)[:300]; store.log(c, "send failed"); store.save(db)
            say(f"FAILED to send to {c['email']}: {e}"); return
        if is_fu:
            c["status"], c["followupSentAt"] = "followup_sent", store.now()
        else:
            c.update(res); c["status"], c["sentAt"] = "sent", store.now()
            db["firstSendDate"] = db.get("firstSendDate") or datetime.date.today().isoformat()
        store.log(c, "follow-up sent" if is_fu else "sent")
        store.save(db)
        say(f"sent {'follow-up ' if is_fu else ''}to {c['name']} <{c['email']}> at {c['company']} ({done + 1}/{cap} today)")

if __name__ == "__main__":
    main()
