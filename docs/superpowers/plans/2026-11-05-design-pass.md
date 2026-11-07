# Design pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Knock screen read in five seconds, starting with the two he uses all day (Jobs and Today), without removing anything he acts on.

**Architecture:** No new UI library: the app is already shadcn (new-york, zinc, Tailwind 4) and every pattern below is built from the components in `web/src/components/ui`. The work is composition: one shared `PageHeader`, one rule for where key hints and row actions show, fewer controls per row, and unknowns said once. Each task is its own PR from `origin/master` (never stacked).

**Tech Stack:** React 19, Vite, Tailwind 4, shadcn/ui and Radix, lucide, vitest, Playwright.

**Spec:** `docs/design/README.md` sections 1, 10 and 16.1c (the visual system and the owner's Today ruling of 2026-10-27), `~/.claude/skills/craft` (the design standard and its gate), and the taste notes below. #9 wins over both where they differ.

## What his taste is, and where it comes from

Read before changing a pixel. Each line is evidence, not a guess.

| Taste | Evidence | What it means here |
|---|---|---|
| Answer first, evidence next, detail on demand. Light, calm, one accent, two-tone headline, a "what to do next" column. | The Remit page he approved (`DevVault/skills/craft/examples/remit-webhook-detail.png`) | Each page opens with one plain sentence and the next action. Raw detail stays closed. |
| Not dense-everything-at-once: no all-caps labels, mono labels, middle-dot strings, alert banners plus fact cards plus legends together. | The Kestrel page he rejected, and three ArkenOS prototypes (craft `sources.md`) | No shortcut legends on the canvas. No dot-joined meta strings. |
| Premium monochrome minimalism: restraint, type, air, 150 to 300 ms motion. No theatrics, no decorative color. | `DevVault/memory/feedback_premium_monochrome_minimalism.md` (portfolio, 2026-06-19) | Color is status or the one accent. Nothing moves unless he acted. |
| User utility beats looks. Never remove information he acts on; justify each removal in his terms. | `DevVault/memory/feedback_ux_over_aesthetics.md` (Saar, GET-16) | Every removal in this plan names where the fact still lives. A removal that loses a fact he uses is rejected. |
| Today was "so dull" and "not easy on the eyes"; the right side is for things that do not change. | Owner ruling in `docs/design/README.md` 16.1c | Keep the three columns, the monograms, the profile column. Do not flatten Today. |
| One rule: right job, right company, right time. He judges every posting himself, wants facts not scores, never reads "why it ranked" text. | `browse-he-decides` memory (2026-11-01), #9 | Jobs is the main screen. Rows are fast-check: age, title, level, size, place. No new scores. |
| Replies per month is the goal and the loudest number on every screen. | `docs/design/README.md` section 1 | The replies count gets weight in the shell. Housekeeping numbers get less. |
| Keyboard first, honest unknowns (never red), plain words, no dashes. | `docs/design/README.md` sections 1, 6, 11 | Keys keep working everywhere. Hints move, they do not go. |
| Thorough but simple: one task, one review, one fix round. | `quality-over-cost`, `keep-it-simple` memory | Small PRs. No clever component framework. |

The craft register for this app is **tool: quiet, dense, fast**. Signature: the K tile and the eight calm monogram hues (already in the app); the one accent stays violet in the four jobs section 10 lists. No bold move.

## Audit: what is unclear today

From screenshots of every route on the sample data, 1440 by 900, light.

| Screen | Problem | Fix (task) |
|---|---|---|
| Every page | Ten copies of `<h1 className="text-xl font-semibold tracking-tight">` with different sub-lines; no shared header. | `PageHeader` (1) |
| Every page | A key hint sits on nearly every button (S, C, A, O, L, X, P, E, N, plus legends). Hints stop meaning anything. | Hint policy (1) |
| Sidebar footer | Four equal lines (Sending, replies, Boards read, Coverage). Replies, the goal, is as quiet as a cron time. | Footer (1) |
| Jobs | Ten filter buttons in two rows, a shortcut legend in the header, Pass and Go ahead on every row (two buttons times every row), the level said twice ("Software Engineer, New Grad" then "new grad"), meta as loose words with no order. | Jobs (2) |
| Today | Header is three lines. Last week is open by default and pushes the pick's own actions down. Reasons chips repeat the profile column at 1280 and up. | Today (3) |
| Needs you | Three buttons with key chips on every row, in a bordered card per group. | Needs you (4) |
| Outbox, Progress, Wins, Done, No-go | Close to the bar already. Progress shows plain tables inside boxes. Headers differ. | Sweep (5) |

## Global Constraints

- Closes: a new issue per task, opened when its PR is. This plan carries no issue. No dashes of any kind (em, en, or a spaced hyphen as a dash) and no middle dots in any copy, comment, commit or PR text. Sentence case. No all-caps labels. Real quotes and apostrophes on screen.
- #9 and `docs/design/README.md` win. Where a task changes a rule there, it edits that file in the same PR (section 16.1c and 10 for Today, the matching section for the others).
- shadcn components stay shadcn. A new component lands in `web/src/components`, uses `cn` and the existing tokens, and has a test.
- Tokens: no new colors. Violet stays at the four jobs in section 10. Status colors only on status. Radius by class (controls 6 px, containers 8 to 12 px). Shadows only on floating layers and the selected rail row that already has one.
- The UI never sends, never classifies, and has no Send button. No task touches `outreach/` (Python).
- Every removal keeps its fact somewhere the same decision can still be made, named in the PR body. The PR body lists each removal and where the fact lives now.
- Tests: `pnpm typecheck`, `pnpm lint`, `pnpm test`, and the touched pages' Playwright specs with `WEB_PORT=$((CONDUCTOR_PORT+2))`. Dev servers use `CONDUCTOR_PORT` and never 3001, 3002, 3011, 3013 or 3100. A test that asserted the old design is changed in the same commit and the PR says why.
- Craft gate before "done": `python3 ~/.claude/skills/craft/scripts/gate.py http://127.0.0.1:<port>/<route>` on each touched route in light and dark, and look at the screenshots.
- Skills: invoke `craft` before UI code in each task (it is the design standard), and `unslop` on every PR body and commit message.

## Review Focus

1. A company with almost nothing known (Trigger.dev on the `queue` scenario): Today's profile and the Jobs row stay short and truthful. Test in Task 2 and the existing profile test.
2. A very long job title or company name, and a title that already contains the level: nothing overflows at 1280 or 390 and the level is not said twice. Tests in Task 2.
3. Keyboard only on Jobs: move, open, pass, go ahead all work with no hint visible on unselected rows, and the `?` sheet lists them. Test in Task 1 and Task 2.
4. Screen reader: a row's name, the actions on it and its key shortcuts are still announced when the buttons are not drawn for unselected rows (`aria-keyshortcuts` stays on the row's group). Test in Task 2.
5. Narrow window (1024 and 390): Jobs filters, the Needs you menu and the shell footer popover are reachable with 44 px targets. Playwright check in Tasks 1, 2 and 4.

---

## Decisions I need from you before Task 1 starts

I recommend each; say "go" to take all of them.

1. **Key hints.** Show a hint only on a screen's one primary action and on the selected row. Every other key stays working and is listed in the `?` sheet and in the button's tooltip. (Alternative: keep every hint, as now.)
2. **Row actions on Jobs.** Pass and Go ahead appear on the selected row, on keyboard focus and on hover, and every row is clickable to select it. (Alternative: keep both buttons on every row.)
3. **Violet.** Keep it exactly as section 10 has it. (Alternative: go fully monochrome like your portfolio and drop the K tile color, ring and sidebar bar. I would not: the app is a tool you stare at daily and the focus ring needs a hue that passes 3:1 on both themes.)

---

### Task 1: Shared header, hint policy and the shell footer

**Files:**
- Create: `web/src/components/page-header.tsx`, `web/src/components/page-header.test.tsx`
- Modify: `web/src/components/ui/kbd.tsx` only if the policy needs a variant; `web/src/shell/app-sidebar.tsx:223-343` (the footer); `web/src/shell/shortcut-sheet.tsx`; the `<h1>` blocks in `routes/browse/index.tsx:311`, `routes/needs-you/index.tsx:559`, `routes/done/index.tsx:22`, `routes/linkedin/index.tsx:318`, `routes/no-go/index.tsx:231`, `routes/progress/index.tsx:89`, `routes/wins/index.tsx:141`
- Test: `web/src/shell/app-sidebar.test.tsx`, `web/e2e/shell.spec.ts`

**Interfaces:**
- Produces: `PageHeader({ title: string; summary?: ReactNode; aside?: ReactNode; className?: string })`. Renders one `<h1 className="text-xl leading-7 font-semibold tracking-[-0.01em]">`, a muted `summary` line at 13 px that is `aria-live="polite"`, and `aside` right-aligned on the same row as the title.
- Produces: `docs/design/README.md` section 6 gains "Key hints": a hint is drawn on the primary action and the selected row only.

- [ ] **Step 1: Write the failing tests.**
  - `page-header.test.tsx`: `it('renders the title as the page heading and the summary beneath it')`, asserts `getByRole('heading', { level: 1, name: 'Jobs' })` and the summary text; `it('puts the aside on the title row')`.
  - `app-sidebar.test.tsx`: `it('shows replies as the loudest footer line')` asserts the replies count element has `text-base` or larger and sits above the sending line; `it('keeps Boards read and Coverage behind one Pipeline row')` asserts neither text is in the footer until the row is opened, then both are in the popover.
- [ ] **Step 2: Run them to see them fail.** `cd web && pnpm exec vitest run src/components/page-header.test.tsx src/shell/app-sidebar.test.tsx`; expect "Cannot find module" and the two assertion failures.
- [ ] **Step 3: Implement `PageHeader`** and swap the seven `<h1>` blocks to use it, keeping each page's own summary text and `aria-live` where it has one.
- [ ] **Step 4: Rework `Footer` in `app-sidebar.tsx`.** Order: the replies line first (the number at 16 px semibold with "replies in November" muted beside it, linking to Wins), then Sending (dot, "20 of 30 today"), then one `Pipeline` row (`BoardsRead` and `PipelineScore` move into a shadcn `Popover` opened from it, with the same words). Failing states keep their own tone and stay visible in the footer (a stopped sender, boards that failed to read), never hidden in the popover.
- [ ] **Step 5: Write the hint policy** into `docs/design/README.md` section 6, and list every key per page in `shortcut-sheet.tsx` so nothing depends on a visible hint. Add a test in `shortcut-sheet` tests that each page's keys are listed.
- [ ] **Step 6: Verify.** `pnpm typecheck && pnpm lint && pnpm test`, then `WEB_PORT=$((CONDUCTOR_PORT+2)) pnpm e2e e2e/shell.spec.ts e2e/morning.spec.ts`, then the craft gate on `/today` and `/needs-you`. Expected: all pass.
- [ ] **Step 7: Commit** `Shell: one page header, key hints on the primary action only, replies first in the footer`, open a PR to master.

### Task 2: Jobs, the main screen

**Files:**
- Modify: `web/src/routes/browse/index.tsx` (header 305-380, `JobItem` 93-162, `Flags` 81-92)
- Create: `web/src/routes/browse/job-filters.tsx` (the filter bar, so `index.tsx` shrinks)
- Test: `web/src/routes/browse/browse.test.tsx`, `web/e2e/browse.spec.ts`

**Interfaces:**
- Consumes: `PageHeader` (Task 1), `Popover` and `Select` from `components/ui`.
- Produces: `JobFilters({ windowDays: JobsWindow; size: JobsSize; notContacted: boolean; q: string; onChange: (next: Partial<...>) => void })` and a pure `levelAlreadyInTitle(title: string, levelTag: string): boolean`.

Target row (one line at 1280, about 48 px; a second line only when it has flags). Columns align on one spine: age, title, company, size, place, flags, actions.

```
 today    Software Engineer, New Grad     Retell AI     45 people      San Francisco, CA      E-Verify  YC     [Pass] [Go ahead]
 1 day    Software Engineer, Early Career Pylon         120 people     New York, NY
 6 days   Software Engineer, Early Career Helix         Big, 2,400     San Francisco, CA      Says no sponsorship
```

Rules: age is the left column, tabular and right-aligned, the first thing his eye hits (how long it has been open is the first thing he checks). The level word is shown only when the title does not already say it. Size reads "45 people", or "Big, 2,400", or "Size not known". Flags keep their status colors (warn for no sponsorship, info for contacted) and sit at the row end. Buttons draw on the selected row, on focus within, and on hover; the row group keeps `aria-keyshortcuts` on the selected row so the keys announce.

Header target: `PageHeader` (Jobs, "20 at your level in the last 30 days"), then one row: the Level tabs on the left, then `JobFilters` as a search box and three controls ("Posted: 30 days", "Size: Any", and a "Not contacted" toggle) on the right. The key legend leaves the header and goes to `?`.

- [ ] **Step 1: Write the failing tests** in `browse.test.tsx`:
  - `it('shows the level only when the title does not say it')`: `levelAlreadyInTitle('Software Engineer, New Grad', 'new grad')` is true; `('Software Engineer I', 'SDE 1')` is false.
  - `it('draws Pass and Go ahead only on the selected row')`: with 3 jobs, one `group` named "Your answer" is in the document; pressing ArrowDown moves it; `X` and `A` still answer the selected row.
  - `it('puts the filters in three controls')`: the Posted and Size controls open a listbox with the same options as today (7, 30, 60, 90 days, Any time; Any size, Small, Big, Not known), choosing one changes the list query exactly as the old buttons did.
  - `it('does not overflow with a 120 character title')` at 390 and 1280 via the layout helper the Today tests use.
  - `it('keeps the empty states')`: no jobs at all, and no match after a filter, each with its own words (existing tests stay green).
- [ ] **Step 2: Run to fail.** `pnpm exec vitest run src/routes/browse`.
- [ ] **Step 3: Implement** `levelAlreadyInTitle`, the new `JobItem` layout (CSS grid with `grid-template-columns: 56px minmax(0,1fr) auto`, the middle column holding title and meta, no dot-joined strings), `JobFilters` with `Select` for window and size and a `Toggle` for "Not contacted yet", and move the legend to `shortcut-sheet.tsx`.
- [ ] **Step 4: Update `e2e/browse.spec.ts`** for the controls. Keep every behavior assertion (filters, pass, go ahead, undo, empty states).
- [ ] **Step 5: Verify.** `pnpm typecheck && pnpm lint && pnpm test`, then `WEB_PORT=$((CONDUCTOR_PORT+2)) pnpm e2e e2e/browse.spec.ts`, then the gate at 1440, 1024 and 390 on `/browse`, light and dark.
- [ ] **Step 6: Edit `docs/design/README.md`** (the Jobs section, or add one) to match; commit `Jobs: one line per job, age first, filters in three controls, actions on the selected row`; PR.

### Task 3: Today, the three loose ends

**Files:**
- Modify: `web/src/routes/today/index.tsx` (the head near the date), `web/src/routes/today/in-flight-list.tsx`, `web/src/routes/today/watch-list.tsx`, `web/src/routes/today/win-strip.tsx`, `web/src/routes/today/email-lane.tsx` (the lower stack), `web/src/routes/today/why-today.tsx`
- Test: `today-layout.test.tsx`, `win-strip.test.tsx`, `watch-list.test.tsx`, `e2e/today-queue.spec.ts`

- [ ] **Step 1: Head as one two-tone line.** "Tuesday, November 10" in foreground and "8 picks, 6 can be emailed" muted at the same size beside it (craft's two-tone heading). The count of passed and carried-over stays in that line. Test: the heading text and the muted continuation are in one `<h1>`/`<p>` pair and `getByText("6 of today's 8 can be emailed")` is replaced by the new wording in the one test that asserts it.
- [ ] **Step 2: Lower stack closed by default.** "Email today", "In flight" and "Last week, what happened?" all start closed with their counts on the closed row. "Last week" opens by itself only when it holds a reply he has not answered (the outcome prompt). Test: closed by default; opens when the prompt exists; the count is on the closed row.
- [ ] **Step 3: Why today shows each fact once.** At 1280 and up the profile column already shows E-Verify, sponsorship, size and stage, so the chips for those four leave Why today at that width and stay below 1280 (use the existing `use-min-width` hook). Why today keeps the new-opening tag, hiring momentum and the ranker's own reasons. Test: at 1280 the four chips are absent from Why today and present in the profile; at 1100 they are present in Why today. Review check from `feedback_ux_over_aesthetics`: nothing is lost at either width.
- [ ] **Step 4: Update `docs/design/README.md` 16.1c** to match everything in PR #178 and this task (see the file's current lines about "Not known", "Judge: not judged yet." and the research links).
- [ ] **Step 5: Verify** as Task 1 with `e2e/today-queue.spec.ts`. Commit `Today: one header line, the lower stack closed, each fact said once`; PR.

### Task 4: Needs you, one fix per row

**Files:**
- Modify: `web/src/routes/needs-you/review-item.tsx:294-353`, `web/src/routes/needs-you/action-button.tsx`
- Test: `web/src/routes/needs-you/*.test.tsx`, `web/e2e/needs-you.spec.ts`

Each row keeps its one primary fix with its key chip. "Leave as is" and "Mark no-go" move into a shadcn `DropdownMenu` on a `MoreHorizontal` button, each with its key shown in the menu, and the keys (O, N) keep working on the selected row. The group card border goes: groups are separated by space and a hairline, per craft.

- [ ] **Step 1: Failing tests.** `it('shows one button on a row and the other two in a menu')`; `it('still answers O and N on the selected row')`; `it('opens the menu with the keyboard and lists each key')`.
- [ ] **Step 2 to 4:** run, implement, run. The menu button is 44 px tall at 390.
- [ ] **Step 5: Verify and commit** `Needs you: one fix on each row, the rest in a menu`; PR.

### Task 5: Sweep Outbox, Progress, Wins, Done and No-go

**Files:** the five routes' index files, `routes/progress/funnel-ledger.tsx`, `routes/outbox/sending-strip.tsx`.

- [ ] **Step 1: Walk each page cold** (craft step 12) with the gate screenshots and write the friction log in the PR. Fix only what got in the way. Expected small items: Progress tables lose their outer card border and use hairlines (the bar stays, since it is data); Outbox's `Details` menu and the sending strip keep their words; Wins and Done adopt `PageHeader` (done in Task 1) and the empty states are checked against `words.md`.
- [ ] **Step 2: Dark mode pass** on every route; contrast stays covered by `styles/tokens.test.ts` (no new tokens means no new pairs).
- [ ] **Step 3: Commit and PR** `Progress, Outbox, Wins, Done, No-go: header and spacing pass`.

### Task 6: Keep it clean

**Files:** `web/src/copy-rules.test.ts`, `web/e2e/` (one new spec), `docs/design/README.md`.

- [ ] **Step 1:** extend `copy-rules.test.ts` so no source string under `web/src` contains a middle dot or an all-caps label (the existing rule covers dashes). Run it red against any offender, fix, run green.
- [ ] **Step 2:** add `e2e/layout.spec.ts`: for each route at 1280 and 390, no horizontal scroll and every `button` and `a` that is a primary control is at least 44 px tall at 390. This is the mechanical half of craft's gate.
- [ ] **Step 3:** write the "Key hints", "Page header" and "Row actions" rules into `docs/design/README.md` so the next screen starts from them. Commit and PR.

---

## Order and merge rules

One PR at a time, each from fresh `origin/master`: PR #178 (open), then Task 1, 2, 3, 4, 5, 6. Merge order is that order. After each merge confirm `gh pr view <n> --json state,baseRefName` says `MERGED` into `master`. Deploy per `CLAUDE.md` (web changed: build, then kickstart the API job) only after Devanshu merges.

## Self-review

- **Spec coverage:** every audit row maps to a task; every taste line maps to a constraint or a task step. Fit and judge scores were not touched on Today (owner ruling of 2026-10-27) and none are added to Jobs.
- **Type consistency:** `PageHeader`, `JobFilters`, `levelAlreadyInTitle` are defined once and named the same in the tasks that consume them.
- **Open risk:** Task 2 hides the Pass and Go ahead buttons on unselected rows. That is the one change a mouse-first moment could feel as a loss; decision 2 above covers it, and hover keeps them one move away.
