# Reach out from a job (design, 2026-11-14)

Status: draft, decisions made by Claude on Devanshu's instruction ("answers khud hi dena hai"), with his two rules
from the same chat: Knock finds the people itself, and he sees and approves the picks before any credit is spent on
revealing an email.

## Why

He finds jobs in Knock's job list and wants volume before the holidays. Today, reaching people for a job means Apollo
in a separate Claude session, copy and paste, `add_people`, the cold-email skill, the Outbox. That caps volume, and
Knock never knows which job an email is about. The 2026-11-06 idea (Go ahead finds the people and drafts) was researched
and never built.

## What he sees

1. On a job row in Jobs, and on the company page, a **Reach out** button.
2. Within a few minutes the row shows **"4 people to check"**. Opening it lists each proposed person: name, title,
   city, why they were picked ("campus recruiter, Arizona", "engineering manager, Chandler"), and their LinkedIn link.
   No email is shown or bought yet.
3. He ticks the ones he wants and clicks **Get emails and draft**. That click is his approval, the same as pasting
   people he found himself.
4. Knock reveals emails for the ticked people only, keeps the ones Apollo marks verified, and drafts one email each
   about that job. The drafts land in the Outbox as now, each showing "for: <job title>".
5. He approves in the Outbox; the sender sends from the right account, inside the window and the ramp.
6. A person whose email is not verified is kept without an email, on the LinkedIn list, with a note.

## Who Knock picks (my calls)

- Per job: 1 recruiter (campus, university, early career or technical recruiter first, then talent acquisition), 1
  engineering manager (software engineering manager, then director of engineering), and 2 engineers (senior or staff
  software engineer). At a company of 200 or fewer: 1 recruiter or founder, 1 manager, 1 engineer.
- Location first: the posting's city, then its state, then the US. A remote posting uses the US.
- Team words from the posting title (for example "DevOps", "AI", "Platform") rank people whose title shares them.
- Never proposed: anyone already on record at the company, a no-go person or company, an address on the never-send
  list, and anyone outside the US.
- One open request per company at a time. A second job at the same company while people there wait for replies shows
  "already reaching out at <company> for <job>" and proposes no one new.

## How it works

- **Apollo is called by code, not by the model.** A small adapter (`outreach/people/apollo.py`) uses Apollo's REST API
  with an API key from his Apollo account, kept in the secrets folder (never in the repo). People search returns names,
  titles and locations without emails and, per Apollo's docs, spends no credits; revealing an email spends one. The
  adapter has a daily reveal cap in config (default 40) and a per-request cap (4).
- **The click only writes a row.** `POST /api/jobs/{postingId}/reach` (his click) adds a `reach_request` row. A launchd
  job, `com.devanshu.outreach-reach`, every 10 minutes, does the slow work: search and propose (no credits), and after
  his tick, reveal and draft.
- **Drafting reuses the follow-up drafter's pattern.** Code builds the brief (`draft_brief`), the model is text in and
  text out (`claude -p`, no tools), and code saves with `save_draft`, so every #9 and voice check runs. The posting is
  the hook. Company research reuses `outreach.research company` when the company has none from the last 30 days.
- **Backlog guard.** The job stops drafting while drafted plus approved first emails exceed 3 days of the sending
  accounts' combined limit, so drafts never go stale in the Outbox. Proposals still appear.
- **A job not in Knock.** A pasted job link with a company Knock knows makes a request too; a company Knock does not
  know gets a Needs-you line with the by-hand add command.

## Store

- `reach_request`: request_id, company_id, posting_id (nullable), url, state (`searching`, `to_check`, `revealing`,
  `drafting`, `done`, `failed`, `skipped`), reason, requested_at, updated_at, revision.
- `reach_candidate`: candidate_id, request_id, Apollo person id, name, title, city, state, linkedin_url, role_kind,
  why, picked (0 or 1), person_id once added, email_status.
- `touch.posting_id` (nullable): the job a first email is about, shown in the Outbox.
- The MCP guard never lets Claude write these tables; only his clicks (API) and the reach job do.

## Reputation rules this keeps (#9, 2026-11-13)

Verified emails only; the never-send list is checked before any reveal; his two clicks (pick, then approve) stay;
the sender's ramp, window, one company one account, and pauses are unchanged; drafts follow the handwritten voice rules,
so no two emails at a company read alike.

## Approaches considered

1. **Code adapter plus a scheduled job (chosen).** Spend is capped in code, the model only writes text, and it runs
   without his terminal open. Needs an Apollo API key on his plan.
2. Apollo through the MCP in a headless `claude -p` run. No API key, but the model would choose and spend, and the MCP
   is set up only for the DevVault project.
3. A skill he runs by hand ("reach out for my queue") in his Claude session. Works today with no key, but only when he
   runs it. Kept as the fallback if his plan has no API access.

## Open item for him

An Apollo API key (Settings, Integrations, API in Apollo). If his plan has no API access, the fallback is approach 3.

## Order

1. Spike: one people search for Applied Materials with his key (no reveal), to confirm search spends no credits and
   returns titles and cities.
2. Store, API route, Reach out button, proposals view.
3. The reach job: search and propose.
4. Reveal and draft after his tick.

## People per company: the two-arm test (2026-11-14, agreed with Devanshu)

Research (`docs/research/2026-11-14-who-replies.md`, and the job search, contacts, risk and model notes under
`.context/research/2026-11-14-*`) found no job search data on how many people per company to contact. The only
per-employer method with numbers (Dalton, The 2-Hour Job Search) goes one or two at a time, and one referral per role is
what counts at big companies. Devanshu wants far more people. So Knock runs both, measures, and keeps the winner.

**Arms.** Each company is put in arm A or arm B by a coin flip, stratified by size band, the first time it gets a
request. It keeps its arm.

| Size | Arm A (his numbers) | Arm B (evidence) |
|---|---|---|
| Under 50 | up to 10 per company | up to 3 per company |
| 50 to 200 | up to 30 per company | up to 6 per role, 10 per company a month |
| 200 to 1,000 | up to 40 per company, 15 per role | up to 6 per role, 15 per company a month |
| Over 1,000 | up to 50 per company a month, 15 per role or team | up to 5 per role or team, 50 per company a month |

**Same in both arms.** Waves of at most 2 first emails a day per company; the order of roles by size from the research
(founder first under 50; manager and recruiter first at 50 to 200; recruiter first above 200; never executives at big
companies, agencies, interns or people outside the US); the next wave only while the company is silent; any human reply
stops new people at that company; a bounce or stop reply stops the company; verified emails only; the never-send list;
his pick and his Outbox approval; the sender's ramp, window, one account per company and pauses.

**Company status.** warm (a human reply that is not a no), no (drop 90 days), silent closed (arm cap reached and 10
business days passed; closed for that role, reopens on a new role, new people in January; never avoided for silence
alone), applied and rejected (from Applied).

**Logged on every first email.** arm, size band, wave, role kind, sender account, the recipient's mail provider (Google,
Microsoft or other, from MX), posting, reply class (none, auto, human positive, human no, stop), referral and interview
when he marks them.

**Review.** Every Monday a table by arm and size band: emails, companies reached, companies with a human reply, positive
replies, referrals, interviews, bounces, stop replies, hard pauses, placement test result. Decision at two weeks or 30
companies per arm, whichever is later: arm A wins if its companies-with-a-positive-reply rate is at least 1.5 times arm
B's with no more spam signals; else arm B wins. **Early stop for arm A:** any account hard pause, a policy rejection from a
company's domain, or a spam complaint signal pauses arm A until he reviews.

**Calendar.** No sends on Nov 26 and 27; from Dec 16, first waves only.

**Build order.** (1) Monday 2026-11-16: Applied Materials runs by hand in a Claude session with Apollo, as the first
case. (2) This week: the reach tables, arms, logging and the Monday review. (3) Then the scheduled job with an Apollo API
key, if his plan has API access.
