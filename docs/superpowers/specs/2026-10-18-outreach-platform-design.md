# Outreach platform v2: design

Date: 2026-10-18. Owner: Devanshu Chicholikar. Status: agreed (grilling session, 2026-10-18).

Source of truth for decisions: GitHub issue #9 (pinned decision record). This document adds the architecture
around those decisions. Where the two disagree, #9 wins and this file gets fixed. It replaces
`2026-10-17-outreach-system-design.md`, whose Modal crawler, paid API research and dated build order no longer
apply.

Research: `docs/research/2026-10-18-cold-email-deliverability.md` (sending limits and stop rules) and the
2026-10-17 reports (`current-system-audit`, `data-lifecycle-and-reliability`, `crawl-and-scheduling`,
`growth-signals`, `outreach-and-tracking`).

## 1. What it is for

Devanshu is an F-1 OPT new grad who needs a job in November or December. Referrals get him interviews;
cold applications have not. The platform finds small, hiring companies, helps him reach the right people
there by cold email (and tells him whom to reach on LinkedIn), writes drafts in his voice through a Claude Code
session he keeps open, and sends them safely from his Gmail.

**Success metric:** replies per month.

## 2. Principles

1. **One source of truth.** One SQLite store. Nothing important lives in browser storage, loose JSON or
   markdown.
2. **One rules function.** Every posting is decided once, in Python. The UI shows stored decisions and never
   classifies anything itself.
3. **Error is not empty.** Every board read is ok, empty or error. An error never closes a company.
4. **Humans approve, the sender sends.** Claude can read, add people, write drafts and raise flags. Only
   Devanshu approves. Only the sender sends.
5. **Protect the inbox.** The Gmail account that receives replies is the asset. Sending adapts to evidence
   and stops on bad signals.
6. **Correctness before features.** Anything not on the switch-over list waits (#7).

## 3. Shape

```
  side terminal: Claude Code (Northeastern Enterprise seat)
        |  stdio
        v
  MCP server (outreach/mcp)  ----+
                                 |
  React app (web/) <--HTTP--> FastAPI (outreach/api) ----> SQLite store <---- sender (Gmail API)
        ^  generated TS types         |                       ^
        |                             |                       |
   Devanshu                     rules module              crawler + refresh jobs (launchd)
```

Everything runs on his Mac. The store is
`~/Library/Application Support/outreach/outreach.db`. Secrets (Gmail client and token) stay in
`~/Library/Application Support/outreach-mail/`.

## 4. Store and entities

SQLite, WAL, STRICT tables, foreign keys on, canonical UTC timestamps. Schema changes are ordered migrations run
only by an install command, never on open. Tests and worktrees use a scratch copy, never the live file.

| Entity | Holds | Notes |
|---|---|---|
| company | identity, domain, aliases, status (active, backlog, removed, done, no-go) | facts live in evidence, not columns |
| evidence | one fact (size, stage, sponsorship, domain, HQ) with source, quote, confidence, locked flag | hand values are locked and win |
| board | ATS + token, last read result (ok, empty, error), error and empty streaks | one row per board |
| posting | board, native id, title, location, url, first seen, last seen, closed at and reason, pay | never deleted silently |
| decision | posting x ruleset version: in lane or not, level tier, US, sponsorship stance, citizenship, years asked | rebuilt when rules change |
| person | company, name, title, role kind (engineer, manager, founder, recruiter, alum), email, LinkedIn, notes | |
| touch / message | one email sequence per person: draft, approval, claim, send, follow-ups 1 to 3, replies | the state machine from `mail/store.py`, extended |
| linkedin_log | requests, accepts, messages, replies he logs | |
| review_item | the five needs-you triggers with reason and resolution | resolved items stay resolved unless evidence changes |
| daily_list | proposed, picked, backlog, removed, by date | |
| events | append-only history for company, touch and application changes | every human action is an event |
| job_run | every scheduled job: start, end, result, counts, error | feeds a health view |

The store imports everything that exists today (#17): `dashboard/data.js`, the browser's `outreach.v1`
export, `mail/contacts.json`, `radar.db`, the old devboard `job-state.json`, `contacted.txt` and DevVault
company notes. Every source record is kept verbatim for audit.

## 5. Rules

One Python module decides every posting and every company filter. Its source plus its config hash to a
ruleset version; changing rules rebuilds decisions and lists every posting that flips from shown to hidden.

- **Level:** new grad, early career, SWE I, SWE II (MTS and FDE included); postings asking up to 3 years.
- **Hidden:** "we don't sponsor" (company goes to no-go); citizenship or clearance required; size over 300 with
  evidence. Size guesses stay visible and raise a review item.
- **Not hidden:** OPT-only, 2027 starts.
- **Order:** open role at his level first, then any open US engineering role; freshest role, then confirmed
  evidence.
- Regression tests cover every misfire in the audit (MTS, SWE II, sponsorship negation, US cities, the "small"
  default, "Bachelor's or 4 years").

## 6. Fetching

Board reads return ok, empty or error (error = non-200, network failure, timeout, unreadable body). Error keeps
the previous state. Empty closes postings only after two empty reads in a row. Three errors in a row raise a
review item. The existing refresh keeps running on launchd until the switch; #21 builds the crawler that writes the
store. The cloud crawler is deferred (#7).

## 7. Daily flow

1. 08:00 weekdays: the daily list proposes 15 to 20 companies and shows how many today's send limit can carry
   (5 early, 10 once ramped). He picks; the rest go to backlog or are removed.
2. In the side terminal he pastes people for a picked company. Claude calls `add_people`, then writes one draft
   per person and calls `save_draft`. Recruiter titles get a warning he can override.
3. In the Outbox he reviews each draft with its check results and approves.
4. The sender sends inside the rules (section 8). Replies hold the whole company and appear at the top.
5. He closes the company with a reason (referral, interview, no sponsorship, no referrals, already hired, not
   now, other). "No sponsorship" adds the company to no-go.
6. People beyond the email budget appear on the LinkedIn list.

## 8. Sending

Ported from the fixed mailer (PR #1, issue #2): claim with Message-ID before calling Gmail, reconcile unclear sends against
Sent, ignore auto-replies, prove the token works, never call Gmail under a lock. Added rules (#14):

- **Ramp:** 20 a day, +10 per clean sending day, ceiling 100. Clean = bounces under 2% over 3 days, no Gmail
  limit or policy errors, no spam or stop replies. A warning steps down; a hard block pauses and alerts.
- **Per company:** at most 3 a day, 60 minutes apart, best 6 to 8 people. Recruiters outside this limit, inside
  the daily limit, on day 2.
- **Window:** weekdays 08:30 to 16:30 recipient time.
- **Follow-ups:** 3, at 3, 7 and 15 business days, in thread; each adds one new thing; the third closes politely.
- **Content:** plain text, two links (portfolio first, then project), no tracking, no attachments, unique subject.
- **Approve checks:** safety blocks (no mail server for the domain, too many links or an attachment or a
  shortener, near-identical to a colleague's draft, no-go or said-no person, company closed for no
  sponsorship); style warns (banned phrases, em dashes, length, stale hook, subject wording).
- One sender account. No second account or domain.

## 9. Drafts and voice

Claude in the side session writes every draft. Voice: his, cleaned up: short sentences, contractions, plain
words, a blunt ask, humble and kind, "I want in" energy, no typos, about 80 to 120 words. The DevVault playbook
(`job-search/outreach/playbook.md`, `banned_phrases.txt`) is the rulebook, adjustable to the market. Facts come
only from `mail/me.md` and what he pastes about the person. Real samples of his writing (#8) are the best guide.

## 10. MCP server (#13)

Local stdio server, official `mcp` Python package, pinned, registered in the project's `.mcp.json`. Tools:
`todays_companies`, `company`, `add_people`, `save_draft`, `save_followup`, `flag_review`, `mark_no_go`. Every tool
validates input against a strict schema and rejects bad input whole. No tool approves or sends. Tools return
only the fields a step needs, because rows enter Claude's context.

## 11. API and app (#11, #12)

FastAPI bound to 127.0.0.1; its OpenAPI schema generates the TypeScript types the React app uses. Writes check
Origin and Host, are idempotent, and carry a revision so two tabs cannot overwrite each other.

React + Vite + shadcn/ui, reusable components, mockups approved before build. Seven views must exist before the
switch: Today, LinkedIn list, Outbox, Needs you, No-go, Company page with full history, Done companies.

## 12. Switch-over (#17, #5)

One move, on a Sunday (no sends), when the seven views work on imported data: stop the old sender and server,
final browser export, import with an in/out/skipped report that must show no unexplained loss, reconcile Sent,
start the new services, retire the old dashboard and refresh, unload Job Radar and keep `radar.db` as a
read-only archive. Until then the old :3001 dashboard and current mailer stay unchanged.

## 13. Deferred (#7)

Cloud crawler, growth signals, automatic research briefs, theses and trends, backups and monitoring, an
embedded Claude terminal, publishing the Google OAuth app.

## 14. Build conventions (new platform)

Decided 2026-10-18 so the platform issues agree with each other.

| Topic | Convention |
|---|---|
| Python package | `outreach/` at the repo root (`outreach/store`, `outreach/rules`, `outreach/crawl`, `outreach/api`, `outreach/mcp`, `outreach/sending`) |
| Store path | `OUTREACH_DB`, default `~/Library/Application Support/outreach/outreach.db`; tests and worktrees always set it to a scratch file |
| Create and migrate | `python3 -m outreach.install` (creates the store, applies migrations); against the live path only from the live checkout (`live_checkout.require_live`) |
| Python dependencies | `pyproject.toml` with exact pins, managed by `uv` (`uv sync`, `uv run ...`); the old `mail/`, `refresh/` code stays standard library only |
| Rules config | `outreach/rules/config.toml`; its hash plus the rules source make the ruleset version |
| API | FastAPI on `127.0.0.1:3100`; serves `/api/*` and, in production, the built React app |
| Web app | `web/`: React + Vite + shadcn/ui, TypeScript, Node 20 (nvm), `pnpm`; dev server on `:5173` proxies `/api` to `:3100` |
| API types | `openapi-typescript` generates `web/src/api/schema.ts` from FastAPI's OpenAPI schema; regenerated whenever the API changes |
| MCP server | `outreach/mcp`, official `mcp` package, stdio, registered in `.mcp.json` |
| Ports in use today | `:3001` old dashboard, `:3002` old mail API (both retire at the switch) |
| Mockups | `docs/design/`, approved by Devanshu before #12 code starts |
