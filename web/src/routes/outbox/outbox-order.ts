import type { Id, OutboxGroup, OutboxItem } from '@/api';

/** A draft a check refuses to let out: it sits under "Blocked, fix first", after the drafts he can approve. */
export const isBlocked = (i: OutboxItem) => i.group === 'review' && i.state === 'drafted' && i.blockCount > 0;

export type Slot = OutboxGroup | 'blocked';
export const slotOf = (i: OutboxItem): Slot => (isBlocked(i) ? 'blocked' : i.group);
export const byKey = (a: OutboxItem, b: OutboxItem) => (a.sortKey < b.sortKey ? -1 : a.sortKey > b.sortKey ? 1 : 0);

/**
 * The row to open on arrival: the first reply (it holds a company), else the first draft he can approve.
 * Null when there is neither: a blocked draft is never preselected, it is found under "Blocked, fix first".
 */
export function firstToAct(items: OutboxItem[]): Id | null {
  const sorted = [...items].sort(byKey);
  const reply = sorted.find((i) => i.group === 'replied');
  if (reply) return reply.touchId;
  return sorted.find((i) => i.group === 'review' && i.state === 'drafted' && i.touchState === 'open' && i.blockCount === 0)?.touchId ?? null;
}
