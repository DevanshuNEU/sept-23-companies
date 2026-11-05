import type { Id, OutboxItem } from '@/api';

type HasUnsaved = (messageId: Id) => boolean;

/** A draft he can still approve from the list: open, in Drafts to review. */
const isOpenDraft = (i: OutboxItem) => i.group === 'review' && i.state === 'drafted' && i.touchState === 'open';

/** What the batch may approve: an open draft with no block, no warning and no unsaved text. Order is kept. */
export function cleanDrafts(items: OutboxItem[], hasUnsavedEdit: HasUnsaved): OutboxItem[] {
  return items.filter((i) => isOpenDraft(i) && i.blockCount === 0 && i.warnCount === 0 && !hasUnsavedEdit(i.messageId));
}

export interface HeldBack {
  warnings: number;
  blocked: number;
  unsaved: number;
}

/** The open drafts the batch leaves out, each counted once: a block first, then a warning, then unsaved text. */
export function heldBack(items: OutboxItem[], hasUnsavedEdit: HasUnsaved): HeldBack {
  const held: HeldBack = { warnings: 0, blocked: 0, unsaved: 0 };
  for (const i of items) {
    if (!isOpenDraft(i)) continue;
    if (i.blockCount > 0) held.blocked += 1;
    else if (i.warnCount > 0) held.warnings += 1;
    else if (hasUnsavedEdit(i.messageId)) held.unsaved += 1;
  }
  return held;
}

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

export function heldBackText(h: HeldBack): string | null {
  const parts = [
    h.warnings > 0 ? `${h.warnings} with ${plural(h.warnings, 'a warning', 'warnings')}` : null,
    h.blocked > 0 ? `${h.blocked} blocked` : null,
    h.unsaved > 0 ? `${h.unsaved} with unsaved edits` : null,
  ].filter((p): p is string => p !== null);
  if (parts.length === 0) return null;
  const total = h.warnings + h.blocked + h.unsaved;
  return `${parts.join(', ')} ${plural(total, 'stays', 'stay')} here for you.`;
}

/** "4 first emails and 7 follow-ups. No blocks, no warnings." A part with none is left out. */
export function batchSummary(drafts: OutboxItem[]): string {
  const followUps = drafts.filter((d) => d.seq > 0).length;
  const firsts = drafts.length - followUps;
  const parts = [
    firsts > 0 ? `${firsts} ${plural(firsts, 'first email', 'first emails')}` : null,
    followUps > 0 ? `${followUps} ${plural(followUps, 'follow-up', 'follow-ups')}` : null,
  ].filter((p): p is string => p !== null);
  return `${parts.join(' and ')}. No blocks, no warnings.`;
}

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
