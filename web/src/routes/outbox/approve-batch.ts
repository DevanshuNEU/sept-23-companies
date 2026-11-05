import type { Id, OutboxItem } from '@/api';

type HasUnsaved = (messageId: Id) => boolean;

/** A draft he can still approve from the list: open, in Drafts to review. */
const isOpenDraft = (i: OutboxItem) => i.group === 'review' && i.state === 'drafted' && i.touchState === 'open';

/** An open draft with no block, no warning and no unsaved text: ticked when the dialog opens. */
export function cleanDrafts(items: OutboxItem[], hasUnsavedEdit: HasUnsaved): OutboxItem[] {
  return items.filter((i) => isOpenDraft(i) && i.blockCount === 0 && i.warnCount === 0 && !hasUnsavedEdit(i.messageId));
}

/**
 * Every draft the dialog may list: open, no block, no unsaved text. Clean drafts come first, then drafts with warnings,
 * each in list order. A warning never blocks an approve (#9), so he may tick them; the server still refuses a block.
 */
export function approvableDrafts(items: OutboxItem[], hasUnsavedEdit: HasUnsaved): OutboxItem[] {
  const ok = items.filter((i) => isOpenDraft(i) && i.blockCount === 0 && !hasUnsavedEdit(i.messageId));
  return [...ok.filter((i) => i.warnCount === 0), ...ok.filter((i) => i.warnCount > 0)];
}

export interface HeldBack {
  blocked: number;
  unsaved: number;
}

/** The open drafts the dialog leaves out, each counted once: a block first, then unsaved text. */
export function heldBack(items: OutboxItem[], hasUnsavedEdit: HasUnsaved): HeldBack {
  const held: HeldBack = { blocked: 0, unsaved: 0 };
  for (const i of items) {
    if (!isOpenDraft(i)) continue;
    if (i.blockCount > 0) held.blocked += 1;
    else if (hasUnsavedEdit(i.messageId)) held.unsaved += 1;
  }
  return held;
}

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

export function heldBackText(h: HeldBack): string | null {
  const parts = [h.blocked > 0 ? `${h.blocked} blocked` : null, h.unsaved > 0 ? `${h.unsaved} with unsaved edits` : null].filter(
    (p): p is string => p !== null,
  );
  if (parts.length === 0) return null;
  return `${parts.join(', ')} ${plural(h.blocked + h.unsaved, 'stays', 'stay')} here for you.`;
}

/** The bar's line under the count: how the drafts split. Null when none has a warning. */
export function splitText(clean: number, warned: number): string | null {
  if (warned === 0) return null;
  if (clean === 0) return warned === 1 ? 'It has a warning.' : `All ${warned} have warnings.`;
  return `${clean} clean, ${warned} with ${plural(warned, 'a warning', 'warnings')}.`;
}

/** "4 first emails and 7 follow-ups. No blocks, no warnings." A part with none is left out; warnings are named. */
export function batchSummary(drafts: OutboxItem[]): string {
  const followUps = drafts.filter((d) => d.seq > 0).length;
  const firsts = drafts.length - followUps;
  const warned = drafts.filter((d) => d.warnCount > 0).length;
  const parts = [
    firsts > 0 ? `${firsts} ${plural(firsts, 'first email', 'first emails')}` : null,
    followUps > 0 ? `${followUps} ${plural(followUps, 'follow-up', 'follow-ups')}` : null,
  ].filter((p): p is string => p !== null);
  const tail = warned === 0 ? 'No blocks, no warnings.' : `${warned} ${plural(warned, 'has a warning', 'have warnings')}. No blocks.`;
  return `${parts.join(' and ')}. ${tail}`;
}

/** The dialog's title; it follows the ticks. */
export const approveTitle = (n: number): string => (n === 0 ? 'Approve drafts' : `Approve ${n} ${plural(n, 'draft', 'drafts')}`);

export const approvedText = (n: number): string => `Approved ${n} ${plural(n, 'draft', 'drafts')}.`;

/** The server's own closing sentence says "Nothing was approved", which is wrong for one draft in a batch. */
const reason = (message: string) =>
  message
    .replace(/\s*Nothing was (approved|saved)\.\s*$/, '')
    .replace(/\.\s*$/, '');

export function refusedSummary(approved: number, total: number, refused: { name: string; message: string }[]): string {
  const lead = approved === 0 ? 'Nothing was approved.' : `Approved ${approved} of ${total}.`;
  const [only] = refused;
  if (refused.length === 1 && only) return `${lead} ${only.name} was refused: ${reason(only.message)}. It is back in Drafts to review.`;
  const list = refused.map((r) => `${r.name} (${reason(r.message)})`).join('; ');
  return `${lead} Refused: ${list}. They are back in Drafts to review.`;
}
