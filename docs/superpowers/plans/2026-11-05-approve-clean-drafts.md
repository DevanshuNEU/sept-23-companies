# Approve clean drafts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One button in the Outbox opens a list of the clean drafts and approves the ones he leaves ticked.

**Architecture:** Web only. The button never approves by itself: it opens a dialog, and the dialog's confirm queues one undoable write in the existing pending-write store. After the 8-second window that write calls the existing `client.approve` once per draft, so the server re-runs the #9 safety checks on every one. No new API route, so `approve_message` stays the only path to `approved`.

**Tech Stack:** React, TanStack Query, the app's pending-write store (`web/src/pending/pending.tsx`), shadcn Dialog and Checkbox, vitest with the `FixtureClient`.

**Spec:** GitHub issue #174 ("Done when") and the design agreed in chat on 2026-11-05. Closes #174.

## Global Constraints

- Approval is his click or key. Only the dialog's confirm button queues the batch. No effect, timer or poll may queue it.
- A draft is clean when: `group === 'review'`, `state === 'drafted'`, `touchState === 'open'`, `blockCount === 0`, `warnCount === 0`, and it has no unsaved edit (`useEdits().get(messageId) === undefined`). Warnings are left out of the batch entirely (Devanshu's call was open; this is the default, change it only if he says so).
- Each approve uses the revision the dialog showed (`item.revision` at open time). A newer saved version is refused by the server and named in the summary.
- No em dashes or en dashes in any string, comment, commit message or PR body. Plain words, sentence case.
- Never call the real API write routes. Tests use `FixtureClient` only. Never use ports 3001, 3002, 3011, 3013 or 3100.
- Branch from current `origin/master` (this workspace's branch already is). One PR, base `master`, body says "Closes #174".
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. The PR body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

## Review Focus

- Claude saves a newer version of a draft between dialog open and confirm: that draft is refused (conflict), the others are approved, the toast names it.
- Undo (button or Z) inside the window: zero `approve` calls, rows back in Drafts to review.
- One draft refused mid-batch must not stop the drafts after it.
- A draft with unsaved typed text is never offered, and the bar count follows him typing in a draft.
- The company filter (`?company=`) is on: only that company's clean drafts are offered.
- No clean drafts: no bar, and Shift+A does nothing.
- The tab closes inside the window: the pagehide flush starts every approve without waiting for the one before it.

---

### Task 1: Rules, copy and the optimistic patch

**Files:**
- Create: `web/src/routes/outbox/approve-batch.ts`
- Create: `web/src/routes/outbox/approve-batch.test.ts`
- Modify: `web/src/routes/outbox/outbox-patches.ts` (add `approveRows`)
- Modify: `web/src/pending/edits.tsx` (add `useEditsVersion`)

**Interfaces:**
- Produces, in `approve-batch.ts`:
  - `cleanDrafts(items: OutboxItem[], hasUnsavedEdit: (id: Id) => boolean): OutboxItem[]` in input order.
  - `heldBack(items: OutboxItem[], hasUnsavedEdit: (id: Id) => boolean): { warnings: number; blocked: number; unsaved: number }` counting open `review` drafts that are not clean. Blocked wins over the other two, then warnings, then unsaved, so each draft counts once.
  - `heldBackText(h: ReturnType<typeof heldBack>): string | null` : `null` when all zero, else for example `2 with warnings, 1 blocked, 1 with unsaved edits stay here for you.` Parts with zero are dropped; singular and plural read right (`1 with a warning`).
  - `batchSummary(drafts: OutboxItem[]): string` : `4 first emails and 7 follow-ups. No blocks, no warnings.` Zero parts are dropped (`7 follow-ups.` alone reads `7 follow-ups. No blocks, no warnings.`), `1 first email`, `1 follow-up`.
  - `approvedText(n: number): string` : `Approved 1 draft.` or `Approved 11 drafts.`
  - `refusedSummary(approved: number, total: number, refused: { name: string; message: string }[]): string` : for one refusal `Approved 10 of 11. Blake Graham was refused: <reason>. It is back in Drafts to review.`; for several `Approved 9 of 11. Refused: Blake Graham (<reason>); Ava Moreno (<reason>). They are back in Drafts to review.`; when `approved === 0` it starts `Nothing was approved.` instead of `Approved 0 of N.` A trailing `Nothing was approved.` or `Nothing was saved.` on a server message is stripped from `<reason>`, and a trailing full stop too, so the sentence does not double up.
- Produces, in `outbox-patches.ts`: `approveRows(view: OutboxView, touchIds: ReadonlySet<Id>): OutboxView` sets `state: 'approved'`, `group: 'approved'` on those rows and returns through `recount`.
- Produces, in `edits.tsx`: `useEditsVersion(): number`, a number that changes whenever any email's unsaved text is set or deleted (`useSyncExternalStore` on the store; add a `version` counter the store bumps in `changed()`).

- [ ] **Step 1: Write the failing tests** in `approve-batch.test.ts`. Build items by taking `buildFixture().outbox.items` and overriding fields. Assertions: `cleanDrafts` keeps a clean first email and a clean follow-up (`seq 2`) and drops each of: `blockCount 1`, `warnCount 1`, `touchState 'closed'`, `group 'paused'`, `state 'approved'`, and an id for which `hasUnsavedEdit` is true. `heldBack` counts one draft with block and warning once, under `blocked`. `heldBackText` and `batchSummary` and `approvedText` and `refusedSummary` return the exact strings in the Interfaces block (one test per bullet, including the stripped `Nothing was approved.` case and the zero-approved case). `approveRows` test: two rows approved, `counts.review` drops by 2 and `counts.approved` rises by 2, other rows untouched.
- [ ] **Step 2: Run** `cd web && pnpm test approve-batch` . Expected: FAIL, module not found.
- [ ] **Step 3: Implement** the functions above in `approve-batch.ts`, `approveRows` in `outbox-patches.ts` (reuse `patchRow`'s `recount`), and `useEditsVersion` in `edits.tsx`.
- [ ] **Step 4: Run** `cd web && pnpm test approve-batch` . Expected: PASS.
- [ ] **Step 5: Commit** `git add web/src && git commit -m "Outbox: rules and copy for approving clean drafts (#174)"`

---

### Task 2: The batch write

**Files:**
- Create: `web/src/routes/outbox/use-approve-batch.ts`
- Create: `web/src/routes/outbox/use-approve-batch.test.tsx`
- Modify: `web/src/routes/outbox/approve-guard.test.ts` (ALLOWED gains `'/src/routes/outbox/use-approve-batch.ts'`; keep the `toContain` for `outbox-detail.tsx`, and add one `expect(callers).toContain` for the new file)

**Interfaces:**
- Consumes: `approvedText`, `refusedSummary` from Task 1; `approveRows`, `fewerDrafts`, `patchDetailItem` from `outbox-patches.ts`; `usePendingWrite`, `Patch` from `@/pending/pending`; `usePageClient('outbox')`; `newRequestId`, `ApiError`, `queryKeys` from `@/api`.
- Produces: `useApproveBatch(): (drafts: OutboxItem[]) => void`. The returned function queues one `PendingWrite` and returns at once. Details:
  - `id: 'approve-batch:' + drafts.map(d => d.messageId).join(',')`, `label: approvedText(n)`, `afterCommit: approvedText(n)`.
  - Patches: `queryKeys.outbox` with `approveRows`, `queryKeys.shell` with `fewerDrafts(s, n)`, and for each draft `queryKeys.outboxDetail(touchId)` with `patchDetailItem(x, { state: 'approved', group: 'approved' })`.
  - `commit(o)`: calls `client.approve(d.messageId, d.revision, { requestId: newRequestId(), keepalive: o.keepalive })` for each draft. Normally one after the other in list order; when `o.keepalive` is true all are started together (`Promise.allSettled`), because the page is going away. A refusal is caught per draft, never stops the loop. After all have settled: if none failed, resolve; if any failed, `throw new ApiError(firstFailure.code, refusedSummary(approved, n, refused))`, where `code` is the first refusal's `ApiError.code` (or `'network'` for a non-ApiError). The pending store then shows that text as an error toast and refetches, which puts refused rows back.

- [ ] **Step 1: Write the failing tests** in `use-approve-batch.test.tsx`, with a small test component that calls the hook from a button, rendered with `renderWithApp` and a `FixtureClient` (copy `startClock`/`settle` from `outbox-detail.test.tsx`). Pick `drafts = cleanDrafts(db.outbox.items, () => false)`. Tests:
  1. `queues one write: the rows move to Approved at once, one Undo toast, no approve call yet`.
  2. `after 8 seconds approves every draft once, at the revision it showed` : spy `client.approve`, assert call count equals `drafts.length` and each call's second argument equals that draft's `revision`.
  3. `Undo drops the whole batch` : click the live Undo, advance 8 seconds, `approve` not called, rows still in Drafts to review.
  4. `Z undoes the batch` : press `z` in the window, same assertions.
  5. `a refused draft does not stop the rest` : make `client.approve` reject with `new ApiError('conflict', 'The draft changed since you loaded it. Nothing was saved.')` for the second draft only; the others still approve; the toast text equals `refusedSummary(n - 1, n, [{ name, message: 'The draft changed since you loaded it.' }])`... (message with the trailing sentence stripped as Task 1 defines).
  6. `a newer saved version is refused and named` : bump that draft's revision in the fixture db before the window ends (use the fixture's own save path or set `item.revision += 1`), assert the toast names the person.
  7. `pagehide inside the window starts every approve without waiting` : dispatch `pagehide`, assert `approve` was called `n` times before any resolves (make the spy return a never-resolving promise for this test).
  Add to `approve-guard.test.ts` the new allowed path.
- [ ] **Step 2: Run** `cd web && pnpm test use-approve-batch approve-guard` . Expected: FAIL.
- [ ] **Step 3: Implement** `useApproveBatch` in `use-approve-batch.ts` as described.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `git add web/src && git commit -m "Outbox: one undoable write approves a batch of drafts (#174)"`

---

### Task 3: The bar, the dialog and the key

**Files:**
- Create: `web/src/routes/outbox/approve-batch-dialog.tsx`
- Modify: `web/src/routes/outbox/outbox-list.tsx` (the `review` section: bar under the group head, dialog state, `shift+a`)
- Test: `web/src/routes/outbox/approve-batch-dialog.test.tsx`

**Interfaces:**
- Consumes: Tasks 1 and 2; `Dialog*` parts from `@/components/ui/dialog`, `Checkbox`, `Button`, `Kbd`; `StatusBadge` is not needed.
- Produces: `ApproveBatchDialog({ drafts, onClose, onConfirm }: { drafts: OutboxItem[] | null; onClose: () => void; onConfirm: (picked: OutboxItem[]) => void })`, open while `drafts !== null`. Purely presentational: it never calls the client.

**Layout and copy (craft: one job, answer first, every string plain):**
- Bar, inside `<section data-group="review">`, under the group head button and not inside it (the head is a `<button>`), rendered only while the group is open and `cleanDrafts(...).length > 0`: left text `N ready to approve` (muted, `tabular-nums`), right `Button` `Approve N` with `<Kbd>⇧A</Kbd>`. Below it, when `heldBackText` is not null, one muted line with that text. Height and borders match the existing sub-group strips (`h-7`, `border-b bg-subtle/60`, `px-3`, `text-xs`).
- Dialog: title `Approve N drafts` (N follows the ticks; `Approve 1 draft`), description from `batchSummary(ticked)`. A `Select all` checkbox row with the count. One row per draft: checkbox, `**Name** Company` on the first line, the subject on the second, and on the right `First email` or `Follow-up 2 of 4` (use `FOLLOWUP_COUNT` from `@/lib/followups`). The list scrolls inside `max-h-[60vh]`. Footer: `Cancel` (ghost) and `Approve N` (primary, disabled at zero ticked and then labelled `Approve`). Default focus on the first row's checkbox, never on the primary button, so a stray Enter does not approve. Every row is a `<label>` so the whole row toggles.
- Confirm calls `onConfirm(ticked)` then `onClose()`. Then `OutboxList` calls `useApproveBatch()(picked)`.
- `OutboxList` opens the dialog with a snapshot taken at that moment: `cleanDrafts(items, (id) => edits.get(id) !== undefined)`. The bar count recomputes on `useEditsVersion()` and on every render of `items`. `shift+a` is registered with `useHotkeys` (`label: 'Review and approve the clean drafts'`, `group: 'Outbox'`, `enabled: clean.length > 0`) and only opens the dialog. After confirm, selection moves on with the existing `advanceRef` logic only if the selected row was in the batch (call `advance(selected)`); otherwise nothing moves.

- [ ] **Step 1: Write the failing tests** in `approve-batch-dialog.test.tsx` using the `outbox()` helper style from `outbox-list.test.tsx` (default scenario; derive `clean` from `db.outbox.items` with `cleanDrafts`). Tests:
  1. `the bar shows the clean count and what was held back` : text `${clean.length} ready to approve` and the `heldBackText` line; Kenji (blocked) and every warned draft are not counted.
  2. `the button opens the dialog and approves nothing` : click `Approve N`, dialog named `Approve N drafts` is open, `client.approve` not called, no Undo toast yet.
  3. `every clean draft is listed and ticked, and focus is not on the primary button`.
  4. `unticking one drops it: the title, the button and the batch follow` : untick the first row, button reads `Approve N-1`, confirm, advance 8 seconds, `approve` called N-1 times and the unticked person is still in Drafts to review.
  5. `Select all unticks and ticks every row; at zero the primary is disabled`.
  6. `Cancel and Esc close it and change nothing`.
  7. `Shift+A opens the dialog; plain A on a selected draft still approves one draft` (the existing behaviour, untouched).
  8. `no clean drafts: no bar, and Shift+A does nothing` (use a db where every review item is set to `blockCount: 1` or approved).
  9. `a draft with unsaved text is not counted` : type into the body of a clean draft (as `typeInBody` in `outbox-detail.test.tsx`), the bar count drops by one and the dialog does not list that person.
  10. `the company filter limits the batch` : route `/outbox?company=<id>`, the bar and the dialog hold only that company's clean drafts.
  11. `confirming moves the rows to Approved at once with one toast and Undo`, and `Z` brings them back.
- [ ] **Step 2: Run** `cd web && pnpm test approve-batch-dialog` . Expected: FAIL.
- [ ] **Step 3: Implement** `ApproveBatchDialog`, then the bar, state, hotkey and `onConfirm` wiring in `outbox-list.tsx`. Reuse existing `Dialog`, `Checkbox` and `Kbd`; no new dependency.
- [ ] **Step 4: Run** the same command, then the whole outbox folder `cd web && pnpm test src/routes/outbox` . Expected: PASS, and the existing `outbox-list.test.tsx` and `outbox-detail.test.tsx` stay green (the new bar must not change `headings()` or row queries).
- [ ] **Step 5: Commit** `git add web/src && git commit -m "Outbox: Approve N bar, review dialog and Shift+A (#174)"`

---

### Task 4: Gate, check in the browser, open the PR

**Files:**
- Modify: `web/src/keyboard` help text only if the shortcuts list is not generated from `useHotkeys` labels (check first; the registry should list `Shift+A` on its own).

- [ ] **Step 1: Run** from `web/`: `pnpm lint && pnpm typecheck && pnpm test && pnpm build`. Expected: all pass.
- [ ] **Step 2: Look at it.** Serve the web app on this workspace's `CONDUCTOR_PORT` against the fixture client (the sample source), open `/outbox`, and take screenshots into `.context/`: the bar, the dialog with all ticked, the dialog with some unticked, the toast with Undo, and the refused-summary toast. Run `python3 ~/.claude/skills/craft/scripts/gate.py <url>` and fix what fails (contrast on the muted bar text, focus ring on the rows, keyboard order, reduced motion).
- [ ] **Step 3: Push and open the PR** with `gh pr create --base master`, body says `Closes #174`, lists the screenshots and the test commands run. Then `gh pr view <n> --json baseRefName,mergeStateStatus` shows base `master` and `CLEAN` before asking Devanshu to merge.
- [ ] **Step 4: After he merges,** deploy per CLAUDE.md "web changed": in the live checkout `git pull --ff-only https://github.com/DevanshuNEU/sept-23-companies.git master`, `cd web && pnpm install --frozen-lockfile && pnpm build`, then `launchctl kickstart -k gui/$(id -u)/com.devanshu.outreach-api`. No Python, schema or launchd change.
