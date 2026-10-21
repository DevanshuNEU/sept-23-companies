# Docs map

Decisions live in GitHub issue #9. This folder holds the design, plans, and the research behind them.

## Current

| File | Use it for |
|---|---|
| `superpowers/specs/2026-10-18-outreach-platform-design.md` | The architecture around #9: store, rules, fetching, sending, MCP, API and app, switch-over |
| `superpowers/plans/2026-10-18-fetch-ok-empty-error.md` | Plan for #3 (done, merged in PR #19); a worked example of the plan format |
| `superpowers/plans/2026-10-19-monday-readiness.md` | Plan for #10's drafting half: `mail/check_draft.py`, `mail/me.md`, the cold-email skill |
| `superpowers/plans/2026-10-19-store-and-rules.md` | Plan for #4: the SQLite schema, store safety, the rules module, and which tables each later issue uses |
| `design/README.md` and `design/*.html` | #12's UI design: the seven views, keyboard map, visual system, and the data each view needs (mockups approved by Devanshu) |
| `superpowers/plans/2026-10-19-web-app.md` | Plan for #12 phase 1: the React app in `web/` against a fixture client, swapped for #11's API in phase 2 |
| `superpowers/specs/2026-10-20-opportunity-pipeline-design.md` | Approved 2026-10-21: finding every reachable company, diff-only reads, reading postings through Claude Code, ranking, the daily people plan, outcomes, how the app should feel; #9 edited to match |
| `superpowers/plans/2026-10-21-pipeline-foundation.md` | #30: the one schema change, shared contracts and formats, contract checks and the pipeline flow test |
| `superpowers/plans/2026-10-21-sources-and-identity.md` | #31: DOL LCA, stapply, Form D, HN and by-hand sources; one company per domain; work tiers |
| `superpowers/plans/2026-10-21-diff-crawler.md` | #21: board adapters, unchanged reads, bodies, cadence, watched careers pages |
| `superpowers/plans/2026-10-21-posting-reader.md` | #32: the read queue, quote checks, the night job through Claude Code, the labeled set and eval |
| `superpowers/plans/2026-10-21-ranking.md` | #6: lanes, hides with evidence, config-weighted signals with reasons, stored scores |
| `superpowers/plans/2026-10-21-daily-people-plan.md` | #15: Today as a plan of people, capacity, the prepare step, LinkedIn notes and checks |
| `superpowers/plans/2026-10-21-api.md` | #11: FastAPI, generated types, idempotent writes, outcomes, the HTTP client that replaces the fixtures |
| `superpowers/plans/2026-10-21-web-experience.md` | #33: the people plan, outcomes, progress, wins and the finish line (mockups first) |
| `superpowers/plans/2026-10-21-integration-review.md` | Cross-plan review of the eight plans and how each finding was resolved |
| `web/` (how to run) | Needs Node 20.19 (see `web/.nvmrc`; `pnpm install` fails on other majors, engines are strict). `cd web && pnpm install && pnpm dev`, then open http://127.0.0.1:5173. Add `?scenario=hard_block` (or `signed_out`, `empty`, `weekend`, `job_failed`, `error`, `many_sent`) to see each state |

Every other issue gets its own plan here before coding starts.

## Evidence only

Research reports. Their facts and measurements stand; their **Recommendation** sections do not decide anything.
Where one disagrees with #9, #9 wins. Each file opens with a note listing what #9 overruled.

| File | Still useful for |
|---|---|
| `research/2026-10-18-cold-email-deliverability.md` | Gmail limits, how filters work, bounce and stop signals |
| `research/2026-10-17-current-system-audit.md` | How the old code works and where it misfires (rules regression cases for #4) |
| `research/2026-10-17-data-lifecycle-and-reliability.md` | SQLite settings and measured data volumes (#4) |
| `research/2026-10-17-crawl-and-scheduling.md` | ATS endpoints, ETags, launchd behavior across sleep (#21) |
| `research/2026-10-17-growth-signals.md` | Form D, YC, HN and wage-level sources (deferred, #7) |
| `research/2026-10-17-outreach-and-tracking.md` | Gmail and LinkedIn rules, CRM options considered |
| `research/2026-10-20-outreach-history.md` | His recorded outreach April to September 2026: who replied, by stage, role and channel |
| `research/2026-10-20-lca-small-junior-employers.md` | Small employers that filed junior software H-1Bs in FY2025, the staffing filter, coverage of his lists |
| `research/2026-10-20-visa-rules-and-employer-targeting.md` | OPT, STEM OPT, the weighted lottery, fee rules and cap-exempt employers as of October 2026 |
| `research/2026-10-20-company-discovery-and-job-data.md` | Which job boards small sponsors use, board discovery, ETag and diff support, free and paid sources |
| `research/2026-10-20-llm-posting-extraction.md` | Reading postings with a model: Claude features, measured volumes and cost, accuracy evidence, schema |
| `research/2026-10-20-people-and-channels.md` | Referral evidence, who to contact, LinkedIn rules and limits, finding emails safely |

## Superseded (history only, do not build from)

- `superpowers/specs/2026-10-17-outreach-system-design.md`
- `superpowers/plans/2026-10-17-track-a-outreach-live.md`
