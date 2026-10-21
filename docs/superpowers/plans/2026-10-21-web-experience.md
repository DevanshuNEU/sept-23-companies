# Web experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the phase-1 React app into the experience design section 16 asks for: Today as a plan of about 7 people with one-key outcome logging, a finish line, honest progress, a win log, a quiet-week view, monthly findings, the 7-day "what happened?" prompt, a watch list and a per-signal score breakdown on the company page, all on fixtures first.

**Architecture:** Same seam as phase 1. Views import only from `@/api`; `OutreachClient` gains new reads and writes; the `FixtureClient` implements them over new fixture data; #11's generated types later replace the hand-written ones without touching a view. Every write goes through the existing pending store (8 second undo, overlay patches, flush on leave) and carries a stable idempotency key. Visual work follows the approved design system and is gated by mockups Devanshu sees first (Task 1). The UI never classifies: reasons, rates' inputs, "enough data", wins and finding text come from the client; the UI only formats numbers and lays them out.

**Tech Stack:** Node 20.19.5 (nvm), pnpm 10.28.1, Vite 8, React 19, TypeScript 5.9 strict, Tailwind 4, shadcn/ui (new-york, zinc), TanStack Query 5, React Router 7, Sonner, Vitest 4 with Testing Library, Playwright 1.64. No new packages.

**Spec:** `docs/superpowers/specs/2026-10-20-opportunity-pipeline-design.md` (sections 2, 8, 9, 10, 11, 16 as the bar for every view, 17, 18); `docs/superpowers/plans/2026-10-21-pipeline-foundation.md` (tables `outreach_touch`, `application`, `company_score`, `daily_list_entry.person_id` and `channel`, `read_queue` is not used here); `docs/design/README.md` and `docs/design/*.html` (the approved visual system and keyboard map, extended here, not replaced); `docs/superpowers/plans/2026-10-19-web-app.md` (conventions: tasks, fixtures first, Vitest, Playwright with `WEB_PORT`). Issue: #33. Decision record: #9 (changes of 2026-10-21).

**Branch:** `DevanshuNEU/web-experience`, from the tip of the `web-views` branch (phase 1, #12), rebased on master once #12 merges. One PR, body says "Closes #33".

## Controller rulings (2026-10-21). These override the task text below where they disagree.

1. **Task 1 mockups are a gate:** Devanshu sees the six visual decisions before Task 7. Tasks 2 to 6 may start now.
2. Keep pick and backlog at `/proposals` until the switch. Email rows show "Review in Outbox" with no logging key.
3. **Recognition and the win log are different.** Any accept or reply gets the moment of recognition (design 16.4).
   The win log keeps referrals, interviews, offers and replies tagged Interested. "Not now" counts as a reply in
   progress, not as a win; "Said no" and "Asked to stop" are neither.
4. **The 7-day prompt:** "Nothing yet" asks once more 7 days later; after 21 days with no outcome the touch is
   marked quiet on its own, without asking again.
5. Minimum samples as proposed: 30 requests before a rate shows, 20 per finding group.
6. The note limit comes from the API (default 200 characters, which fits free and Premium).
7. **Corrections:** no take-back on the main path after the 8-second window, but every outcome can be corrected
   from the touch's history on the company page (a revisioned write), so a wrong log never sticks.
8. **One touch per action:** the API writes `outreach_touch` and `linkedin_log` together; progress reads
   `outreach_touch` only (now in #30). Applications are touches with no person (now allowed by #30).
9. The API supplies every derived field the UI shows (row state, reason and sources, `enough`, `quiet`, win line,
   score share, funnel denominators, tomorrow's preview); the UI computes none of them.
10. **The reason line is his.** `NoteDraft` carries `reasonLine` and `reasonState` (`proposed` by Claude, `confirmed`
    by his key, `written` when he edits the line). `needsYourLine` is `reasonState === 'proposed'`. `saveNote` sends
    `{ text, reasonLine, revision }` and `confirmReason(noteId, revision, o)` confirms the line (key `y`). Note checks
    are #15's: `note_too_long`, a banned phrase and a dash are `block` checks.
11. **Types ownership.** Task 2 owns the view-only types in `types.ts`; #11 owns the types the API itself needs and adds
    them additively; its generated `schema.ts` resolves any overlap, and #11's Task 24 runs after this plan merges.
12. **Ranking hides and reader flags are shown** (design 16.9). The No-go view lists `hiddenCompanies` (kind, reason, and
    the words "hidden, pending reader" when provisional) and the company page shows each posting's `flags` (field, both
    values, both quotes). The types are #11's (its Tasks 7 and 21); Task 16 adds them to `types.ts` additively and to
    the fixtures if #11 has not landed.

13. **Mockups approved by Devanshu (2026-10-21), with these answers.** The day rail plus work panel replaces the
    table of 7 rows. Rates show no percent until a stage has at least 20 in its denominator (`formatRate` follows the
    findings rule). Bars are drawn against the larger of the period's top count and 30. The 7-day question is shown
    open, not collapsed. Confirm line is the filled button until the reason line is his, then Log request sent.
14. **A referral is the win.** His words: once a referrer has put his application in, that is the most he can do.
    Recognition has three sizes: an accept is a quiet note in the rail; a human reply gets the banner; a referral gets
    the largest moment the design allows (the banner in its strongest form, kept until seen, and the top of the win
    log), and so do interviews and offers. The win log's first filter is Referrals.
15. **An Insights view** (new task after Task 15, reads from #11): how many people and how many companies he has
    reached, by channel and by month; and "What your interviews have in common": the companies that led to an
    interview or a referral side by side with the rest, on the ranking signals (stage, size, junior H-1B filings,
    live role at his level named, who he wrote to, channel, days from posting to message), with counts and the
    small-sample rule. It starts from his real history (Distyl, Runlayer, Crusoe, Retell and the other interview
    companies in DevVault interview-prep), imported by #17, so the pattern is there on day one.
16. **QA:** the plan's e2e specs stay; in addition a QA pass (exploratory, against the running app on fixtures,
    every view and every state in the mockups) runs before the PR, and the app gets a "Report a problem" key that
    saves what he saw (view, selection, note) to a local file the side session can read, so his own use feeds fixes.

## Global Constraints

- Everything lives in `web/`, plus `docs/design/` (Task 1) and `docs/README.md` (Task 19). Never touch `dashboard/`, `mail/`, `refresh/`, `outreach/`, the live checkout or DevVault. Never call the mail API on :3002.
- Node: run every command with `export PATH="$HOME/.nvm/versions/node/v20.19.5/bin:$PATH"` first. Commands assume `cd web`.
- Ports: e2e runs with `WEB_PORT=$CONDUCTOR_PORT pnpm e2e` (CLAUDE.md). Never 3001, 3002, 3011, 3013, and never 5173 from a workspace. The dev server serves on `WEB_PORT`. The app makes no network call to any other host; external links open in a new tab and are never fetched.
- TypeScript: `strict`, `noUncheckedIndexedAccess`, `verbatimModuleSyntax`, `noUnusedLocals`, `noUnusedParameters`, alias `@/*`. Views import data types and the client only from `@/api`. No new packages.
- Time: the app's zone is `America/New_York`; Vitest runs with `TZ=America/New_York`; the fixture clock is `2026-11-10T15:14:00Z` (Tuesday, 10:14 in Boston). Views never read the real clock for what they show: every new view response carries `asOf`, and weeks, ages and "carried over from" come from `asOf` and calendar dates (`IsoDate` is never passed to `new Date()`).
- API shape: camelCase wire names, response models named exactly as the interfaces below, no `Record<...>` or dict fields (so #11's generated types are drop-in), `// derived` marks what the API computes.
- Writes: every write takes `WriteOptions` (`requestId`) and logging writes also take a stable `idempotencyKey` (format below). A stale `revision` rejects with `ApiError('conflict')`. Reversible writes go through `usePendingWrite` (8 s window, toast with Undo and `z`). The said-no and asked-to-stop reply kinds commit at once (phase 1 lead decision 2).
- Section 16 rules, verbatim from the spec, are acceptance criteria for every view (matrix below): always one next action; a day has a finish line; real progress shown plainly; wins loud and kept; effort counts on quiet days; the system visibly learns; fast and exact (one key, instant feedback, undo, no spinner on the main path); calm, never guilt (no streaks, no red alarm for a missed day, unfinished rows roll forward quietly); honest about why (every rank, flag and hide shows reason and source).
- Copy: plain words, short sentences, sentence case, no em dash or en dash in any UI string, test, comment, doc or commit message. Never the words streak, missed, overdue, behind or "keep it up" in UI copy. No button says Send. One verb per action across button, key hint and toast. Errors say what happened, what did not happen, and what to do; they do not apologize.
- Nothing on LinkedIn is automated (spec non-goals): the UI opens links in a new tab, copies text and logs what he did. It never reads LinkedIn.
- Look: the approved tokens in `web/src/styles/globals.css` only (zinc base, the five status hues, Inter, 4 px grid, radius 8 and 6). No new hue. Color is never the only signal.
- Fixtures: real public company names, invented people, `.example` email domains, invented URLs on `.example` hosts, except LinkedIn search links, which use `https://www.linkedin.com/search/results/people/?...`. A badge reads "Sample data. Nothing is saved." (already built) whenever `client.source === 'sample'`.
- Commits: every commit step names its paths in `git add`, never `git add -A` or `git add web`. Messages end with `(#33)` and the Co-Authored-By trailer the executing session is told to use. Never push.

## Review Focus

Inputs and conditions the spec implies, that no single task's happy path would catch, most likely first. Each has a named test in the owning task.

1. **A double press logs once.** Holding `r`, double clicking Log request, pressing `r` again after the commit landed, or logging from two tabs gives one touch, one toast, one count. The key is per entry and step (`plan:<entryId>:request`), not per click. Tests: Task 3 `same key twice makes one touch`, Task 5 `two presses inside the window queue one write`, `a press after the commit reaches the client with the same key`, Task 18 `double press logs one request`.
2. **Honest numbers with little data.** One request, zero requests, 1 of 2, a base rate with zero replies, a month with no outcomes: never `NaN%`, never a percentage under the minimum sample, never "1 in 0", never a rate with its count hidden. Tests: Task 4 `formatRate`, `oneIn`, `baseRateLine` edge cases; Task 13 `first week shows counts and no percent`; Task 14 `no replies ever shows no base rate`.
3. **A win is never lost or faked.** A win that arrives while he works stays at the top of Today across polls and reloads until he marks it seen; opening the Wins page does not erase the "New" marks mid-visit; a said-no reply is not a win; undoing an accept inside the window removes the win with it. Tests: Task 12 `an unseen win survives a poll and a reload`, `said no is not a win`, Task 3 `quiet clears the prompt without a win`.
4. **The last row finishes the day without losing focus or moving rows.** Logging the last step shows the finish line at once (no spinner, no wait for the poll), focus lands on its heading, a skipped row reads "Carried over" and never red, and the rail never reorders under the cursor. Tests: Task 5 `done count moves at once`, Task 8 `row stays in place after it is logged`, Task 9 `focus moves to the finish heading`.
5. **A note shown with a fact must show its link, and the link must be safe.** A fact with a missing or non-http link is flagged "No link" and the Copy action warns before copying; a note over its limit cannot be mistaken for ready; Copy copies exactly the saved text, and unsaved edits are kept when he moves to another row. Tests: Task 7 `NoteBlock flags a fact without a safe link`, Task 8 `copy puts the saved note on the clipboard`, `edits survive moving rows`.

---

## Design decisions (frontend-design applied)

**Subject, audience, job.** One person, a new grad on OPT in a long, mostly silent search, opening the app each weekday morning. The job: make today's effort clear, quick and visibly worth it, so the app keeps him going the way good work keeps a person going. Not a dashboard of vanity numbers: a day he can finish.

**Direction.** Extend the approved system (zinc, Inter, five status hues, 8 and 6 px radii). Spend the boldness in one place: **the day rail**, the left column of Today. It is a vertical line of nodes, one per person, ending in a finish node. Numbering and a connecting line are honest here because the content really is a sequence (do these, in this order, then you are done). Everything else stays quiet.

Tokens (all existing; nothing new is added):

| Name | Value light / dark | Use |
|---|---|---|
| ink | `#09090b` / `#fafafa` | person name, reason, primary action |
| rail | `#e4e4e7` / `#27272a` (border) | the rail line, ledger rules |
| quiet | `#6b6b75` / `#a1a1aa` (muted-foreground) | meta, carried-over text, small-sample notes |
| paper | `#fafafa` / `#111113` (subtle) | the note block, the finish panel |
| win | `#15803d` on `#f0fdf4` / `#4ade80` on green at 12% (the `ok` pair) | the only color on the app's best moments: accept, reply, referral, interview |
| working | `#1d4ed8` on `#eff6ff` (the `info` pair) | a row whose step is logged but not complete |

Type: Inter throughout, tabular figures for every count. Roles: person name 24/32 600; the one reason 16/24 400 (the sentence he acts on); note text 14/22 in the paper block, at most 62 characters wide; tables and ledgers 13/18; meta 12/16. Sentence case, no all caps, no eyebrow labels. Line length under 80 characters.

Layout concept (left aligned everywhere; widths for a 1440 by 900 laptop):

```
Today, Tuesday, November 10            7 people, 2 done          Requests this month 52 of 150
+-- rail (320) --------------+  +-- work panel (flex, text max 62ch) ---------------------+
| (check) 1 Dana Whitfield   |  | Lena Ortiz                       Engineer at Pylon      |
| |       Resend             |  | Her team shipped usage billing in October (blog post).   |
| (check) 2 Tomas Reyes      |  | Role: Software Engineer I        [Open role O] [Apply A] |
| |       Baseten            |  | +-- Note to send  168 of 200 ---------------------+   |
| (ring)  3 Lena Ortiz   <-- |  | | Hi Lena, I built ...                              |   |
| |       Pylon              |  | | Facts used: Pylon blog, Oct 14 (link)             |   |
| (empty) 4 Jonah Park       |  | +---------------------------- [Copy C] [Edit E] ---+   |
| (empty) 5 Find someone     |  | [Open profile L]  [ Log request sent  R ]  Skip X       |
| (empty) 6 Ines Duarte      |  +---------------------------------------------------------+
| (empty) 7 Mintlify (email) |
| |                          |  below, quiet: Last week, what happened? (2)   Watch list (3)
| (flag)  Done for today     |
|         Tomorrow: 7 people |
+----------------------------+
```

Other views, one line each: **Finish** replaces the work panel when no row is left (heading, what he did, tomorrow's preview, the week so far). **Progress** is a ledger table (stage, count, rate with its count, a thin monochrome bar scaled to the period's largest count), not a funnel graphic and not stat tiles; the bar widths use real counts so a small sample cannot look big. **Quiet week** puts "Your work this week" first and the base rate second; zeros are plain grey, never red. **Wins** is a dated list, newest first, with what he sent beside each, so a hard day shows proof. **Score** is a ledger of signals with a signed bar, points, share and the reason and source in words.

**Memorable moment, and only one animated one:** a win. The banner enters with a 240 ms fade, a check mark draws once, nothing else moves, and it stays until he marks it seen. No confetti, no counters flying, no streak flame. Reduced motion turns it off (the global rule in `globals.css` already does).

**Reviewed against the generic defaults, and changed:**
1. First instinct was four big stat tiles for progress (a hero number with a gradient). Replaced by the ledger: counts always sit beside their denominators, which is the honest-numbers rule and also avoids the template look.
2. First instinct for wins was celebration (confetti, a streak badge). Replaced by a persisted, quiet banner and a kept log. Streaks are forbidden by section 16 rule 8.
3. Numbered markers appear only on the rail, where the content is a sequence. No eyebrow labels, no middle-dot meta strings, no tracked all-caps.
4. One radius per role as in the system (8 on blocks, 6 on controls), the paper block is the only tinted surface on Today, and no shadows.
5. The finish node's "Tomorrow: 7 people" line is the only forward-looking copy; nothing nags.

**Mockups first (Task 1).** These are visual decisions Devanshu should see before React work on them: the rail plus work panel layout (instead of a table), the finish line, the win banner moment, the Progress ledger and quiet-week arrangement, the Wins log, and the Score tab. Logic tasks (2 to 6) do not depend on them and can start now.

## Section 16 acceptance matrix

Each row is a design-review line for the PR body. "Proof" names where it is tested.

| Rule | Built as | Proof |
|---|---|---|
| 1 One next action | Today lands with the first unfinished person selected, panel open on their next step; the primary button is the one next step (`nextStep`). Never a blank page: empty and not-ready states say what to do | T5 `nextStep`, T8 `first todo is selected on load`, e2e plan |
| 2 A finish line | The rail ends in a finish node; the last logged step shows the Finish panel: what he did, tomorrow's preview, the week so far | T9, e2e plan |
| 3 Real progress | Progress ledger: counts with denominators and rates, minimum sample enforced by the API's `enough` flag, effort counts, no vanity counts | T4, T13 |
| 4 Wins loud and kept | Win banner on top of Today until marked seen; Wins log keeps every accept, reply, referral, interview and offer | T12, e2e logging |
| 5 Effort on quiet days | Quiet-week arrangement: effort first, his own base rate second, only with enough data | T14 |
| 6 The system learns | Monthly findings with counts, a caution when the sample is small, and "what changed" | T15 |
| 7 Fast and exact | One key per step, optimistic overlay, toast with Undo and `z`, no spinner after first load, stable idempotency key | T3, T5, T8, e2e logging |
| 8 Calm | No streak, no red for unfinished rows, "Carried over from Monday" in grey, the prompt is one question at a time with "Nothing yet" as a normal answer | T8, T10, T17 copy audit |
| 9 Honest about why | One reason with its source links on each row, note facts with links before sending, score breakdown per signal with reason and source, watch list items say why they are there | T7, T8, T11, T16 |

## How the work is run

- **One integration branch** `DevanshuNEU/web-experience`. Task 1 (docs only) can run in parallel with Tasks 2 to 5 (one agent, in order). Tasks 6 and 7 follow (7 waits for Devanshu's approval of the mockups). Tasks 8 to 16 are independent after 7 and may each get a worktree and agent (`git worktree add ../web-x<NN> -b web-x<NN>-<slug> DevanshuNEU/web-experience`, `pnpm install --frozen-lockfile`); they run unit tests, `pnpm lint` and `pnpm typecheck` only. Tasks 17 to 19 are the integrator's.
- **Integrator, after each merge:** `pnpm install --frozen-lockfile && pnpm lint && pnpm typecheck && pnpm test`, and `WEB_PORT=$CONDUCTOR_PORT pnpm e2e` after Tasks 8, 12, 13 and 18. A red run goes back to the task's agent.
- **Shared files** edited by more than one task are named in File layout; only the owner task edits them (Task 2 owns the view-only types in `types.ts`, `client.ts`, `keys.ts` and `fixture/data.ts`; #11's API-driven types are additive edits to `types.ts` (ruling 11); Task 6 owns `routes.tsx`, `pages.tsx`, the sidebar and the shell).

## File layout

```
web/src/
  api/types.ts                 T2: new view types (below)
  api/client.ts                T2 reads, T3 writes
  api/keys.ts                  T2: plan, prompt, wins, progress, findings, watch, score keys
  api/fixture/plan-data.ts     T2: plan, prompt, wins, progress, findings, watch, scores, per scenario
  api/fixture/data.ts          T2: Scenario union, FixtureDb fields, computeShell additions
  api/fixture/client.ts        T2 reads, T3 writes
  lib/progress.ts              T4: formatRate, oneIn, baseRateLine, smallSampleNote, didToday
  lib/status.ts                T4: channel, lane, win, watch words; T2: new ReviewKind words
  copy-rules.test.ts           T4: no dashes, no guilt words in UI source; T17 extends
  keyboard/list-nav.ts         T5: `initial` option
  components/reply-kind-dialog.tsx   T10: moved out of routes/linkedin/index.tsx
  components/day-rail.tsx, note-block.tsx, step-marks.tsx, source-links.tsx, win-mark.tsx    T7
  routes/today/index.tsx       T8: rewritten as the plan
  routes/today/plan-steps.ts, use-plan-actions.ts          T5
  routes/today/plan-panel.tsx, find-someone-panel.tsx, email-row-panel.tsx, plan-states.tsx   T8
  routes/today/finish-panel.tsx             T9
  routes/today/outcome-prompt.tsx           T10
  routes/today/watch-list.tsx               T11
  routes/today/win-strip.tsx                T12
  routes/proposals/*           T8: the phase-1 Today (picks, backlog, remove) moved here unchanged
  routes/wins/index.tsx, wins.test.tsx      T12
  routes/progress/index.tsx, funnel-ledger.tsx, quiet-week.tsx, findings.tsx, progress.test.tsx   T13 to T15
  routes/company/tabs/score.tsx             T16
web/e2e/ plan.spec.ts, logging.spec.ts, helpers.ts (T18); proposals.spec.ts replaces today.spec.ts (T8)
docs/design/ today-plan.html, today-finish.html, progress.html, progress-quiet.html, wins.html, company-score.html (T1)
```

## The seam: new types in `web/src/api/types.ts` (Task 2)

Exact copy for Task 2, appended under a banner `// Plan, outcomes, progress, wins (#33)`. They mirror foundation tables `outreach_touch` (channels, outcome dates), `application`, `company_score` (`signals`, `lanes`) and `daily_list_entry` (`person_id`, `channel`, `list_group`).

```ts
export type Channel = 'linkedin_request' | 'linkedin_message' | 'email' | 'application' | 'hn_reply' | 'other';
export type ListGroup = 'level_role' | 'intern_junior' | 'reason_no_role' | 'any_us_role'; // widens DailyEntry.listGroup
export type StepKind = 'request' | 'apply';
export type OutcomeKind = 'accepted' | 'replied' | 'referred' | 'interview' | 'offer' | 'quiet';
export type WinKind = 'accepted' | 'replied' | 'referred' | 'interview' | 'offer';
export type WatchKind = 'careers_changed' | 'new_reason_no_role' | 'merge_candidate';
export type FunnelKey = 'requests' | 'accepts' | 'replies' | 'referrals' | 'interviews';
// ReviewKind (existing) gains 'merge_candidate' | 'careers_changed' | 'size_check' | 'reader_disagrees' (foundation values).

export interface SourceLink { label: string; url: string }
export interface NoteFact { text: string; source: SourceLink; inNote: boolean }     // a public-page fact and its link
export interface NoteDraft {
  noteId: Id; text: string; chars: number; maxChars: number;                         // maxChars from config, never hard-coded in the UI
  reasonLine: string; reasonState: 'proposed' | 'confirmed' | 'written';             // the one line that says why this person; Claude only proposes it
  needsYourLine: boolean;                                                            // derived: reasonState === 'proposed'
  facts: NoteFact[]; checks: Check[]; savedBy: SavedBy; savedAt: IsoTime; revision: Revision;
}
export interface PlanStep { kind: StepKind; touchId: Id | null; at: IsoTime | null }  // at null: not done yet
export interface PlanRow {
  entryId: Id; position: number; channel: 'linkedin_request' | 'email'; lane: ListGroup;
  company: CompanyRef & { domain: string | null; hq: string; revision: Revision };
  person: { person: PersonRef; foundOn: SourceLink | null } | null;                  // null: find someone
  role: RoleToName | null; applyUrl: string | null;                                  // null with no role
  reason: string; reasonSources: SourceLink[];                                       // derived: one reason and where it comes from
  note: NoteDraft | null;
  findSomeone: { searchUrl: string; handoff: string } | null;                        // set only when person is null
  emailTouchId: Id | null;                                                           // channel 'email': the Outbox item
  steps: PlanStep[];                                                                 // linkedin_request: request, then apply when there is a role
  state: 'todo' | 'done' | 'skipped';                                                // derived
  carriedFrom: IsoDate | null; revision: Revision;
}
export interface PlanView {
  asOf: IsoTime; date: IsoDate; builtAt: IsoTime | null; listJob: JobRunSummary | null;
  notReady: { reason: 'weekend' | 'not_built' | 'job_failed'; text: string } | null;
  rows: PlanRow[]; done: number; total: number;
  linkedinMonth: { used: number; limit: number; hardUsed: number; hardLimit: number };   // plan quota, and every request against the hard total
  tomorrow: { total: number; text: string; preview: { companyName: string; personName: string | null; roleTitle: string | null }[] };
  week: { requests: number; accepts: number; replies: number };
}
export interface PromptTouch { touchId: Id; person: PersonRef; company: CompanyRef; channel: Channel; sentAt: IsoTime; ageDays: number; context: string; revision: Revision }
export interface OutcomePromptView { asOf: IsoTime; touches: PromptTouch[] }
export interface Win {
  winId: Id; kind: WinKind; at: IsoTime; touchId: Id; person: PersonRef | null; company: CompanyRef;
  line: string; sentNote: string | null; role: string | null; replyKind: ReplyKind | null; unseen: boolean;   // line is derived
}
export interface WinsView { asOf: IsoTime; wins: Win[]; unseen: number; counts: { accepted: number; replied: number; referred: number; interview: number; offer: number }; since: IsoDate }
export interface FunnelStage { stage: FunnelKey; label: string; count: number; from: { stage: FunnelKey; count: number } | null }  // rate = count / from.count
export interface ProgressPeriod { label: string; from: IsoDate; to: IsoDate; stages: FunnelStage[]; effort: { label: string; count: number }[]; enough: boolean; quiet: boolean }
export interface BaseRates { since: IsoDate; requests: number; accepts: number; replies: number; medianDaysToReply: number | null; enough: boolean; minRequests: number }
export interface TodayProgress { planned: number; done: number; requests: number; applications: number; accepted: number; replied: number }
export interface ProgressView { asOf: IsoTime; today: TodayProgress; week: ProgressPeriod; month: ProgressPeriod; base: BaseRates }
export interface Finding { id: Id; text: string; counts: { label: string; count: number; of: number }[]; caution: string | null; changed: string | null }
export interface FindingsView { asOf: IsoTime; month: IsoDate; reportedOn: IsoDate | null; findings: Finding[]; changes: { at: IsoTime; text: string }[] }
export interface WatchItem { id: Id; kind: WatchKind; company: CompanyRef; text: string; at: IsoTime; link: SourceLink | null; reviewId: Id | null }
export interface WatchView { asOf: IsoTime; items: WatchItem[] }
export interface ScoreSignal {
  name: string; label: string; value: number; weight: number; points: number; share: number;   // share = points / sum of |points|, signed
  neutral: boolean; reason: string; source: SourceLink | null;
}
export interface ScoreView {
  asOf: IsoTime; companyId: Id; rankerVersion: string; score: number; rank: number | null; ofCompanies: number;
  lanes: { lane: ListGroup; strongest: boolean }[]; signals: ScoreSignal[]; scoredAt: IsoTime;
  previous: { rankerVersion: string; score: number } | null;
}
```

`ShellSummary` gains `plan: { done: number; total: number }` and `winsUnseen: number`.

Client additions. Reads (Task 2):

```ts
plan(): Promise<PlanView>; outcomePrompt(): Promise<OutcomePromptView>; wins(): Promise<WinsView>; progress(): Promise<ProgressView>;
findings(): Promise<FindingsView>; watchList(): Promise<WatchView>; score(companyId: Id): Promise<ScoreView>; // 'not_found' for an unknown company
```

Writes (Task 3). Idempotency keys are `plan:<entryId>:request` and `plan:<entryId>:apply`; the same key twice returns the first result and writes nothing:

```ts
logRequest(entryId: Id, input: { personId: Id; idempotencyKey: string }, revision: Revision, o: WriteOptions): Promise<PlanRow>;
logApplied(entryId: Id, input: { postingId: Id; idempotencyKey: string }, revision: Revision, o: WriteOptions): Promise<PlanRow>; // 'not_allowed' with no role
logOutcome(touchId: Id, kind: OutcomeKind, revision: Revision, o: WriteOptions, replyKind?: Exclude<ReplyKind, 'unsorted' | 'auto_reply'>): Promise<{ revision: Revision }>;
skipRow(entryId: Id, revision: Revision, o: WriteOptions): Promise<PlanRow>;       // carried to tomorrow; 'not_allowed' on a done row
saveNote(noteId: Id, edit: { text: string; reasonLine: string; revision: Revision }, o: WriteOptions): Promise<NoteDraft>;
confirmReason(noteId: Id, revision: Revision, o: WriteOptions): Promise<NoteDraft>;   // reasonState becomes 'confirmed'; 'not_allowed' with an empty line
markWinsSeen(winIds: Id[], o: WriteOptions): Promise<void>;                          // 'not_found' for an unknown id
dismissWatch(itemId: Id, o: WriteOptions): Promise<void>;
```

"Not a fit" reuses `removeCompany(row.company.id, reason, note, row.company.revision, o)`.

---

## Tasks

### Order and groups

| Group | Tasks | Needs |
|---|---|---|
| A | 1 (mockups, docs only) in parallel with 2, 3, 4, 5 (one agent, in order) | nothing |
| B | 6, then 7 | A; 7 also needs Devanshu's approval of Task 1 |
| C | 8, then 9 to 16 (one agent each) | 7 |
| D | 17, 18, 19 | all of C |

### Task 1: Mockups for the visual decisions (gate)

**Files:**
- Create: `docs/design/today-plan.html`, `docs/design/today-finish.html`, `docs/design/progress.html`, `docs/design/progress-quiet.html`, `docs/design/wins.html`, `docs/design/company-score.html`
- Modify: `docs/design/index.html` (links), `docs/design/mockup.css` (only the rail, note block, ledger bar and win banner classes; no new tokens), `docs/design/README.md` (new sections: Today as a plan replaces 5.1, Finish, Progress, Wins, Score tab, the keyboard map below, copy rules for them)

**Interfaces:** none (static HTML on the existing `mockup.css` and `mockup.js`, fake data from the fixture numbers in Task 2).

- [ ] **Step 1: Build the six pages** with the frontend-design skill and the layout concept above: `today-plan.html` (rail with 7 nodes, work panel on row 3, a find-someone row and an email row visible by clicking them via `mockup.js` tabs, win banner at the top in a second state), `today-finish.html`, `progress.html` (week with enough data, a small-sample state toggled by a button), `progress-quiet.html`, `wins.html`, `company-score.html`. Light and dark both work through the existing toggle. Use the dataviz skill for the ledger bar (one monochrome series, direct labels, text alternative).
- [ ] **Step 2: Write the key map into `docs/design/README.md`** (Today: `j k` rows, `enter` company, `l` profile, `o` role, `a` apply page, `s` LinkedIn search, `c` copy note or handoff, `e` edit note, `r` log request sent, `p` log applied, `x` carry to tomorrow, `n` not a fit, `1 2 3` answer the prompt, `w` mark wins seen, `shift+c` copy every find-someone handoff; Progress `1 2` week or month; Wins `1 2` all or referrals and interviews; Company `7` Score; chords `g p` Progress, `g w` Wins).
- [ ] **Step 3: Check contrast** of every text and background pair on the six pages against the token table in `docs/design/README.md` section 10 (body 4.5:1, status pairs as listed). Expected: no pair under 4.5:1; list the check in the commit body.
- [ ] **Step 4: Run** `grep -cP '\xe2\x80[\x93\x94]' docs/design/*.html docs/design/README.md docs/design/mockup.css` (a byte match for the two dash characters). Expected: `0` for each file.
- [ ] **Step 5: Show Devanshu** (serve the folder with `python3 -m http.server $CONDUCTOR_PORT` from `docs/design/`, never 3001, 3002, 3011, 3013). Do not start Tasks 7 to 16 until he approves or sends changes; record his answer in this plan's open questions.
- [ ] **Step 6: Commit** `git add docs/design && git commit -m "Design: mockups for Today as a plan, finish, progress, wins and score (#33)"`

### Task 2: Types, reads and fixture data

**Files:**
- Modify: `web/src/api/types.ts`, `client.ts`, `keys.ts`, `fixture/data.ts`, `fixture/client.ts`, `web/src/lib/status.ts` (new `ReviewKind` words), `web/src/routes/today/proposals-table.tsx` (group titles for the two new `ListGroup` values)
- Create: `web/src/api/fixture/plan-data.ts`
- Test: `web/src/api/fixture/client.read.test.ts` (extend), `web/src/api/fixture/plan-data.test.ts` (new)

**Interfaces:**
- Produces: the types and read methods above; `queryKeys.plan`, `.outcomePrompt`, `.wins`, `.progress`, `.findings`, `.watch`, `.score(id)`; `pollMs` unchanged (60 s for all); `Scenario` gains `quiet_week`, `first_week`, `plan_done`, `win`, `fresh` (no outcomes ever); `buildPlanFixture(scenario, ctx): PlanFixture` (the new `FixtureDb` fields `plan`, `prompt`, `wins`, `progress`, `findings`, `watch`, `scores: Record<Id, ScoreView>`; the one `Record` is fixture-internal, not the wire).
- Fixture facts the tests and e2e rely on (default, 2026-11-10): 7 rows in this order, `done` 2, `total` 7. 1 Dana Whitfield, Resend, "Software Engineer, New Grad", `level_role`, request and apply done. 2 Tomas Reyes, Baseten, "Forward Deployed Engineer, New Grad", both done. 3 Lena Ortiz, Pylon, "Software Engineer I", `level_role`, todo, note 168 of 200 with two facts (each with an `.example` link), `reasonState` `confirmed`. 4 Jonah Park, Inngest, "Software Engineer II", todo, `needsYourLine` true (`reasonState` `proposed` with a proposed `reasonLine`). 5 Cerebrium, `person` null, role "Member of Technical Staff", `findSomeone.searchUrl` a LinkedIn people-search link. 6 Ines Duarte (founder), Turso, `intern_junior`, role "Software Engineering Intern", todo. 7 Mintlify, `channel` email, `reason_no_role`, `emailTouchId` set, no role. `linkedinMonth` `{ used: 52, limit: 150, hardUsed: 60, hardLimit: 200 }`. `week` requests 31, accepts 9, replies 2. Progress week: requests 31, accepts 9 (from requests 31), replies 2 (from requests 31), referrals 0 (from replies 2), interviews 1 (from replies 2), `enough` true, `minRequests` 30; month requests 52, accepts 17, replies 4, referrals 1, interviews 2. `base`: requests 142, accepts 41, replies 9, `medianDaysToReply` 6, `enough` true. Prompt: 2 touches older than 7 days. Wins default: 5 kept wins (one interview at Distyl AI, one referral, three replies), `unseen` 0; scenario `win` has one unseen accept from Dana Whitfield. Findings for October: 2 findings (engineers on the team accepted 14 of 33 against founders 5 of 24, `changed` set; roles named in the note 5 of 71 against 0 of 29, `caution` set). Watch: 3 items (one of each `WatchKind`). Scores for every fixture company, Pylon with 8 signals including one neutral (size unknown) and one negative.

- [ ] **Step 1: Write the failing tests** (`plan-data.test.ts` unless noted):
  - `default plan has 7 rows in the listed order, done 2, total 7`; positions are 1 to 7.
  - `row 5 has no person and a LinkedIn people-search link`: `person === null`, `findSomeone.searchUrl` starts `https://www.linkedin.com/search/results/people/`; every other row has `findSomeone === null`.
  - `row 7 is an email row`: `channel === 'email'`, `emailTouchId !== null`, `role === null`, `applyUrl === null`.
  - `every note is within its limit and every fact link is safe`: `note.chars === note.text.length`, `chars <= maxChars`, `safeHref(fact.source.url) !== undefined`.
  - `every row has a reason and at least one source link`.
  - `no em or en dash in any string of any scenario` (deep walk of every read of every scenario).
  - `shell carries plan and wins`: default `shell.plan` is `{ done: 2, total: 7 }`, `winsUnseen` 0; scenario `win` gives 1.
  - `progress stages are in funnel order with their denominators` (default numbers above); `first_week` has `week.enough === false` and `week.stages[0].count === 9`; `quiet_week` has `week.quiet === true`, `week.stages` replies 0, `month.quiet === false`; `fresh` has all counts 0, `base.enough === false`.
  - `plan_done`: every row `state === 'done'`, `done === total`, row 5 has a person (Wen Zhao) with both steps logged, so the counts are 6 requests, 6 applications and 1 email reviewed. `weekend` and `job_failed`: `rows` is empty, `notReady` is set with its reason and `tomorrow.total > 0`.
  - `score points add up`: for Pylon `sum(points)` equals `score` within 0.001 and `sum(abs(share))` equals 1 within 0.001; every signal has a non-empty reason; the size signal has `neutral === true` and `value === 0`.
  - `client.read.test.ts`: `error` scenario makes each new read reject with `ApiError` code `network`; `score(999999)` rejects `not_found`; reads return copies (mutating a result does not change the next read).
- [ ] **Step 2: Run** `pnpm test src/api/fixture` . Expected: FAIL (modules and methods missing).
- [ ] **Step 3: Implement** the types, the read methods on `OutreachClient` and `FixtureClient` (`read()` as the other reads), the keys, `plan-data.ts` and the `buildFixture` wiring. `computeShell` takes `plan` and `wins` into account. Add the four foundation `ReviewKind` values with words (`merge_candidate` "Merge?", `careers_changed` "Careers page changed", `size_check` "Check size", `reader_disagrees` "Check posting"; all tone `warn`) so the existing Needs you view cannot crash on them. Add group titles for `intern_junior` ("Intern or junior work you can do on OPT") and `reason_no_role` ("No role yet, a reason to reach out") in `proposals-table.tsx`.
- [ ] **Step 4: Run** `pnpm test src/api && pnpm typecheck && pnpm lint`. Expected: PASS.
- [ ] **Step 5: Commit** `git add web/src/api web/src/lib/status.ts web/src/routes/today/proposals-table.tsx && git commit -m "web: plan, outcome, progress, win, finding, watch and score types with fixture reads (#33)"`

### Task 3: Fixture writes and idempotency

**Files:**
- Modify: `web/src/api/client.ts`, `web/src/api/fixture/client.ts`, `web/src/api/fixture/plan-data.ts` (a note rescan rule set)
- Test: `web/src/api/fixture/client.write.test.ts` (extend)

**Interfaces:**
- Consumes: Task 2.
- Produces: the write methods above. Fixture behavior: `logRequest` and `logApplied` set the step's `at` to `asOf` and `touchId` to a new id, recompute `state` (`done` when every step has `at`) and `plan.done`, `progress.today` and `week`, `month` counts and `plan.week`, and the shell; a repeated `idempotencyKey` (any `requestId`) returns the stored row and changes nothing. `logOutcome`: `accepted`, `replied` (any kind except `said_no`, `asked_to_stop`), `referred`, `interview`, `offer` add an unseen `Win` and move the touch out of the prompt; `quiet` only moves it out of the prompt; `said_no` and `asked_to_stop` log the reply without a win; a reply on a quiet touch is accepted.

- [ ] **Step 1: Write the failing tests:**
  - `logRequest marks the request step at asOf and moves progress`: after logging row 3, `plan().rows[2].steps[0].at === asOf`, `progress().today.requests` 3, `shell().plan.done` still 2 (the row still has its apply step).
  - `the apply step completes the row`: after `logApplied` on row 3 too, row 3 `state === 'done'`, `plan.done` 3, `progress().today.applications` 3.
  - `same key twice makes one touch` (Review Focus 1): two `logRequest` calls with the same `idempotencyKey` and different `requestId` return the same `touchId`; `progress().today.requests` rises by one.
  - `same requestId twice does the work once` (existing contract).
  - `logApplied with no role is refused`: row 7 (no role) rejects `not_allowed`; a stale revision rejects `conflict`.
  - `logOutcome accepted makes an unseen win and clears the prompt`: `wins().unseen` +1, `outcomePrompt().touches` shorter by one.
  - `quiet clears the prompt without a win` (Review Focus 3).
  - `said no is not a win`: `logOutcome(id, 'replied', rev, o, 'said_no')` clears the prompt, adds no win, but `progress().week` replies +1.
  - `skipRow carries the row`: `state === 'skipped'`, `tomorrow.total` +1; on a done row it rejects `not_allowed`.
  - `saveNote rescans`: a body over `maxChars` adds a `block` check `note_too_long` ("Over the limit by N characters"); `I'd love to` adds a `block` `banned_phrase`; an em dash adds a `block` `dash` (the codes and severities are #15's `check_note`); the returned `chars` equals the text length; a changed `reasonLine` makes `reasonState` `written` and an unchanged one keeps it; a stale revision rejects `conflict`.
  - `confirmReason sets confirmed and clears needsYourLine`; with an empty reason line it rejects `not_allowed`; a stale revision rejects `conflict`.
  - `markWinsSeen zeroes unseen; an unknown id rejects not_found`; `dismissWatch removes the item`.
- [ ] **Step 2: Run** `pnpm test src/api/fixture/client.write.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement** the methods on `FixtureClient` through the existing `write()` helper. Keep a private `Map<string, Id>` of idempotency keys. Recompute derived counts in one place (`settle()` already does this for the shell).
- [ ] **Step 4: Run** `pnpm test src/api && pnpm typecheck && pnpm lint`. Expected: PASS.
- [ ] **Step 5: Commit** `git add web/src/api && git commit -m "web: fixture writes for logging, outcomes, notes, wins and the watch list (#33)"`

### Task 4: Honest-number helpers, status words, copy audit

**Files:**
- Create: `web/src/lib/progress.ts`, `web/src/lib/progress.test.ts`, `web/src/copy-rules.test.ts`
- Modify: `web/src/lib/status.ts`, `web/src/lib/status.test.ts`

**Interfaces:**
- Produces (all pure, no clock):
  - `formatRate(count: number, of: number, enough: boolean): string`: `"9 of 31 (29%)"` when `enough`; `"2 of 9"` when not (no percent, ever); `"None yet"` when `of === 0`. Percent is `Math.round(100 * count / of)`.
  - `oneIn(of: number, count: number): number | null`: `Math.round(of / count)`, `null` when `count === 0`.
  - `baseRateLine(b: BaseRates): string | null`: `"About 1 in 16 requests gets a reply."` when `enough` and `replies > 0` and N is at least 2; `"Most requests get a reply."` when N is 1; otherwise `null`.
  - `smallSampleNote(p: Pick<ProgressPeriod, 'enough'>, requests: number, minRequests: number): string | null`: `"Only 9 requests so far. Rates this small swing a lot, so none are shown."` when not enough; `null` when enough.
  - `didToday(rows: PlanRow[]): { label: string; count: number }[]`: `"Requests sent"`, `"Applications sent"`, `"Emails reviewed"` (email rows done), in that order, zero counts omitted.
  - In `status.ts`: `channelLabel` (`linkedin_request` "LinkedIn request", `linkedin_message` "LinkedIn message", `email` "Email", `application` "Application", `hn_reply` "HN reply", `other` "Other"), `laneLabel` (`level_role` "Role at your level", `intern_junior` "Intern or junior work", `reason_no_role` "Reason to reach out", `any_us_role` "Open US role"), `winKindLabel` (accepted "Accepted", replied "Replied", referred "Referral", interview "Interview", offer "Offer"), `watchKindLabel` (careers_changed "Careers page changed", new_reason_no_role "New company to reach", merge_candidate "Possible duplicate"), each `satisfies Record<Union, string>` so a new API value fails typecheck.

- [ ] **Step 1: Write the failing tests** (Review Focus 2):
  - `formatRate`: `(9, 31, true)` is `"9 of 31 (29%)"`; `(2, 31, true)` is `"2 of 31 (6%)"`; `(1, 2, false)` is `"1 of 2"` and never contains `%`; `(0, 0, true)` and `(0, 0, false)` are `"None yet"`; no result contains `NaN` or `Infinity`.
  - `oneIn(142, 9)` is 16; `oneIn(30, 0)` is `null`; `baseRateLine` is `null` when `enough` is false, when `replies` is 0, and `"About 1 in 16 requests gets a reply."` for the default fixture; N of 1 gives `"Most requests get a reply."`.
  - `smallSampleNote` returns the exact sentence for 9 requests and `null` when enough.
  - `didToday` on the default plan is `[{ label: 'Requests sent', count: 2 }, { label: 'Applications sent', count: 2 }]`.
  - status maps: every label non-empty, no dash.
  - `copy-rules.test.ts` reads every non-test `.ts` and `.tsx` file under `web/src` (as `tokens.test.ts` reads CSS) and fails on: an em or en dash anywhere, or any of `streak`, `overdue`, `keep it up` in a string literal. Fix any existing hit in the same commit.
- [ ] **Step 2: Run** `pnpm test src/lib src/copy-rules.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement** the helpers and maps.
- [ ] **Step 4: Run** `pnpm test src/lib src/copy-rules.test.ts && pnpm typecheck && pnpm lint`. Expected: PASS.
- [ ] **Step 5: Commit** `git add web/src/lib web/src/copy-rules.test.ts && git commit -m "web: honest-rate helpers, plan words and a copy audit test (#33)"`

### Task 5: Plan steps, outcome actions and first-row selection

**Files:**
- Create: `web/src/routes/today/plan-steps.ts`, `plan-steps.test.ts`, `use-plan-actions.ts`, `use-plan-actions.test.tsx`
- Modify: `web/src/keyboard/list-nav.ts` (option `initial`), `web/src/keyboard/list-nav.test.tsx`

**Interfaces:**
- Consumes: Tasks 2, 3; `usePendingWrite`, `usePendingControls` (`web/src/pending/pending.tsx`).
- Produces:
  - `nextStep(row: PlanRow): 'request' | 'apply' | 'review_email' | 'find_someone' | null`: `find_someone` when `person === null`; `review_email` for an unfinished email row; `request` or `apply` for the first step with `at === null`; `null` when done or skipped.
  - `withStepLogged(plan: PlanView, entryId: Id, kind: StepKind, at: IsoTime): PlanView`: sets the step, recomputes the row's `state` and `plan.done`; a step already logged changes nothing.
  - `nextTodoAfter(rows: PlanRow[], entryId: Id): Id | null`: the next `todo` row after it, else the nearest `todo` row before it, else `null`.
  - `usePlanActions(): { logRequest(row), logApplied(row), skip(row), saveNote(row, text, reasonLine), confirmReason(row), answer(t: PromptTouch, kind: OutcomeKind, replyKind?), seeWins(ids: Id[]) }`. Pending ids: `log:<entryId>:request`, `log:<entryId>:apply`, `skip:<entryId>`, `confirm:<noteId>`, `answer:<touchId>`, `wins:seen`. Idempotency keys `plan:<entryId>:request` and `plan:<entryId>:apply`. Toast labels: `Logged: request sent to Lena Ortiz.`, `Logged: applied for Software Engineer I at Pylon.`, `Carried to tomorrow: Pylon.`; `afterCommit` adds `To change it, open Pylon's page.` Patches touch `queryKeys.plan` and `queryKeys.shell` (`plan.done`), and for `answer` also `queryKeys.outcomePrompt` and `queryKeys.wins`. Said no and asked to stop are `immediate`.
  - `useListNav` option `initial?: T`: used as the selection when the URL has no `sel`; the URL is not written until he moves.

- [ ] **Step 1: Write the failing tests:**
  - `plan-steps`: `nextStep` returns `request` for row 3, `apply` after its request is logged, `null` for row 1, `find_someone` for row 5, `review_email` for row 7; `withStepLogged` for row 3 request leaves `done` 2 and `state` `todo`; adding the apply makes `state` `done` and `done` 3; calling it twice is the same as once; `nextTodoAfter(rows, 3)` is 4, `(rows, 7)` is 6 when 6 is todo.
  - `use-plan-actions` (FixtureClient with `vi.spyOn` on `logRequest`): `logRequest calls the client once after the window with key plan:<entryId>:request`; `two presses inside the window queue one write` (one toast, one call) (Review Focus 1); `a press after the commit reaches the client with the same key and the fixture logs one touch`; `a press on a logged step shows "Already logged" and calls nothing`; `undo inside the window sends nothing and the row is todo again`; `leaving the view flushes the write`; `done count moves at once`: right after `logRequest` and `logApplied` on row 3 the cached plan (with overlay) reads `done` 3 and the shell `plan.done` 3, before any timer runs (Review Focus 4); `logApplied on a row with no role toasts "No role to apply for." and calls nothing`; `a conflict from the client drops the overlay and toasts what happened`; `answer said_no commits at once`.
  - `list-nav`: `with initial the selection is the initial row and the URL is untouched`; `j moves from the initial row`; an `initial` not in `ids` selects nothing.
- [ ] **Step 2: Run** `pnpm test src/routes/today/plan-steps.test.ts src/routes/today/use-plan-actions.test.tsx src/keyboard/list-nav.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement** the three modules and the `initial` option, following `routes/today/actions.ts` (the phase-1 pick actions) for the pending-write pattern and `routes/linkedin/index.tsx` for the overlay patch pattern.
- [ ] **Step 4: Run** the three test paths, then `pnpm test && pnpm typecheck && pnpm lint`. Expected: PASS.
- [ ] **Step 5: Commit** `git add web/src/routes/today web/src/keyboard && git commit -m "web: plan steps, outcome actions with stable keys and first-row selection (#33)"`

### Task 6: Routes and shell wiring

**Files:**
- Modify: `web/src/app/routes.tsx`, `web/src/app/pages.tsx`, `web/src/shell/app-sidebar.tsx`, `web/src/shell/app-shell.tsx`, `web/src/shell/command-palette.tsx`, `web/src/shell/shortcut-sheet.tsx` (only if group order needs it), `web/src/shell/app-sidebar.test.tsx`, `web/src/app/App.test.tsx`
- Create: `web/src/routes/wins/index.tsx` and `web/src/routes/progress/index.tsx` as titled stubs (`<h1 class="sr-only">`, an `EmptyState` "Coming in this branch") that later tasks replace

**Interfaces:**
- Consumes: Task 2 (`ShellSummary.plan`, `winsUnseen`).
- Produces: routes `/progress` and `/wins`; chords `g p`, `g w`; sidebar group "Results" with Progress and Wins; the Today count reads `2 of 7` with accessible name `Today, 2 of 7 done`; Wins shows a `ok` count badge with a dot when `winsUnseen > 0` (name `Wins, 1 new`); `VIEW_TITLE` gains `progress` and `wins`; palette "Go to" gains Progress and Wins. `/today` still renders the phase-1 Today until Task 8.

- [ ] **Step 1: Write the failing tests:** sidebar renders `Today, 2 of 7 done`, `Progress` and `Wins` links, and `Wins, 1 new` under `?scenario=win`; `g p` navigates to `/progress` and `g w` to `/wins`; the route table has no duplicate paths; the palette lists Progress and Wins under Go to; the shortcut sheet shows the two chords under Everywhere.
- [ ] **Step 2: Run** `pnpm test src/shell src/app`. Expected: FAIL.
- [ ] **Step 3: Implement** the wiring. The sidebar Today count switches from `pickedToday` to `plan` (the phase-1 Today keeps working because it reads its own query).
- [ ] **Step 4: Run** `pnpm test src/shell src/app && pnpm typecheck && pnpm lint`. Expected: PASS. The two phase-1 e2e expectations of "3 of 5" are updated in Task 8.
- [ ] **Step 5: Commit** `git add web/src/app web/src/shell web/src/routes/wins web/src/routes/progress && git commit -m "web: Progress and Wins routes, results group and chords (#33)"`

### Task 7: Shared plan components

Starts only after Devanshu approves the Task 1 mockups; follow them where they differ from this text.

**Files:**
- Create: `web/src/components/day-rail.tsx`, `note-block.tsx`, `step-marks.tsx`, `source-links.tsx`, `win-mark.tsx`, `web/src/components/plan-components.test.tsx`
- Modify: `web/src/styles/globals.css` (one `@keyframes win-draw` and one `win-in`, 240 ms, under no new token)

**Interfaces:**
- Consumes: `PlanRow`, `NoteDraft`, `SourceLink`, `CheckList` (existing), `safeHref`, `StatusBadge`, `Kbd`.
- Produces:
  - `DayRail(props: { rows: PlanRow[]; rowProps: (id: Id) => RowProps; tomorrow: PlanView['tomorrow']; allDone: boolean; asOf: IsoTime })`: `role="listbox"` named "Today's people"; each row an option with `aria-selected`, a node (empty circle todo, ring when selected and todo, check when done, a half-filled circle when skipped), the position number, person or "Find someone", company, and a status word (To do, Logged, Done, Carried over); a connecting line between nodes; a final finish node "Done for today" with `tomorrow.text`; `aria-current="step"` on the first todo.
  - `NoteBlock(props: { note: NoteDraft; onCopy: () => void; onEdit: () => void; onConfirm: () => void; editing?: ReactNode })`: the note text in the paper block, "168 of 200" characters (a `warn` tone past 90% and `bad` over the limit, always with words), the facts list ("Facts used" with each fact's text and its link as an anchor, new tab, `rel="noopener noreferrer"`), a `needsYourLine` prompt that shows the proposed `reasonLine` with "Confirm line" (key `y`) and the edit action to write his own ("Is this why you are writing to them? Confirm it or write your own."), and the note's checks through `CheckList`.
  - `StepMarks(props: { steps: PlanStep[]; role: boolean })`: "Requested" and "Applied" with a check or an empty mark and the time of day when logged; no Applied mark when `role` is false.
  - `SourceLinks(props: { links: SourceLink[] })`: anchors with the label; an unsafe URL renders its label as plain text with "No link".
  - `WinMark()`: a check whose stroke draws once (class from `win-draw`), `aria-hidden`.

- [ ] **Step 1: Write the failing tests:**
  - `DayRail names each row with its status word` (`"3. Lena Ortiz at Pylon, To do"`); `the first todo has aria-current step`; `a skipped row reads Carried over and has no red tone`; `the finish node shows tomorrow's text`; clicking a row calls its `onClick`.
  - `NoteBlock shows the count with words`; `NoteBlock flags a fact without a safe link` (a `javascript:` URL renders no anchor and shows "No link") (Review Focus 5); `NoteBlock shows facts with links before the actions`; `an over-limit note is bad toned and says "Over the limit by 12 characters"`; `needsYourLine shows the proposed line with a Confirm line button (aria-keyshortcuts y)`; `Copy and Edit buttons carry aria-keyshortcuts c and e`.
  - `StepMarks shows only Requested when there is no role`; `SourceLinks open in a new tab with noopener`.
  - `WinMark is aria-hidden`; `globals.css has win-draw and win-in inside the reduced motion override scope` (read the CSS as `tokens.test.ts` does; both animations are named in a rule the existing `prefers-reduced-motion` block disables by `animation: none !important`).
- [ ] **Step 2: Run** `pnpm test src/components/plan-components.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement** the components to the approved mockups.
- [ ] **Step 4: Run** `pnpm test src/components && pnpm typecheck && pnpm lint`. Expected: PASS.
- [ ] **Step 5: Commit** `git add web/src/components web/src/styles/globals.css && git commit -m "web: day rail, note block, step marks, source links and win mark (#33)"`

### Task 8: Today as a people plan

**Files:**
- Move: `web/src/routes/today/{index,actions,grouping,preview-panel,proposals-table,remove-dialog,still-emailing}.tsx|ts` and `today.test.tsx` to `web/src/routes/proposals/` (git mv, imports and the route path updated, behavior unchanged; reachable at `/proposals` from a quiet link "All proposed companies" under the plan)
- Rewrite: `web/src/routes/today/index.tsx`
- Create: `web/src/routes/today/plan-panel.tsx`, `find-someone-panel.tsx`, `email-row-panel.tsx`, `plan-states.tsx`, `note-editor.tsx`, `web/src/routes/today/plan.test.tsx`
- Modify: `web/src/app/pages.tsx` and `routes.tsx` (the `proposals` route and lazy page; this task owns only those two lines), `web/e2e/today.spec.ts` renamed to `web/e2e/proposals.spec.ts` and pointed at `/proposals`, `web/e2e/morning.spec.ts` steps 1 and 3 (sidebar `2 of 7`; the Pylon pick moves to `/proposals`)

**Interfaces:**
- Consumes: Tasks 2 to 7.
- Produces: `TodayPage` (default export). Bindings under group `Today`: `l` open profile, `o` open role, `a` open the apply page, `s` open the LinkedIn search (find someone rows), `c` copy the note (or the handoff on a find-someone row), `e` edit the note, `y` confirm the reason line, `r` log request sent, `p` log applied, `x` carry to tomorrow, `y` confirm the reason line, `n` not a fit (the phase-1 `RemoveDialog`, moved to `routes/proposals/remove-dialog.tsx` and imported from there), `shift+c` copy every find-someone handoff, plus `j k`, arrows and `enter` (company page) from `useListNav`. `useListNav({ ids, initial: firstTodoId, announce })`.
- Behavior: heading `h1` "Today" (sr-only) and visible `h2` of the date; a line `"7 people, 2 done"` and `"Requests this month 52 of 150"`; the rail on the left and the work panel for the selected row; the panel shows the person (name, title, kind badge, `foundOn` link), the one reason with `SourceLinks`, the role link and apply link, `NoteBlock`, `StepMarks`, and one primary button for `nextStep` (`Log request sent`, `Log applied`, `Review in Outbox`, `Open LinkedIn search`) with the other keys as quiet buttons; after the last step of a row the selection moves to `nextTodoAfter`; a logged row stays in place, relabelled; carried rows show `Carried over from Monday` in grey; `notReady` and empty plans use `EmptyState` and the existing stale banner for a failed list job; a loading skeleton at final height on first load only.

- [ ] **Step 1: Write the failing tests** (`plan.test.tsx`, FixtureClient default scenario unless noted):
  - `lands on Lena Ortiz with her reason, role link, apply link and note` (first todo selected without a key press; `Log request sent` is the primary button, with `aria-keyshortcuts="r"`).
  - `the note's fact links are visible before any action` (two anchors with `.example` hrefs).
  - `r logs the request: the row shows Requested at once, the toast reads "Logged: request sent to Lena Ortiz.", and the primary button becomes Log applied`; `p then moves selection to Jonah Park` and the sidebar reads `3 of 7`.
  - `row stays in place after it is logged` (Review Focus 4): the rail order is unchanged and row 3 is still the third option.
  - `z undoes the last log inside the window`.
  - `a row with no role has no apply button and p says "No role to apply for."`.
  - `find someone row shows the search link, c copies the handoff, no note, no log button`; `shift+c copies every handoff and toasts "Copied 1 handoff for Claude."`.
  - `the email row shows its status and a Review in Outbox link to the item, and no log key acts on it`.
  - `copy puts the saved note on the clipboard` and toasts `Copied the note.`; with an unsafe fact link it toasts `Copied. One fact has no link: check it before you send.` (Review Focus 5).
  - `edits survive moving rows`: type in the note editor on row 3, press `j`, come back, the text is still there; `Save` calls `saveNote` (text and reason line) and shows the rescanned checks.
  - `y confirms the proposed reason line`: on row 4 (`needsYourLine`) the prompt shows the proposed line; `y` calls `confirmReason`, the prompt goes and the toast reads `Confirmed your line.`; editing the line and saving makes it his (`reasonState` `written`).
  - `x carries a row`: the toast is `Carried to tomorrow: Inngest.`, the rail shows `Carried over`, and `plan.done` does not change; `x` on a find-someone row works the same way.
  - `n opens the not a fit dialog with the four reasons`.
  - `weekend scenario shows "No plan on weekends." with tomorrow's preview; job_failed shows the stale banner with the job's error; empty built plan says who to add and how`.
  - `no spinner after first load`: no element with `aria-busy="true"` and no `role="progressbar"` once data is shown, and after a log.
  - `polls keep the selected row`: a refetch that changes row order or counts does not move the selection (selected id kept).
  - phase-1 proposals tests pass unchanged at `/proposals`.
- [ ] **Step 2: Run** `pnpm test src/routes/today src/routes/proposals`. Expected: FAIL for the new tests.
- [ ] **Step 3: Implement** the page and panels. Reuse `QueryState`, `EmptyState`, `StateBanner`, `useViewQuery` (`queryKeys.plan`, `pollMs.default`), `useUrlState`. Copy and edit buttons use `aria-disabled` (not `disabled`) so focus is never dropped.
- [ ] **Step 4: Run** `pnpm test && pnpm typecheck && pnpm lint`, then `WEB_PORT=$CONDUCTOR_PORT pnpm e2e e2e/proposals.spec.ts e2e/morning.spec.ts`. Expected: PASS.
- [ ] **Step 5: Commit** `git add web/src/routes web/src/app web/e2e && git commit -m "web: Today as a plan of people with one key per step (#33)"`

### Task 9: The finish line

**Files:**
- Create: `web/src/routes/today/finish-panel.tsx`, `finish-panel.test.tsx`
- Modify: `web/src/routes/today/index.tsx` (render it in the work panel region when no row is `todo`)

**Interfaces:**
- Consumes: `didToday` (Task 4), `PlanView.tomorrow`, `PlanView.week`.
- Produces: `FinishPanel(props: { plan: PlanView })`. Content: an `h2` "Done for today" (focus target, `tabIndex={-1}`); "What you did" (`didToday` lines, then one line per row: person and company and the step); "Carried to tomorrow" (only when rows are skipped); "Tomorrow" (`tomorrow.text` and up to 3 preview lines, then "and N more"); "This week so far" (requests, accepts, replies as plain counts, a link to Progress). No streak, no "come back", nothing red.

- [ ] **Step 1: Write the failing tests:** `shows what he did from the rows` (`plan_done`: "Requests sent 6", "Applications sent 6", "Emails reviewed 1"); `previews tomorrow with at most 3 lines and "and 4 more"`; `skipped rows are listed under Carried to tomorrow`; `focus moves to the finish heading when the last step is logged` (Review Focus 4: start from default, log every remaining step by keys, assert `document.activeElement` is the heading and the live region says `All 7 done for today.`); `appears at once, with no aria-busy and no waiting for the poll`; `the week line links to /progress`; `the weekend plan (no rows) shows the not-ready state, not the finish panel`.
- [ ] **Step 2: Run** `pnpm test src/routes/today/finish-panel.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement** per the approved `today-finish.html`; announce through `useAnnounce`.
- [ ] **Step 4: Run** `pnpm test src/routes/today && pnpm typecheck && pnpm lint`. Expected: PASS.
- [ ] **Step 5: Commit** `git add web/src/routes/today && git commit -m "web: the finish line, what he did and tomorrow's preview (#33)"`

### Task 10: The 7-day "what happened?" prompt

**Files:**
- Create: `web/src/components/reply-kind-dialog.tsx`, `web/src/routes/today/outcome-prompt.tsx`, `outcome-prompt.test.tsx`
- Modify: `web/src/routes/linkedin/index.tsx` (use the extracted `ReplyKindDialog`; no behavior change), `web/src/routes/today/index.tsx` (render the section below the plan)

**Interfaces:**
- Consumes: `outcomePrompt()`, `usePlanActions().answer`.
- Produces: `OutcomePrompt()`: one question at a time for the oldest touch with no outcome: `What happened with Lena Ortiz? You sent a request on Nov 2 for Software Engineer I.`; buttons `Accepted` (1), `Replied` (2), `Nothing yet` (3); `Replied` opens `ReplyKindDialog` (Interested, Not now, Said no, Asked to stop); the section is a plain heading "Last week" with a grey count, collapsed by default when more than one is waiting, absent when none; `Nothing yet` logs `quiet` and is a normal answer. Keys `1 2 3` act on the shown question only.

- [ ] **Step 1: Write the failing tests:** `asks about the oldest touch first with its date and context`; `1 logs accepted, the question moves to the next touch, and a win appears in the wins view`; `3 logs quiet and adds no win`; `2 opens the reply dialog and Interested logs replied with kind interested`; `said no commits at once`; `undo within the window brings the question back`; `the section is absent with no touches`; `the keys do nothing while a dialog is open`; `copy has no count badge in red and no word overdue` (query for `bad` tone classes inside the section).
- [ ] **Step 2: Run** `pnpm test src/routes/today/outcome-prompt.test.tsx src/routes/linkedin`. Expected: FAIL for the new tests.
- [ ] **Step 3: Implement** and extract the dialog.
- [ ] **Step 4: Run** `pnpm test && pnpm typecheck && pnpm lint`. Expected: PASS.
- [ ] **Step 5: Commit** `git add web/src/components web/src/routes && git commit -m "web: the 7-day what happened prompt (#33)"`

### Task 11: The watch list

**Files:**
- Create: `web/src/routes/today/watch-list.tsx`, `watch-list.test.tsx`
- Modify: `web/src/routes/today/index.tsx` (render below the prompt)

**Interfaces:**
- Consumes: `watchList()`, `dismissWatch`, `watchKindLabel`.
- Produces: `WatchList()`: a collapsible "Watch list" with a grey count; each item shows its kind word, the company link, the API's `text` (the reason), the date, and one link: the careers page for `careers_changed` (`link`), the company page for `new_reason_no_role`, `/needs-you` with "Decide in Needs you" for `merge_candidate`; `Dismiss` (with undo, pending write `watch:<id>`). Empty: nothing is rendered. Closed by default; the count is plain text, never a badge color.

- [ ] **Step 1: Write the failing tests:** `lists one item of each kind with its reason`; `a careers item opens the careers link in a new tab with noopener`; `a merge item links to /needs-you`; `dismiss removes the item at once and undo brings it back`; `an unsafe link renders as text`; `nothing renders with no items`.
- [ ] **Step 2: Run** `pnpm test src/routes/today/watch-list.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement** with shadcn `Collapsible`.
- [ ] **Step 4: Run** `pnpm test && pnpm typecheck && pnpm lint`. Expected: PASS.
- [ ] **Step 5: Commit** `git add web/src/routes/today && git commit -m "web: the watch list of changed pages, new companies and merges (#33)"`

### Task 12: Wins, loud and kept

**Files:**
- Create: `web/src/routes/today/win-strip.tsx`, `web/src/routes/wins/wins.test.tsx`, `web/src/routes/today/win-strip.test.tsx`
- Modify: `web/src/routes/wins/index.tsx` (replace the stub), `web/src/routes/today/index.tsx` (strip at the very top), `web/src/shell/app-sidebar.tsx` (no change if Task 6 already shows the badge; assert only)

**Interfaces:**
- Consumes: `wins()`, `usePlanActions().seeWins`, `WinMark`, `winKindLabel`.
- Produces:
  - `WinStrip()`: `role="status"` (polite) at the top of Today above the plan, newest unseen win first as its API `line` ("Dana Whitfield accepted your request. 9:41"), up to 3 lines then "and N more", a `Seen` button (key `w`, pending write with undo), a link "Open the win log". It plays the one entrance (`win-in`, the check draws) when a win first appears in this session; reloading shows it without replaying. It persists across polls and reloads until marked seen (the server's `unseen` flag).
  - `WinsPage` at `/wins`: `h1` "Wins"; counts line (`Referrals 1, interviews 2, replies 9, accepted 41 since Oct 12`, from `counts` and `since`); a toggle "All" (1) and "Referrals and interviews" (2); a list newest first grouped by month: kind badge word (`ok` tone), person and company link, the API `line`, the role, what he sent (a collapsed "What you sent"), the date. Unseen ones carry "New" for this visit; opening the page marks them seen through an immediate write, and the "New" marks stay until he leaves. Empty: "No wins yet. A request that is accepted or a reply lands here the moment you know."

- [ ] **Step 1: Write the failing tests:** `an unseen win shows on top of Today with role status and its line`; `an unseen win survives a poll and a reload` (Review Focus 3: refetch twice, remount, still shown until `w`); `w marks seen, the strip goes, undo within the window brings it back`; `said no is not a win` (a reply with kind said_no never appears in the strip or the log); `undoing an accept inside the window removes its win`; `the strip shows up to 3 lines and "and 2 more"`; `the Wins page lists every kept win newest first with counts`; `1 and 2 switch the toggle and the URL`; `opening the page marks wins seen but keeps New marks for the visit`; `the empty page says what lands here`; `the strip does not animate when reduced motion is set` (class `win-in` absent when `matchMedia('(prefers-reduced-motion: reduce)')` matches); `the sidebar Wins badge clears after they are seen`.
- [ ] **Step 2: Run** `pnpm test src/routes/wins src/routes/today/win-strip.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement** per the approved `wins.html`.
- [ ] **Step 4: Run** `pnpm test && pnpm typecheck && pnpm lint`, then `WEB_PORT=$CONDUCTOR_PORT pnpm e2e e2e/shell.spec.ts`. Expected: PASS.
- [ ] **Step 5: Commit** `git add web/src/routes web/src/shell && git commit -m "web: the win banner and the win log (#33)"`

### Task 13: Progress

**Files:**
- Create: `web/src/routes/progress/funnel-ledger.tsx`, `progress.test.tsx`
- Modify: `web/src/routes/progress/index.tsx` (replace the stub)

**Interfaces:**
- Consumes: `progress()`, `formatRate`, `smallSampleNote`.
- Produces: `ProgressPage` (default export): `h1` "Progress"; "Today" ledger (planned and done, requests, applications, accepted, replied, each `n of m` where a plan number exists); a period toggle Week (1) and Month (2) in `?period=`; `FunnelLedger(props: { period: ProgressPeriod; minRequests: number })`: rows in funnel order, each with the stage label, count, `formatRate(count, from.count, period.enough)` beside "of N requests" or "of N replies", and a thin monochrome bar whose width is `count / max count of the period` (a `role="img"` with a text alternative, 2 px minimum for a non-zero count); the small-sample note under the table when `!period.enough`; the effort list ("Requests sent 31", "Applications 24", ...) below; a link to the Wins page. Zero is a plain grey `0`.

- [ ] **Step 1: Write the failing tests:** `shows today against the plan` (`2 of 7`); `the week ledger shows 9 of 31 (29%) for accepts and 2 of 31 (6%) for replies`; `referrals show 0 of 2 replies`; `bars scale to the period's largest count and a 2 count is not a tiny sliver of 31 drawn as zero` (non-zero minimum 2 px); `first_week shows counts with no percent and the small-sample note` (Review Focus 2); `1 and 2 switch the period and the URL`; `month shows its own label and numbers`; `fresh shows "Nothing logged yet. Log a request on Today and it counts here." and no NaN`; `no stat tile and no streak copy` (no element with a big-number tile class; the copy audit passes); `the effort list shows work done`.
- [ ] **Step 2: Run** `pnpm test src/routes/progress`. Expected: FAIL.
- [ ] **Step 3: Implement** per the approved `progress.html`.
- [ ] **Step 4: Run** `pnpm test && pnpm typecheck && pnpm lint`. Expected: PASS.
- [ ] **Step 5: Commit** `git add web/src/routes/progress && git commit -m "web: progress with today against the plan and the week and month funnel (#33)"`

### Task 14: Quiet week and base rates

**Files:**
- Create: `web/src/routes/progress/quiet-week.tsx`, `quiet-week.test.tsx`
- Modify: `web/src/routes/progress/index.tsx` (use it when the selected period is `quiet`)

**Interfaces:**
- Consumes: `ProgressPeriod.quiet`, `BaseRates`, `baseRateLine`.
- Produces: `QuietWeek(props: { period: ProgressPeriod; base: BaseRates })`: the effort list first under "Your work this week" (counts only), then one honest base-rate paragraph from his own data: `"About 1 in 16 requests gets a reply, and replies often take about 6 days."` (the days clause only when `medianDaysToReply` is set), then the ledger muted below. When `!base.enough`: `"Not enough outcomes yet to say what is usual. It takes about 30 requests."` (30 from `minRequests`). With zero replies ever: the paragraph is omitted, never "1 in 0". The copy never says normal, failure, or any guilt word, and states only what the numbers say.

- [ ] **Step 1: Write the failing tests:** `quiet_week puts the effort list before the funnel`; `shows the base rate and median days from his own data`; `no median days drops the days clause`; `not enough outcomes shows the minimum sample line` and no rate; `zero replies ever shows no base rate line` (Review Focus 2); `the zero counts are grey not red` (no `text-bad` or `bad` tone class); `the non-quiet week does not use this arrangement`; `the month toggle keeps the same arrangement when the month is quiet`.
- [ ] **Step 2: Run** `pnpm test src/routes/progress/quiet-week.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement** per the approved `progress-quiet.html`.
- [ ] **Step 4: Run** `pnpm test && pnpm typecheck && pnpm lint`. Expected: PASS.
- [ ] **Step 5: Commit** `git add web/src/routes/progress && git commit -m "web: the quiet-week view with effort first and his own base rates (#33)"`

### Task 15: Monthly findings

**Files:**
- Create: `web/src/routes/progress/findings.tsx`, `findings.test.tsx`
- Modify: `web/src/routes/progress/index.tsx` (render below the ledger)

**Interfaces:**
- Consumes: `findings()`, `formatRate`.
- Produces: `Findings()`: heading "What the data says" with the month ("October") and the report date; each finding: the API's plain `text`, its counts as `"14 of 33 (42%)"` beside the group label (always the count with the rate; the rate only when the group has at least 20, else the count alone), a `caution` line in grey when present, and `Changed: ...` when `changed` is set; a collapsed "Earlier changes" list from `changes`. Empty (`findings: []` or `reportedOn: null`): `"The first findings arrive after your first month of outcomes."`. One or two findings only are shown; more are cut by the API, not the UI.

- [ ] **Step 1: Write the failing tests:** `shows each finding with its counts and rates`; `the caution shows for the small group and no percent is drawn for a group under 20`; `Changed line shows only when set`; `empty state says when findings arrive`; `earlier changes are collapsed and listed by date`; `a finding text is rendered as given (no UI rewording)`.
- [ ] **Step 2: Run** `pnpm test src/routes/progress/findings.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** `pnpm test && pnpm typecheck && pnpm lint`. Expected: PASS.
- [ ] **Step 5: Commit** `git add web/src/routes/progress && git commit -m "web: monthly findings from the outcome report (#33)"`

### Task 16: Company page score breakdown

**Files:**
- Create: `web/src/routes/company/tabs/score.tsx`, `web/src/routes/company/tabs/score.test.tsx`
- Modify: `web/src/routes/company/index.tsx` (the seventh tab `score`, key `7`, count = signals), `web/src/routes/company/company.test.tsx` (tab list)

**Interfaces:**
- Consumes: `score(companyId)` through its own `useViewQuery(queryKeys.score(id), ...)` so the tab loads, fails and retries on its own (the existing `TabBoundary` pattern).
- Produces: `Score({ companyId })`: header "Score 0.74, rank 12 of 1,010" with the strongest lane named; a ledger, one row per signal in API order: label, a signed bar from `share` (right for positive, left for negative, a short grey tick for `neutral`), `points`, `share` as a percent of the total, the `reason` sentence, and its `source` as a link; a neutral signal reads "No data, scored neutral" in place of a bar; a footer "Ranker v4. Weights live in the ranking config. A change makes a new version." and, when `previous` is set, "Was 0.52 under v3". Also (ruling 12): the company page's postings list shows each posting's `flags` (field, both values, both quotes) and the No-go view lists `hiddenCompanies` with kind, reason and "hidden, pending reader" when provisional.

- [ ] **Step 1: Write the failing tests:** `lists every signal with its reason and share`; `the shares printed add to 100 percent of the absolute total`; `a negative signal draws left and reads as a cost`; `the neutral size signal says "No data, scored neutral" and has no bar width`; `a source with an unsafe URL is plain text`; `key 7 opens the Score tab`; `a failing score read shows the partial state with Retry and the other tabs still work`; `the previous version line shows only when set`; `posting flags show both values and both quotes`; `the No-go view lists ranking hides with their reason and the pending-reader wording`.
- [ ] **Step 2: Run** `pnpm test src/routes/company`. Expected: FAIL.
- [ ] **Step 3: Implement** per the approved `company-score.html`.
- [ ] **Step 4: Run** `pnpm test && pnpm typecheck && pnpm lint`. Expected: PASS.
- [ ] **Step 5: Commit** `git add web/src/routes/company && git commit -m "web: the score breakdown tab on the company page (#33)"`

### Task 17: Accessibility, motion and copy audit

**Files:**
- Create: `web/src/a11y-plan.test.tsx`
- Modify: whichever view files the tests find short (named in the commit)
- Modify: `web/src/copy-rules.test.ts` (add the new view folders to its scan; it already scans all of `src`)

**Interfaces:** consumes Tasks 7 to 16.

- [ ] **Step 1: Write the failing tests** (rendered with the shell, per scenario):
  - `focus order on Today`: Tab from the top bar reaches the rail, then the panel's links and buttons in reading order, then the prompt, then the watch list; nothing in the rail is a tab stop except the selected row (listbox, arrow keys inside).
  - `every key-driven button has aria-keyshortcuts and a visible Kbd` across Today, the prompt, the win strip, Wins, Progress.
  - `one h1 per route and no skipped heading levels` for Today, Progress, Wins, Score tab.
  - `live regions`: logging announces `Logged: request sent to Lena Ortiz.`; the win strip is `role="status"`; the finish line is announced once.
  - `no action drops focus`: after `r`, `document.activeElement` is still inside the page (the button uses `aria-disabled`).
  - `reduced motion`: with the media query matching, no element carries `win-in` or `win-draw`, and `globals.css` contains the global override.
  - `contrast`: every tone used by the new views is one of the five status pairs; `tokens.test.ts` stays green (no new token).
  - `copy audit`: the whole of `src` has no em or en dash and none of the guilt words (`copy-rules.test.ts`); every new status or lane word has no dash.
- [ ] **Step 2: Run** `pnpm test src/a11y-plan.test.tsx src/copy-rules.test.ts`. Expected: FAIL where gaps exist.
- [ ] **Step 3: Fix** the views the tests name; do not weaken a test.
- [ ] **Step 4: Run** `pnpm test && pnpm typecheck && pnpm lint`. Expected: PASS.
- [ ] **Step 5: Commit** `git add web/src && git commit -m "web: accessibility, reduced motion and copy audit for the plan views (#33)"`

### Task 18: e2e specs

**Files:**
- Create: `web/e2e/helpers.ts` (the shared `watchRequests` and `expectCleanPage` from `morning.spec.ts`, moved), `web/e2e/plan.spec.ts`, `web/e2e/logging.spec.ts`
- Modify: `web/e2e/morning.spec.ts` (import the helpers; steps for Today read as the plan: the first person is ready, `r` and `p` log, the sidebar reads `3 of 7`)

**Interfaces:** consumes the whole app on fixtures. Every spec: `page.clock.install` before load (to drive the 8 s window with `page.clock.runFor(8000)`), `expectCleanPage` on every page (no console error, no request to a host other than `127.0.0.1:${WEB_PORT}`, no button named `Send...`), external links are asserted by `href` and `target` and never followed (`page.route` aborts any other host). Run with `WEB_PORT=$CONDUCTOR_PORT pnpm e2e`.

- [ ] **Step 1: Write `plan.spec.ts`, the morning people-plan flow:**
  - `opens on the first person ready`: `/today` shows `Tuesday, November 10`, `7 people, 2 done`, Lena Ortiz selected, her reason, role link, apply link and note with two fact links visible, `Log request sent` as the primary button; no `[aria-busy="true"]` and no progressbar after the first paint.
  - `copy the note, log the request, apply, move on`: grant clipboard permission; `c` puts the note text on the clipboard; `r` shows `Requested` at once and the toast `Logged: request sent to Lena Ortiz.`; `p` completes the row, selection moves to Jonah Park, sidebar `3 of 7`; `z` inside the window restores Lena's row.
  - `find someone`: `j` to Cerebrium, `s` triggers a popup whose URL host is `www.linkedin.com` and path starts `/search/results/people/` (the request is aborted by the spec), `c` copies the handoff.
  - `skip carries a row` (`x`) and the rail reads `Carried over`.
  - `finishing the day`: log the remaining steps by keys; `Done for today` is focused, "What you did" and tomorrow's preview are visible, the sidebar reads `7 of 7`, and no red element exists on the page.
  - `focus order`: Tab through the page; the order is sidebar, top bar, rail, panel, prompt, watch list.
- [ ] **Step 2: Write `logging.spec.ts`:**
  - `double press logs one request` (Review Focus 1): press `r` twice quickly, one toast, after `runFor(8000)` open `/progress` and the Today ledger says requests 3 (not 4).
  - `a win stays on top until seen` (`?scenario=win`): the strip shows with role status, survives `page.reload()`, `w` removes it, `z` brings it back; `/wins` lists it with `New`.
  - `the what happened prompt`: `1` accepted adds a win; `3` clears the question with no win; `2` then Interested logs a reply.
  - `progress is honest`: default week shows `9 of 31 (29%)`; `?scenario=first_week` shows counts, no `%` and the small-sample note; `?scenario=quiet_week` shows the effort list before the ledger and the line `About 1 in 16 requests gets a reply`.
  - `findings and the score tab`: `/progress` shows the two findings with counts and the caution; the Pylon company page `?tab=score` lists the signals with reasons and links; the neutral signal says `No data, scored neutral`.
  - `a request logged on Today shows on Progress without waiting for a poll`.
- [ ] **Step 3: Run** `WEB_PORT=$CONDUCTOR_PORT pnpm e2e`. Expected: FAIL first on the specs that need the app, then PASS after fixes; fix the app, not the specs, unless a spec is wrong.
- [ ] **Step 4: Run** the whole suite twice. Expected: PASS both times (no flake).
- [ ] **Step 5: Commit** `git add web/e2e && git commit -m "web: e2e for the morning people plan and the logging flow (#33)"`

### Task 19: Final verification, docs and the design review

**Files:**
- Modify: `docs/README.md` (a line for this plan, the new mockups, and how to see each scenario: `?scenario=win`, `quiet_week`, `first_week`, `plan_done`, `fresh`), `docs/design/README.md` (mark the old 5.1 Today as moved to Proposals, add the new views if Task 1 left anything open)

- [ ] **Step 1: Run everything:** `pnpm lint && pnpm typecheck && pnpm test && pnpm build && WEB_PORT=$CONDUCTOR_PORT pnpm e2e`. Expected: all PASS; `vite build` writes `web/dist/`.
- [ ] **Step 2: Look at it** with `pnpm dev --port $CONDUCTOR_PORT`: every new view at 1440 by 900 and 1280 by 800, light and dark, each scenario, against the approved mockups; fix what differs.
- [ ] **Step 3: Run the Section 16 review:** copy the matrix above into the PR body with a pass or fail and the test names beside each row; Devanshu signs off the look and the flow (issue "Done when").
- [ ] **Step 4: Run** `grep -rnP '\xe2\x80[\x93\x94]' web/src web/e2e docs/README.md docs/design`. Expected: no output.
- [ ] **Step 5: Commit** `git add docs/README.md docs/design/README.md && git commit -m "Docs: map the web experience plan and its scenarios (#33)"`

---

## Coverage of the issue's scope

| Scope line | Task |
|---|---|
| Client types and fixtures for plan, touches, applications, scores, progress, shaped for #11's types | 2, 3 |
| Today as a people plan: person, profile link, role link, one reason, note with fact links, apply link, find someone with a LinkedIn search link | 2, 7, 8 |
| One-key outcome logging with undo and an idempotency key | 3, 5, 8, 18 |
| Finish line (what he did, tomorrow's preview) | 9 |
| Progress: today against plan, week and month funnel, honest numbers, rates with counts, small-sample note | 4, 13 |
| Win log and loud wins | 12 |
| Quiet-week view with effort and his own base rates | 14 |
| Monthly findings | 15 |
| 7-day "what happened?" prompt | 10 |
| Company page breakdown of every signal and its share | 16 |
| Watch list (careers changed, new reason-no-role, merge items) | 11 |
| Accessibility, copy rules | 4, 17 |
| e2e for the morning flow and the logging flow, with `WEB_PORT` | 18 |
| Section 16 as acceptance criteria per view | matrix, 19 |

## Open questions

**For Devanshu (see as a mockup first, Task 1):**
1. **Today layout:** the rail plus work panel (one person at a time, the day as a line with a finish node) instead of a table of 7 rows. This is the biggest visual change from the approved Today.
2. **The finish panel and the win banner moment:** the quiet check that draws once and a banner that stays until you mark it seen. Is that loud enough, or too much?
3. **Progress as a ledger** (counts, rates with their counts, a thin bar) rather than stat tiles or a funnel graphic, and the quiet-week arrangement (your work first, your own base rate second).
4. **Wins log and Score tab** layouts.

**Decisions with a default this plan builds (change any before Task 8):**
5. The phase-1 pick, backlog and remove view moves to `/proposals`, unchanged, linked quietly under the plan. Default: keep it until the switch, then decide whether to delete it. The pipeline design replaced "pick 5 of 15 to 20 companies" with a plan of people.
6. Email rows appear in the plan (as "Review in Outbox"); the sender and reply reader log email outcomes, so no logging key acts on them.
7. Recognition (the banner moment) fires on any accept or reply. The win log keeps referrals, interviews, offers and replies tagged Interested. A "Not now" reply counts as a reply in progress, not as a win; "Said no" and "Asked to stop" are neither (ruling 3).
8. "Nothing yet" on the 7-day prompt is a normal answer: it asks once more 7 days later, and after 21 days with no outcome the touch is marked quiet on its own, without asking again (ruling 4).
9. The minimum sample before any rate is shown is 30 requests (the API's `minRequests`), and a finding group shows a rate only from 20. Change the number in the API config, not the UI.
10. Opening the Wins page counts as seeing them (the "New" marks stay for that visit).
11. Note length comes from the API (`maxChars`). The research says plan for 200 (the newest LinkedIn page; an older page says 300 for Premium). Default: 200.
12. A wrong log after its 8 second window is fixed from the company page or in the side session. This plan builds no "take back" action; it can be added in #11 as an appended correcting event.

**For #11 and #30 (flag before the API lands; the types here assume them):**
13. **One write path.** The LinkedIn list's `logLinkedIn` and the plan's `logRequest` must write the same `outreach_touch` (and `linkedin_log` if it stays). Two paths would double count in Progress.
14. **Applications have no person.** The foundation's `log_touch(person_id: int, ...)` needs a person; an application is a role, not a person. `logApplied` assumes the API writes the `application` row (and a touch with a null person, or none). Make `person_id` nullable for channel `application`, or log it only in `application`.
15. **A reply on a quiet touch.** `outreach_touch` forbids `quiet_at` and `replied_at` together. `record_outcome('replied')` must clear `quiet_at`, or a late reply after "Nothing yet" is rejected.
16. **Derived fields the UI will not compute:** `PlanRow.state`, `reason` and `reasonSources`, `NoteDraft.needsYourLine`, `ProgressPeriod.enough` and `quiet`, `BaseRates.enough`, `Win.line` and `unseen`, `ScoreSignal.share`, `FunnelStage.from`, `PlanView.tomorrow`. Week boundaries are Monday to Sunday in `America/New_York`, decided by the API.
17. **Merge items** in the watch list link to Needs you. The side by side merge confirm belongs to #16; until it lands, the foundation's `merge_candidate` kind only has the status word added here.
