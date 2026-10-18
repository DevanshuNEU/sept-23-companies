"""
Runs every 10 minutes (launchd). Each run:
  0. proves the Gmail token still works (a token file alone is not a sign-in), and settles any send whose
     outcome was unclear by looking for its Message-ID in Sent;
  1. checks the threads of sent emails for replies and bounces; out-of-office and other automatic replies
     are logged and ignored;
  2. turns sent emails with no reply after N business days into a follow-up draft (status followup_ready),
     which waits for Devanshu's approval like any other draft; closes threads silent after the follow-up;
  3. sends at most ONE approved email, and only if: Gmail is signed in, sending is not paused, it is a weekday
     inside the recipient's local window, today's cap is not reached, and the random gap since the last send
     has passed. A bounce rate over the limit pauses sending by itself.
Gmail is never called while the store lock is held, so the dashboard's Approve never waits on the network.

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
AUTO_SUBJ = re.compile(r"^\s*(automatic reply|auto(matic)?[- ]?reply|autoreply|out of (the )?office|ooo\b|away from (the )?office|"
                       r"abwesenheit|absence|on leave|on vacation)", re.I)
SENT_STAGES = ("sent", "followup_ready", "followup_approved", "followup_sent")
# A send whose outcome is unclear (timeout, crash mid-call) stays out of every queue until Sent is checked.
UNCLEAR = ("sending", "send_unknown")
NOT_SENT_AFTER_MIN = 10        # not in Sent this long after the attempt: treat as not sent

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

def is_auto(m):
    """An out-of-office or other machine reply, which must not count as the person answering."""
    h = m.get("headers") or {}
    if h.get("auto-submitted", "no").strip().lower() != "no": return True
    if "x-autoreply" in h or "x-autorespond" in h or "x-auto-response-suppress" in h: return True
    if h.get("precedence", "").strip().lower() in ("auto_reply", "bulk", "junk", "list"): return True
    return bool(AUTO_SUBJ.search(m.get("subject") or ""))

def read_threads(db):
    """Gmail reads for sync(); runs outside the store lock. {contact id: messages}"""
    out = {}
    for c in db["contacts"]:
        if c["status"] not in SENT_STAGES or not c.get("threadId"): continue
        try: out[c["id"]] = gmail.thread(c["threadId"])
        except Exception as e: say(f"could not read thread for {c['email']}: {e}")
    return out

def sync(db, cfg, me, threads):
    for c in db["contacts"]:
        if c["status"] not in SENT_STAGES or c["id"] not in threads: continue
        msgs = threads[c["id"]]
        others = [m for m in msgs if me.lower() not in m["from"].lower()]
        if any(BOUNCE.search(m["from"]) or BOUNCE_SUBJ.search(m["subject"]) for m in others):
            c["status"] = "bounced"; store.log(c, "bounced"); say(f"bounced: {c['email']}"); continue
        autos = [m for m in others if is_auto(m)]
        if autos and not c.get("autoReplyAt"):
            c["autoReplyAt"] = store.now(); store.log(c, f"auto-reply ignored: {autos[0]['subject'][:80]}")
        others = [m for m in others if not is_auto(m)]
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

def say_once_a_day(msg, marker):
    today = datetime.date.today().isoformat()
    last = open(LOGF).read().splitlines()[-1] if os.path.exists(LOGF) and os.path.getsize(LOGF) else ""
    if not (last.startswith(f"[{today}") and marker in last): say(msg)

def read_unclear(db):
    """Sent-folder lookups for sends whose outcome is unclear; runs outside the store lock. {contact id: hit or None}"""
    out = {}
    for c in db["contacts"]:
        if c["status"] not in UNCLEAR or not c.get("pendingRfcId"): continue
        msg = c["followup"] if c.get("pendingFollowup") else c["draft"]
        try: out[c["id"]] = gmail.find_sent(c["pendingRfcId"], c["email"], msg["subject"])
        except Exception as e: say(f"could not check Sent for {c['email']}: {e}")
    return out

def record_sent(db, c, res, at):
    """The one place a send is recorded, whether we saw it succeed or found it in Sent afterwards."""
    if c.get("pendingFollowup"):
        c["status"], c["followupSentAt"] = "followup_sent", at
    else:
        c.update({k: res[k] for k in ("messageId", "threadId", "rfcMessageId")}); c["status"], c["sentAt"] = "sent", at
        db["firstSendDate"] = db.get("firstSendDate") or at[:10]
    for k in ("pendingRfcId", "pendingFollowup", "sendingAt", "error"): c.pop(k, None)

def reconcile(db, found):
    now = datetime.datetime.now().astimezone()
    for c in db["contacts"]:
        if c["status"] not in UNCLEAR or c["id"] not in found: continue
        if found[c["id"]]:
            record_sent(db, c, found[c["id"]], c["sendingAt"]); store.log(c, "found in Sent: it did go out")
            say(f"unclear send to {c['email']} found in Sent; recorded as sent")
        elif (now - datetime.datetime.fromisoformat(c["sendingAt"])).total_seconds() >= NOT_SENT_AFTER_MIN * 60:
            fu = c.get("pendingFollowup")
            c["status"] = "followup_ready" if fu else "failed"
            c["error"] = f"not in Sent {NOT_SENT_AFTER_MIN} min after the attempt, so it did not go out; safe to approve again"
            for k in ("pendingRfcId", "pendingFollowup", "sendingAt"): c.pop(k, None)
            store.log(c, "not found in Sent: did not go out"); say(f"unclear send to {c['email']} did not go out")

def main():
    if not gmail.authorized():
        say_once_a_day("not signed in to Gmail yet (run: python3 mail/gmail.py auth); nothing sent", "not signed in")
        return
    try: me = gmail.whoami()       # a token file is not a sign-in: this call proves the token still works
    except Exception as e:
        say_once_a_day(f"Gmail sign-in is not working ({str(e)[:200]}); run: python3 mail/gmail.py auth. Nothing sent",
                       "sign-in is not working")
        return
    # Gmail calls never run while the store lock is held: read, call Gmail, then lock again and apply.
    with store.locked(): snapshot = store.load()
    threads, found = read_threads(snapshot), read_unclear(snapshot)
    with store.locked():
        db, cfg = store.load(), store.config()
        sync(db, cfg, me, threads); reconcile(db, found); check_bounces(db, cfg); store.save(db)
        if cfg["paused"]: say("paused: " + (cfg["pausedWhy"] or "by you")); return
        cap, done = cap_today(db, cfg), sent_today(db)
        if done >= cap: return
        last = max((c.get(k) or "" for c in db["contacts"] for k in ("sentAt", "followupSentAt", "sendingAt")), default="")
        gap = random.uniform(*cfg["gapMinutes"])
        if last and (datetime.datetime.now().astimezone() - datetime.datetime.fromisoformat(last)).total_seconds() < gap * 60: return
        c, waiting = pick(db, cfg)
        if not c: return
        is_fu = c["status"] == "followup_approved"
        msg = c["followup"] if is_fu else c["draft"]
        if DRY: say(f"would send {'follow-up ' if is_fu else ''}to {c['email']}: {msg['subject']} ({done}/{cap} today, {waiting} waiting)"); return
        # Claim it, with the Message-ID it will carry, before calling Gmail. If the call times out or this
        # process dies, the contact stays out of every queue until reconcile() finds it in Sent or not.
        rfc = gmail.new_message_id(me)
        c.update({"status": "sending", "pendingRfcId": rfc, "pendingFollowup": is_fu, "sendingAt": store.now()})
        store.log(c, "sending"); store.save(db)
        cid = c["id"]
    try:
        res = gmail.send(c["email"], msg["subject"], msg["body"], cfg["fromName"], me,
                         thread_id=c.get("threadId") if is_fu else None,
                         in_reply_to=c.get("rfcMessageId") if is_fu else None, message_id=rfc)
    except Exception as e:
        with store.locked():
            db = store.load(); c = next(x for x in db["contacts"] if x["id"] == cid)
            c["status"], c["error"] = "send_unknown", str(e)[:300]
            store.log(c, "send outcome unclear; will check Sent before anything else"); store.save(db)
        say(f"send to {c['email']} unclear ({e}); will check Sent next run"); return
    with store.locked():
        db = store.load(); c = next(x for x in db["contacts"] if x["id"] == cid)
        record_sent(db, c, res, store.now()); store.log(c, "follow-up sent" if is_fu else "sent"); store.save(db)
    say(f"sent {'follow-up ' if is_fu else ''}to {c['name']} <{c['email']}> at {c['company']} ({done + 1}/{cap} today)")

if __name__ == "__main__":
    main()
