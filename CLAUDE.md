# Outreach

Devanshu's job-search system: finds small, hiring companies, drafts cold referral emails in his voice, and
sends them from his Gmail. The goal it serves is **replies per month**.

## Start here

1. Read GitHub issue **#9** (pinned decision record). Every number and rule (ramp, follow-ups, links, size cap,
   voice, word count) comes from #9. It wins over every doc; when a decision changes, edit #9 first.
2. Read your issue, including "Done when" and "Depends on". Check every "Done when" line before calling it done.
3. Find its plan in `docs/superpowers/plans/`. No plan yet: write one with superpowers:writing-plans before
   coding.
4. `docs/README.md` maps every doc and says which are current, evidence only, or superseded.

`gh issue view <n> -R DevanshuNEU/sept-23-companies` reads an issue.

## Order of work

Trust before features: one store, one rules function, error is not empty, a working mailer. Anything parked in
issue #7 stays parked until the Switch-over milestone closes.

## Live system

Live means real contacts and real sends.

| What | Runs from | launchd label |
|---|---|---|
| Mail sender (every 10 min) | `~/Desktop/Sept 23 Companies` (live checkout) | `com.devanshu.outreach-mail-sender` |
| Mail API (:3002) | live checkout | `com.devanshu.outreach-mail-server` |
| Dashboard (:3001) | `~/Library/Application Support/outreach-dashboard`, a copy made by `dashboard/publish.sh` | `com.devanshu.outreach-dashboard` |
| Refresh (Mon and Thu 08:00) | live checkout | `com.devanshu.outreach-refresh` |
| Job Radar (until the switch) | `~/Desktop/DevVault/job-search/pipeline` | `com.devanshu.jobradar` |

- Gmail secrets: `~/Library/Application Support/outreach-mail/`. Never print them.
- New platform store: `~/Library/Application Support/outreach/outreach.db`.
- `live_checkout.is_live()` is true only in the live checkout. Real sends and `refresh.py` refuse to run
  anywhere else.

**Only in the live checkout:** `mail/sender.py` without `--dry-run`, `refresh/refresh.py`, `dashboard/publish.sh`,
`mail/add.py` (writes the real contact list), `mail/gmail.py auth` (writes the real token). From a worktree, use
the scratch settings below.

**Scratch settings for tests and worktrees:**

| Variable | Points at | Default |
|---|---|---|
| `OUTREACH_MAIL_HOME` | contacts, config, send log | `mail/` in the checkout |
| `OUTREACH_GMAIL_DIR` | Gmail client and token | the real secrets folder |
| `OUTREACH_DB` | new platform store | the live store path |
| `OUTREACH_LIVE_ROOT` | which checkout counts as live | `~/Desktop/Sept 23 Companies` |

Claude never approves or sends an email; approval is Devanshu's click, sending is the sender's job.

In the live checkout, `refresh.py` rewrites `dashboard/data.js` and `dashboard/index.html`. Leave both files
untouched on branches so the live checkout can always fast-forward.

## Outside this repo

`~/Desktop/DevVault/job-search/outreach/` holds the outreach rulebook used for drafts: `playbook.md`,
`banned_phrases.txt`, `scripts/lint_draft.sh`, and `voice/authored.jsonl` (messages Devanshu wrote himself).
Where the playbook disagrees with #9, #9 wins; #9 lists which playbook rules and lint checks apply.

## Deploy

Merge the PR, then in the live checkout run
`git pull --ff-only https://github.com/DevanshuNEU/sept-23-companies.git master` (its SSH remote is unreachable
from agent shells), run the tests there, then:

- mail code changed: `launchctl kickstart -k gui/$(id -u)/com.devanshu.outreach-mail-server` (the sender
  starts fresh every run);
- dashboard files changed: `bash dashboard/publish.sh`.

Rollback is `git revert`, never a reset.

## Building

- Python is Homebrew's: `/opt/homebrew/bin/python3` (3.14), the same one launchd uses.
- Tests: `/opt/homebrew/bin/python3 -m unittest discover -s tests -t .` from the repo root.
- Test first: write the test, watch it go red for the right reason, then make it green. Every bug fix starts
  with a test that reproduces it. Tests never run a real refresh, a real send, or a crawl.
- Current code (`mail/`, `refresh/`, `dashboard/`) is standard library only. The new platform lives in the
  `outreach/` package; its dependencies are pinned (see #4 and #12).
- One issue per branch and PR; the PR body says which issue it closes.

## Writing

Plain words, short sentences, no em dashes or en dashes in anything a person reads: emails, docs, UI copy,
commit messages, PR bodies. Email drafts follow `.claude/skills/cold-email/SKILL.md` and use only facts from
`mail/me.md` and what Devanshu supplies about the person.
