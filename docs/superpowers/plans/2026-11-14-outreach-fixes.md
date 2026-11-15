# Outreach fixes (#281) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Fix what the first referral test (a CMT recruiter) showed: drop "recruiters last", route H-1B sponsors to the Northeastern account, add Send next and Send now, check the mail server at save, fill a company's domain from an email, and bring the cold-email skill in line.

**Architecture:** Small changes in existing modules (route, queue, drafts checks, accounts lane, MCP people tools, API outbox and approve, web Outbox), one migration for the message priority.

**Spec:** GitHub #9, "Outreach fixes ruling (2026-11-14)" (`gh issue view 9 -R DevanshuNEU/sept-23-companies --json body -q .body | grep -A1 "Outreach fixes ruling"`), and issue #281.

## Global Constraints

- #9 wins. Claude never approves or sends; Send next and Send now are only his clicks in the app (API write routes); the MCP guard must refuse any Claude write of the new priority.
- Send now: next sender tick, outside the window, weekends and holidays, no spacing or company gap wait; still needs an approved message, a signed-in sending account that is not paused (his pause or a hard block), the proof gate, and every stop (no-go, bounced address, reply hold, company paused or closed, unread inbound for that company); counts toward the account's day; at most 3 urgent emails per account per New York day past the day's limit.
- Send next: first among its account's ready messages; every rule kept.
- Northeastern (sponsor) lane: the H-1B list (`outreach.h1b.lookup.tag_for`) shows total approvals over 0 for the company (merges followed), or `sponsorship = yes`. E-Verify and `opt_only` no longer decide the lane.
- No "recruiters last" anywhere: route verdict, queue `recruiter_waits`, the `recruiter` draft warning.
- Tests on scratch settings; full suite `uv run python -m unittest discover -s tests -t .` green at the end of each task; never run live commands. No em or en dashes anywhere.

## Review Focus

1. Send now must never send a message that any stop would hold, and never twice (same claim and proof path as a normal send).
2. Send now must not let one account blow past its day: the urgent cap is per account per New York day.
3. The H-1B lane must not move a company that already has a first email on another account (pins and threads win, as today).

---

### Task 1: Remove "recruiters last"

**Files:** `outreach/plan/route.py` (the `#9 recruiters last` verdict near line 186), `outreach/sending/queue.py` (`recruiter_waits`: verdict, text, the check near line 624, the API enum), `outreach/drafts/checks.py` (the `recruiter` warn near line 477: remove it from WARN_CODES and the check list), web types if the enum is shared, tests that assert any of them.

- [ ] Failing tests: a recruiter at a company with someone else on record routes `email` (or its normal verdict), not `find_someone_else`; a queued recruiter email is not held as `recruiter_waits` while another email at the company is queued; a recruiter draft has no `recruiter` warning.
- [ ] Remove the rule in all three places; regenerate OpenAPI and web types if the verdict enum changes; full suite green; commit `outreach: recruiters no longer wait for anyone (#281)`.

### Task 2: Northeastern lane from the H-1B list

**Files:** `outreach/sending/accounts.py` (`lane_of`, `routes`), tests in `tests/test_sending_accounts.py`.

**Interfaces:** `lane_of(conn, company_id) -> "sponsor" | "general"`: sponsor when `outreach.h1b.lookup.tag_for(<the company's name or merge family names>)` returns a tag with `total > 0`, or the company (or any company merged into its survivor) has `sponsorship = 'yes'`. Use the same name inputs the Outbox's H-1B tag uses (read how `api/queries/outbox.py` calls `tag_for`). With no H-1B list on disk, only the sponsorship fact counts.

- [ ] Failing tests: H-1B total 67 gives sponsor; E-Verify enrolled alone gives general; `opt_only` alone gives general; `sponsorship yes` gives sponsor; a merged-away company's H-1B line counts for its survivor; a company with an account-1 or pinned thread keeps its thread account (route order unchanged).
- [ ] Implement with `OUTREACH_H1B_DIR` in tests; full suite green; commit `sending: the Northeastern lane is H-1B sponsors (#281)`.

### Task 3: Mail server check at save, and a live warning

**Files:** move the DoH lookup used by `outreach/api/mx.py` (`check_domain`, `ensure`) to a shared module both the API and the MCP can import without the MCP loading the API or the sender (for example `outreach/drafts/mx.py`; keep `outreach/api/mx.py` as a thin re-export); call it in the MCP `save_draft` and `check_draft` paths before the checks run when the cache says unknown (outside any transaction, 5 second timeout, a failure stays `unknown`); make the Outbox detail recompute the `mail_server_unknown` warning from the current `mx_check` cache instead of showing the one stored at save.

- [ ] Failing tests (resolvers injected, no network): saving a draft for a domain with no cache row runs the lookup and stores `ok`, and the draft has no `mail_server_unknown` warn; a stored draft whose warning said unknown shows no warning in the Outbox once the cache says ok.
- [ ] Implement; full suite green; commit `drafts: check the mail server when a draft is saved (#281)`.

### Task 4: Company domain from a person's email

**Files:** `outreach/mcp/tools/people.py` (`add_people`, `intake`), `outreach/mcp/tools/corrections.py` (`update_person` when it sets an email), the store write helper they share.

- [ ] Failing tests: adding `jwarren@cmtelematics.com` at a company with no `domain` fact records `domain = cmtelematics.com` (confidence guess, source kind the one Knock uses for derived facts, source ref the person id); a free-mail address (gmail.com, outlook.com and the list Knock already keeps, if any) records nothing; a company that already has a domain is left alone.
- [ ] Implement through the existing evidence write path (the MCP guard must allow only this insert for that tool, or the write must go through a function the guard already permits; check guard tests); full suite green; commit `people: an added email fills the company's domain (#281)`.

### Task 5: Send next and Send now

**Files:** migration `outreach/store/migrations/0019_send_priority.sql` (next free number; check master), `outreach/sending/queue.py` (order and the urgent path), `outreach/sending/tick.py` (urgent sends first in the send step), `outreach/sending/claim.py` (claim unchanged except it reads nothing new), `outreach/api` (the approve route takes `priority: "normal" | "next" | "now"`, or two routes; and a route to change priority of an approved message), `outreach/mcp/guard.py` (Claude may never write the priority), web Outbox (buttons beside Approve: "Send next" and "Send now", Send now with a one-line confirm), tests.

**Store:** `message.priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('normal','next','now'))`, plus `urgent_at` timestamp when set to now.

**Rules (exact):** see Global Constraints. Urgent cap is 3 per account per New York day, counted from messages sent with priority `now`. A Send now that cannot go (a stop, the account signed out or paused) stays approved with its reason shown; it never waits silently.

- [ ] Failing tests: Send now on Saturday 18:00 ET goes on the next tick; Send now still refused for a bounced address, a no-go, a reply-held company, a paused account; Send now past the day's limit goes for the first 3 and the 4th waits as `daily_limit`; Send next goes before older approved messages of the same account and still waits for the window; the MCP guard refuses an update of `priority`; the API sets it only through the approve route.
- [ ] Implement; web build and tests; full suite green; commit `outbox: Send next and Send now (#281)`.

### Task 6: Skill and docs

**Files:** `.claude/skills/cold-email/SKILL.md` (follow-ups: one, at 7 business days; no recruiters-last; the Northeastern lane for H-1B sponsors; Apollo steps: search first, reveal only after Devanshu says yes, verified only), `CLAUDE.md` (Send now and Send next in the live system notes), `docs/README.md` (this plan).

- [ ] Edit, check for dashes, commit `docs: cold-email skill and CLAUDE.md follow the outreach fixes (#281)`.
