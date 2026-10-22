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
- `live_checkout.is_live()` is true only in the live checkout, and nothing can change that. With a real Gmail
  token present, `mail/sender.py` (including `--dry-run`) does nothing outside it; `refresh/refresh.py` and
  `mail/gmail.py auth` exit outside it.

**Only in the live checkout:** `mail/sender.py` (with or without `--dry-run`: a dry run still reads Gmail and saves
contacts), `mail/server.py` (it owns :3002), `refresh/refresh.py` and its individual steps (`finalcheck.py`,
`final_merge.py`, `write_level.js` rewrite `dashboard/data.js`), `dashboard/publish.sh`, `mail/add.py` (writes the
real contact list), `mail/gmail.py auth` (writes the real token). From a worktree, test against the scratch
settings below. `python -m outreach.install`, and anything else that opens the default store path in any spelling
(a symlink, a relative path, other letter case), refuses to run outside the live checkout before touching any file;
with a scratch `OUTREACH_DB` it runs anywhere.

**Scratch settings for tests and worktrees:**

| Variable | Points at | Default |
|---|---|---|
| `OUTREACH_MAIL_HOME` | contacts, config, send log | `mail/` in the checkout |
| `OUTREACH_GMAIL_DIR` | Gmail client and token | the real secrets folder |
| `OUTREACH_DB` | new platform store | the live store path (opening it outside the live checkout is refused, #4) |
| `OUTREACH_SOURCES_DIR` | downloaded source files (LCA xlsx, stapply lists, Form D sets) | `~/Library/Application Support/outreach/sources` |

Claude never approves or sends an email; approval is Devanshu's click, sending is the sender's job. Never call
the mail API on :3002 (`/api/approve`, `/api/save`, `/api/skip`, `/api/unapprove`, `/api/pause`) by any means;
`.claude/settings.json` denies the obvious curl forms, and the rule covers every other way too.

The old dashboard's code (`dashboard/app.js`) always talks to the live mail API on `127.0.0.1:3002`. Preview it only
on `:3011` (`.claude/launch.json`): the mail API accepts calls only from pages on `:3001`, so a preview there
cannot read or change real contacts. Never serve a copy on `:3001`.

In the live checkout, `refresh.py` rewrites `dashboard/data.js` and `dashboard/index.html`. Leave both files
untouched on branches so the live checkout can always fast-forward.

## Outside this repo

`~/Desktop/DevVault/job-search/outreach/` holds the outreach rulebook used for drafts: `playbook.md`,
`banned_phrases.txt`, `scripts/lint_draft.sh`, and `voice/authored.jsonl` (messages Devanshu wrote himself).
That folder's own `CLAUDE.md` says Claude never writes a sendable email and every draft runs `lint_draft.sh`;
for this repo #9 overrides both: Claude writes one draft per person, the checks #9 lists run inside the
drafting skill (`mail/check_draft.py`, which reads `banned_phrases.txt`), and `lint_draft.sh` is not run (it
needs DevVault company files).

## Deploy

Merge the PR, then in the live checkout run
`git pull --ff-only https://github.com/DevanshuNEU/sept-23-companies.git master` (its SSH remote is unreachable
from agent shells), run the tests there, then:

- mail code changed: `launchctl kickstart -k gui/$(id -u)/com.devanshu.outreach-mail-server` (the sender
  starts fresh every run);
- dashboard files changed: `bash dashboard/publish.sh`.

Rollback is `git revert`, never a reset. Deploy steps for the new platform (`uv sync`, the web build, its
launchd jobs) are written as part of #17.

## Building

- Python is Homebrew's: `/opt/homebrew/bin/python3` (3.14), the same one launchd uses.
- Tests: `uv run python -m unittest discover -s tests -t .` from the repo root. A new checkout runs
  `uv venv --python /opt/homebrew/bin/python3 && uv sync` once first; the live checkout needs the same once
  before the deploy test run. `tests/__init__.py` points every test at scratch settings; new tests rely on that
  and never use the live paths.
- Conductor workspaces: `.conductor/settings.toml` sets each new workspace up (uv env, web packages). The web app's run script serves on that workspace's `CONDUCTOR_PORT`; run e2e there with `WEB_PORT=$CONDUCTOR_PORT pnpm e2e` so workspaces never fight over 5173. Never use 3001, 3002, 3011 or 3013.
- Test first: write the test, watch it go red for the right reason, then make it green. Every bug fix starts
  with a test that reproduces it. Tests never run a real refresh, a real send, or a crawl.
- Current code (`mail/`, `refresh/`, `dashboard/`) is standard library only. The new platform lives in the
  `outreach/` package; its dependencies are pinned (see #4 and #12).
- One issue per branch and PR; the PR body says which issue it closes.

## Writing

Plain words, short sentences, no em dashes or en dashes in anything a person reads: emails, docs, UI copy,
commit messages, PR bodies. Email drafts follow `.claude/skills/cold-email/SKILL.md`, which carries #9's Drafts
rules; if the two ever disagree, #9 wins and the skill gets fixed. Drafts use only facts from `mail/me.md` and
what Devanshu supplies about the person; me.md's `ASK:` lines are open questions, never facts.
