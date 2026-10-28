# Knock

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

| What | Runs | launchd label |
|---|---|---|
| API and web app (:3100) | live checkout, `python -m outreach.api` serves `web/dist` | `com.devanshu.outreach-api` |
| Sender (every 2 min) | live checkout, `python -m outreach.sending run` | `com.devanshu.outreach-sender` |
| Crawler (every 30 min) | live checkout, `python -m outreach.crawl tick` | `com.devanshu.outreach-crawl` |
| Sources (daily 06:30) | live checkout, `python -m outreach.sources run-due` | `com.devanshu.outreach-sources` |
| Reader night job (daily 02:30) | live checkout, `python -m outreach.reader night` | `com.devanshu.outreach-reader-night` |
| Profile judge (daily 06:10, off by default) | live checkout, `python -m outreach.judge run` | `com.devanshu.outreach-judge` |

Live checkout: `~/Desktop/Sept 23 Companies`. The deploy tool renders each job from `launchd/<label>.plist.template`.
The Needs-you, prepare and plan jobs (#16, #15) install when their templates land. The prepare job (05:30) is off by
config (`[prepare] enabled = false`, company first in #9): it prints one line and exits; the 08:00 plan proposes
company rows only (`[plan] propose_people = false`). The profile judge (06:10, Part of #6 and #15) is off by config too
(`[judge] enabled = false` in `outreach/judge/config.toml`): it prints "judge is off" and exits 0; on, it makes at most
15 model calls a night and only ever adds ranking points and a visible line. The MCP server has no job: Claude
Code starts it from the registration (`python -m outreach.mcp --print-registration`).
The MCP server's guard refuses every write on `linkedin_log` and `outreach_touch` but one: `log_sent` may insert a
LinkedIn request Devanshu says he sent (a `request_sent` row and its `linkedin_request` touch under a `bridge:` key,
no outcome, no undo). Claude calls it only after he says "sent"; it never records a reply, accept, interview,
email touch or undo, and no update or delete of either table is allowed (#9, company-first ruling).

**Retired at the switch (#17), kept on disk as `.disabled` plists:** `com.devanshu.outreach-mail-sender`,
`-mail-server`, `-dashboard`, `-refresh`, and Job Radar (`com.devanshu.jobradar`, per #5). Their code (`mail/`,
`refresh/`, `dashboard/`) stays in the repo for rollback only. Nothing in the old system runs, and `mail/add.py`,
`mail/sender.py` and the old :3001 and :3002 services are not part of daily work any more.

- Gmail secrets: `~/Library/Application Support/outreach-mail/`. Never print them.
- Store: `~/Library/Application Support/outreach/outreach.db`. It holds everything; the old files are history.
- `live_checkout.is_live()` is true only in the live checkout, and nothing can change that. With a real Gmail
  token present, `python -m outreach.sending run` (including `--dry-run`) does nothing outside it;
  `python -m outreach.sending auth` exits outside it.

**Only in the live checkout:** `python -m outreach.sending run` (with or without `--dry-run`: a dry run still reads
Gmail) and `python -m outreach.sending auth` (writes the real token), `python -m outreach.crawl` (every command sends
real requests), `python -m outreach.reader night` and `eval --run` (they call a model), `python -m outreach.judge run`
(it reads company pages and calls a model; `--dry-run` runs anywhere) and the judging half of `python -m outreach.judge
eval` (elsewhere it scores only what is stored), `python -m outreach.mcp`
(the server's tools work only here), `python -m outreach.deploy install`, `retire-old` and `rollback` (they change
launchd), and `python -m outreach.importer run`, `reconcile` and `rehearse` on real data.
Claude never runs the new sender in any checkout; launchd does. From a worktree, test against the scratch
settings below. `python -m outreach.install`, and anything else that opens the default store path in any spelling
(a symlink, a relative path, other letter case), refuses to run outside the live checkout before touching any file;
with a scratch `OUTREACH_DB` it runs anywhere.

The old system's scripts (`mail/sender.py`, `mail/server.py`, `mail/add.py`, `mail/gmail.py auth`,
`refresh/refresh.py` and its steps, `dashboard/publish.sh`) are retired; do not run them. If rollback puts the old
system back (R-back in `docs/superpowers/plans/2026-10-22-import-and-switch-over.md`), the old rules for them return
with it: live checkout only, and `dashboard/data.js` and `dashboard/index.html` stay untouched on branches.

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

The same rule covers the new API (`outreach/api`: :3100 in production, `OUTREACH_API_PORT` in a workspace). Never call
its write routes (approve, unapprove, skip, save, pause, resume, release, send, and every other POST, PUT or DELETE under
`/api/`) by any means, on any port. `.claude/settings.json` denies curl, wget, httpie (`http`, `https`, `xh`), python,
node, bun and deno commands that name one of those paths, and WebFetch to a loopback host. The PreToolUse hook
`.claude/hooks/block_api_writes.py` also refuses any Bash command that names a loopback host (127.x, localhost, [::1],
0.0.0.0) together with such a path or a write method or body flag, or that builds the host from a variable. Both read
the command text only, so the rule still covers every other way: never assemble a request to get around them. A
workspace never binds 3100.

The old dashboard (`dashboard/`, :3001) and mail API (:3002) are retired. Claude never calls either, and never
serves a copy of the old dashboard on :3001. Preview its code, if ever needed, only on `:3011` (`.claude/launch.json`).

## Outside this repo

`~/Desktop/DevVault/job-search/outreach/` holds the outreach rulebook used for drafts: `playbook.md`,
`banned_phrases.txt`, `scripts/lint_draft.sh`, and `voice/authored.jsonl` (messages Devanshu wrote himself).
That folder's own `CLAUDE.md` says Claude never writes a sendable email and every draft runs `lint_draft.sh`;
for this repo #9 overrides both: Claude writes one draft per person, the checks #9 lists run inside the
drafting tools (`save_draft` and `save_followup` in `outreach.mcp`, which read `banned_phrases.txt`), and
`lint_draft.sh` is not run (it needs DevVault company files).

## Deploy

Merge the PR, then in the live checkout run
`git pull --ff-only https://github.com/DevanshuNEU/sept-23-companies.git master` (its SSH remote is unreachable
from agent shells), `uv sync`, and the tests there (`uv run python -m unittest discover -s tests -t .`), then:

- Python changed in a job (`outreach/api`, `sending`, `crawl`, `sources`, `reader`): `launchctl kickstart -k
  gui/$(id -u)/com.devanshu.outreach-<job>`. The API is the only long-running one; the others start fresh each
  run.
- web changed: `cd web && pnpm install --frozen-lockfile && pnpm build`, then kickstart the API job.
- a launchd template changed or a job was added: `uv run python -m outreach.deploy plan`, read it, then
  `uv run python -m outreach.deploy install`. `uv run python -m outreach.deploy status` shows what is loaded.
- the schema changed: `uv run python -m outreach.install` applies the new migration, then kickstart the API.
- MCP tools changed: the next Claude Code session loads the new tools; the registration needs no change.

Rollback is `git revert`, never a reset. Rolling back the whole switch is `python -m outreach.deploy rollback`
plus the steps under R-back in `docs/superpowers/plans/2026-10-22-import-and-switch-over.md`. The new store is never
deleted.

## Branching and merging

- Every PR targets `master`. Never open a PR whose base is another feature branch (no stacked PRs): a PR merged into a
  feature branch after its parent merged into master never reaches master (this happened with #67, re-opened as #68).
- One branch per issue, created from the current `origin/master`. If work B needs work A and A is not merged yet, B
  stays local (no push, no PR) until A is in master, then `git fetch && git rebase --onto origin/master <A's last
  commit> <B's branch>` and push B.
- Before asking Devanshu to merge: `gh pr view <n> --json baseRefName,mergeStateStatus` shows base `master` and `CLEAN`
  (CI green). After he merges: confirm `gh pr view <n> --json state,baseRefName` says `MERGED` into `master` and the
  commit is in `git log origin/master`.
- With more than one PR open, say the merge order. Prefer one open PR per chain of dependent work.
- Repos squash-merge, so a merged branch still looks unmerged in GitHub's network graph. Delete a branch only after its
  PR is merged into master, and only on his say-so for remote branches.
- Push local-only work early so nothing lives on one disk.

## Building

- Python is Homebrew's: `/opt/homebrew/bin/python3` (3.14), the same one launchd uses.
- Tests: `uv run python -m unittest discover -s tests -t .` from the repo root. A new checkout runs
  `uv venv --python /opt/homebrew/bin/python3 && uv sync` once first; the live checkout needs the same once
  before the deploy test run. `tests/__init__.py` points every test at scratch settings; new tests rely on that
  and never use the live paths.
- Conductor workspaces: `.conductor/settings.toml` sets each new workspace up (uv env, web packages). The web app's run script serves on that workspace's `CONDUCTOR_PORT`; run e2e there with `WEB_PORT=$CONDUCTOR_PORT pnpm e2e` so workspaces never fight over 5173. Never use 3001, 3002, 3011, 3013 or 3100.
- Test first: write the test, watch it go red for the right reason, then make it green. Every bug fix starts
  with a test that reproduces it. Tests never run a real refresh, a real send, or a crawl.
- **Drafting harness (#135): code before and after the model.** The drafter never chooses what it reads. The
  `draft_brief` tool builds its whole input in code: the person, his DevVault voice files, me.md above its open
  questions, and the limits. `save_draft` refuses a draft without a current `brief_id` (the hash of those files and the
  person), so a draft cannot skip the voice step. After the model, the voice checks in `outreach/drafting` and the
  #9 checks run on every save. The package makes no model calls. His voice rules live in DevVault and in
  `outreach/drafting`, never copied into the skill. A change that lets a draft skip the brief breaks this rule.
- The old code (`mail/`, `refresh/`, `dashboard/`) is standard library only and frozen. The platform lives in the
  `outreach/` package and `web/`; its dependencies are pinned (see #4 and #12).
- One issue per branch and PR; the PR body says which issue it closes.

## Writing

Plain words, short sentences, no em dashes or en dashes in anything a person reads: emails, docs, UI copy,
commit messages, PR bodies. Email drafts follow `.claude/skills/cold-email/SKILL.md`, which carries #9's Drafts
rules and the MCP steps; if the two ever disagree, #9 wins and the skill gets fixed. Drafts use only facts from `mail/me.md` and
what Devanshu supplies about the person; me.md's `ASK:` lines are open questions, never facts.
