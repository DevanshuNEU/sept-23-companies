# Outreach

Devanshu's job-search system: finds small, hiring companies, drafts cold referral emails in his voice, and
sends them from his Gmail. The goal it serves is **replies per month**.

## Where decisions live

- **Decisions:** GitHub issue #9 (pinned). It wins over every doc; when a decision changes, edit #9 first.
- **Design:** `docs/superpowers/specs/2026-10-18-outreach-platform-design.md`. Files marked "Superseded" are
  history only.
- **Work:** one GitHub issue per piece, grouped by milestone. Read the issue's "Done when" before starting and
  check every line before calling it done.
- **Evidence:** `docs/research/`. Sending limits and stop rules come from
  `2026-10-18-cold-email-deliverability.md`.

## Order of work

Trust before features: one store, one rules function, error is not empty, a working mailer. Anything parked in
issue #7 stays parked until the Switch-over milestone closes.

## Live system

Live means real contacts and real sends. The live checkout is `~/Desktop/Sept 23 Companies`; launchd runs
everything from there:

- `com.devanshu.outreach-mail-sender` (every 10 min), `com.devanshu.outreach-mail-server` (:3002),
  `com.devanshu.outreach-dashboard` (:3001), `com.devanshu.outreach-refresh` (Mon and Thu 08:00),
  `com.devanshu.jobradar` (until the switch).
- Secrets: `~/Library/Application Support/outreach-mail/` (Gmail client and token). Never print them.
- New platform store: `~/Library/Application Support/outreach/outreach.db`.

Worktrees work on scratch copies: tests point `OUTREACH_MAIL_HOME` (and the store path) at a temp directory,
so nothing a worktree runs can touch live contacts, the store, or the Gmail account. Claude never approves or
sends an email; approval is Devanshu's click, sending is the sender's job.

In the live checkout, `refresh.py` rewrites `dashboard/data.js` and `dashboard/index.html`. Leave both files
untouched on branches so the live checkout can always fast-forward.

## Deploy

Merge the PR, then in the live checkout run
`git pull --ff-only https://github.com/DevanshuNEU/sept-23-companies.git master` (its SSH remote is unreachable
from agent shells), run the tests there, then `launchctl kickstart -k gui/$(id -u)/<label>` for any service
whose code changed. Rollback is `git revert`, never a reset.

## Building

- Python is Homebrew's: `/opt/homebrew/bin/python3` (3.14), the same one launchd uses.
- Tests: `/opt/homebrew/bin/python3 -m unittest discover -s tests -t .` from the repo root.
- Test first: write the test, watch it go red for the right reason, then make it green. Every bug fix starts
  with a test that reproduces it.
- Current code (`mail/`, `refresh/`, `dashboard/`) is standard library only. New-platform dependencies are
  pinned.
- One issue per branch and PR; the PR body says which issue it closes.

## Writing

Plain words, short sentences, no em dashes or en dashes in anything a person reads: emails, docs, UI copy,
commit messages, PR bodies. Email drafts follow `.claude/skills/cold-email/SKILL.md` and use only facts from
`mail/me.md` and what Devanshu supplies about the person.
