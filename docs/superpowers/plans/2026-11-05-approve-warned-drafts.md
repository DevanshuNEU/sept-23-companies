# Approve warned drafts

Follow-up to `2026-11-05-approve-clean-drafts.md`. Closes #176.

The first version offered only drafts with no warning. Live, all 14 drafts had one, so the bar hid itself and looked broken. A warning never blocks an approve (#9), so the dialog now lists every open draft with no block and no unsaved text.

**Rules** (`web/src/routes/outbox/approve-batch.ts`): `approvableDrafts` (clean first, then warned), `cleanDrafts` (ticked at open), `heldBack` (blocked and unsaved only), `splitText` (bar line), `batchSummary` (names the warned ones).

**UI:** the bar counts approvable drafts and its button reads `Review N`. The dialog ticks clean drafts, leaves warned ones unticked with their warning text, and groups them under "No warnings" and "With warnings". Select all ticks every one.

**Unchanged:** one undoable write, Undo and Z, Skip and Withdraw taking one draft out, the server refusing any block, no new API route.
