"""
The cold-email contact store: mail/contacts.json, plus mail/config.json for sending rules.

A contact moves through:
  drafted -> approved -> sent -> replied | bounced | followup_ready
  followup_ready -> followup_approved -> followup_sent -> replied | bounced | closed
  (any unsent stage) -> skipped
Only a click on Approve in the dashboard moves a draft to approved; only the sender sends.
"""
import json, os, fcntl, datetime, contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
CONTACTS, CONFIG, LOCK = (os.path.join(HERE, n) for n in ("contacts.json", "config.json", ".lock"))

DEFAULT_CONFIG = {
    "fromName": "Devanshu Chicholikar",
    "signature": "Devanshu Chicholikar",
    "dailyCaps": [[7, 10], [14, 20], [9999, 25]],   # [days since first send, cap]: ramp 10, then 20, then 25
    "window": ["08:30", "16:30"],                    # recipient's local time
    "weekdays": [0, 1, 2, 3, 4],                     # Mon-Fri
    "gapMinutes": [6, 14],                           # random gap between two sends
    "followupBusinessDays": 4,
    "closeAfterDays": 7,                             # after the follow-up, with no reply
    "pauseIfBounceRate": 0.03, "minSendsForBounceCheck": 10,
    "paused": False, "pausedWhy": "",
}

@contextlib.contextmanager
def locked():
    with open(LOCK, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        yield

def _read(path, default):
    try: return json.load(open(path))
    except FileNotFoundError: return default

def _write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)

def load():
    return _read(CONTACTS, {"contacts": [], "firstSendDate": None})

def save(db): _write(CONTACTS, db)

def config():
    cfg = dict(DEFAULT_CONFIG); cfg.update(_read(CONFIG, {}))
    return cfg

def save_config(cfg): _write(CONFIG, cfg)

def now(): return datetime.datetime.now().astimezone().isoformat(timespec="seconds")

def log(c, what):
    c.setdefault("history", []).append({"at": now(), "what": what})
