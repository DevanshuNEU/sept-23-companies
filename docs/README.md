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

## Superseded (history only, do not build from)

- `superpowers/specs/2026-10-17-outreach-system-design.md`
- `superpowers/plans/2026-10-17-track-a-outreach-live.md`
