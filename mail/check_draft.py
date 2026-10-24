"""
Check one cold-email draft against the #9 Drafts rules before it is queued (run by .claude/skills/cold-email).

  python3 mail/check_draft.py [--banned PATH] [--me PATH] [--followup] < contact.json

contact.json is what mail/add.py takes (the draft is under "draft"); for a follow-up, pass --followup and a JSON
with "name", "company", "kind", "subject" and "body". Prints BLOCK and WARN lines, then a summary.
Exit 0: no blocking problem (warnings go to Devanshu with the draft). Exit 1: fix and re-check. Exit 2: bad input.

Not checked here: the recipient's mail server (needs the network), the hook's age (the JSON carries no date),
flattery beyond the banned list (judgment). The skill covers those.
"""
import argparse, difflib, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BANNED_DEFAULT = os.path.expanduser("~/Desktop/DevVault/job-search/outreach/banned_phrases.txt")
ME_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "me.md")
PORTFOLIO = "devanshuchicholikar.com"
WORDS = {"engineer": (80, 120), "manager": (80, 120), "founder": (80, 120), "alum": (80, 120), "recruiter": (80, 100)}
FOLLOWUP_MAX_WORDS = 80
NEVER = ["bumping this", "bump this", "got buried", "gentle reminder", "in case you missed"]   # #9 Follow-ups
WEAK_SUBJECT = ["quick question", "following up", "excited"]
SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "buff.ly", "is.gd", "rebrand.ly", "cutt.ly",
              "lnkd.in", "shorturl.at", "rb.gy", "tiny.cc", "s.id", "t.ly"}
DASHES = re.compile(r"[\u2012-\u2015]")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
URL = re.compile(r"https?://[^\s<>()\"']+", re.I)
# A bare domain becomes a link in Gmail too, so it counts.
BARE = re.compile(r"(?<![\w@./-])(?:[a-z0-9-]+\.)+(?:com|ai|io|dev|app|co|org|net|ly|gl|in|at|cc|id|me|sh|so|xyz|us)"
                  r"\b(?:/[^\s<>()\"']*)?", re.I)
WORK_AUTH = re.compile(r"\b(OPT|F-1|visa|sponsor\w*|work authori[sz]\w*|authori[sz]ed to work)\b", re.I)
PLACEHOLDER = re.compile(r"\bTODO\b|\bASK:|\{\{|\}\}")


def load_banned(path):
    """(pattern text, compiled) for scopes both and email of a DevVault banned_phrases.txt."""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            scope, _, pattern = line.rstrip("\n").partition("\t")
            if scope.strip() in ("both", "email") and pattern.strip():
                out.append((pattern, re.compile(pattern.replace("[[:space:]]", r"\s"), re.I)))
    if not out: raise ValueError(f"no email patterns in {path}")
    return out


def load_unconfirmed(me_path):
    """Values quoted in backticks on the ASK items of me.md (an item may wrap onto indented lines)."""
    out, in_ask = [], False
    with open(me_path, encoding="utf-8") as f:
        for line in f:
            if line.lstrip().startswith("- "): in_ask = line.lstrip().startswith("- ASK:")
            elif not line.startswith((" ", "\t")) or not line.strip(): in_ask = False
            if in_ask: out += re.findall(r"`([^`]+)`", line)
    return out


def _trim(u): return u.rstrip(".,;:!?")


def links(text):
    """Every link in reading order: full URLs, then bare domains Gmail would link. Email addresses are not links."""
    text = EMAIL.sub(" ", text)
    found = [(m.start(), _trim(m.group())) for m in URL.finditer(text)]
    rest = URL.sub(lambda m: " " * len(m.group()), text)
    found += [(m.start(), _trim(m.group())) for m in BARE.finditer(rest)]
    return [u for _, u in sorted(found)]


def _host(u):
    h = re.sub(r"^https?://", "", u, flags=re.I).split("/")[0].lower()
    return h[4:] if h.startswith("www.") else h


def _is_portfolio(u):
    return _host(u) == PORTFOLIO and re.sub(r"^https?://[^/]+", "", u).strip("/") == ""


def _norm(s): return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def check(contact, banned, unconfirmed, others, followup=False):
    """(blocks, warns) for one draft. others: the contacts already in the list."""
    msg = contact.get("draft") or contact
    subject, body = (msg.get("subject") or "").strip(), (msg.get("body") or "").strip()
    kind = contact.get("kind") or ""
    blocks, warns = [], []
    if not subject: blocks.append("subject is empty")
    if not body: blocks.append("body is empty")
    if kind not in WORDS: blocks.append(f"kind is {kind!r}; use engineer, manager, founder or recruiter")
    text = subject + "\n" + body

    for src, rx in banned:
        if rx.search(text): blocks.append(f"banned phrase: {src!r}")
    for phrase in NEVER:
        if phrase in text.lower(): blocks.append(f"banned phrase: {phrase!r} (a follow-up adds one new thing)")
    if DASHES.search(text): blocks.append("em or en dash: use a comma, colon, full stop or plain hyphen")
    if PLACEHOLDER.search(text): blocks.append("placeholder text (TODO, ASK:, {{ }}) left in the draft")
    for v in unconfirmed:
        if re.search(r"(?<!\w)" + re.escape(v) + r"(?!\w)", text, re.I):
            blocks.append(f"uses {v!r}, which me.md marks as unconfirmed (an ASK line)")

    found = links(body)
    if any(_host(u) in SHORTENERS for u in found): blocks.append("link shortener: use the full link")
    if followup:
        if len(found) > 2: blocks.append(f"{len(found)} links: more than 2 links")
    else:
        if len(found) != 2:
            blocks.append(f"{len(found)} links: a first email has exactly 2 links, portfolio first, then one project")
        elif not _is_portfolio(found[0]):
            blocks.append(f"first link is {found[0]}; the portfolio https://www.{PORTFOLIO} goes first")
        if re.match(r"re\s*:", subject, re.I): blocks.append("subject starts with 'Re:' on a first email")
        for o in others:
            if o.get("email", "").lower() == (contact.get("email") or "").lower(): continue
            od = o.get("draft") or {}
            if subject and (od.get("subject") or "").strip().lower() == subject.lower():
                blocks.append(f"subject already used for {o.get('name')} at {o.get('company')}; write a new one")
            if body and _norm(o.get("company")) == _norm(contact.get("company")) and \
                    difflib.SequenceMatcher(None, body.lower(), (od.get("body") or "").lower(), autojunk=False).ratio() >= 0.8:
                blocks.append(f"near-identical to the draft for {o.get('name')}, a colleague at the same company")

    n = len(URL.sub(" ", body).split())
    lo, hi = (0, FOLLOWUP_MAX_WORDS) if followup else WORDS.get(kind, WORDS["engineer"])
    if not lo <= n <= hi: warns.append(f"{n} words; aim for {lo} to {hi}")
    if kind == "recruiter" and not WORK_AUTH.search(body):
        warns.append("recruiter email with no work authorization line; put it up front")
    first = (contact.get("name") or "").split()[:1]
    if body and first and first[0].lower() not in body.splitlines()[0].lower():
        warns.append(f"first line does not greet {first[0]} by first name")
    for w in WEAK_SUBJECT:
        if w in subject.lower(): warns.append(f"subject wording: {w!r} reads like a mass send")
    return blocks, warns


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check one cold-email draft before queueing it.")
    ap.add_argument("--banned", default=BANNED_DEFAULT)
    ap.add_argument("--me", default=ME_DEFAULT)
    ap.add_argument("--followup", action="store_true")
    a = ap.parse_args(argv)
    try:
        contact = json.load(sys.stdin)
        banned, unconfirmed = load_banned(a.banned), load_unconfirmed(a.me)
    except (OSError, ValueError) as e:
        print(f"cannot check: {e}", file=sys.stderr); return 2
    import store   # read-only: subjects and colleagues' drafts already in the list
    blocks, warns = check(contact, banned, unconfirmed, store.load()["contacts"], followup=a.followup)
    for b in blocks: print("BLOCK:", b)
    for w in warns: print("WARN:", w)
    body = ((contact.get("draft") or contact).get("body") or "")
    print(f"{len(blocks)} blocking, {len(warns)} warnings, {len(URL.sub(' ', body).split())} words")
    return 1 if blocks else 0


if __name__ == "__main__":
    sys.exit(main())
