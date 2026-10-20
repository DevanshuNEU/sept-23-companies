import type { Id, OutboxCounts, OutboxGroup, OutboxView } from '@/api';

const GROUPS: OutboxGroup[] = ['replied', 'review', 'approved', 'sending', 'checking', 'sent', 'paused', 'closed'];

/**
 * The counts and the Closed breakdown, read off the rows. Optimistic patches and the company filter
 * call this after they change the rows, so a count is always the number of rows in its group (and a
 * poll that lands inside an undo window gives the same answer twice).
 */
export function recount(view: OutboxView): OutboxView {
  const counts = Object.fromEntries(GROUPS.map((g) => [g, 0])) as unknown as OutboxCounts;
  const closed = { bounced: 0, skipped: 0, companyClosed: 0 };
  for (const i of view.items) {
    counts[i.group] += 1;
    if (i.group !== 'closed') continue;
    if (i.touchState === 'bounced') closed.bounced += 1;
    else if (i.state === 'skipped') closed.skipped += 1;
    else closed.companyClosed += 1;
  }
  return { ...view, counts, closedBreakdown: closed };
}

/** Only one company's rows, with the counts to match. */
export function forCompany(view: OutboxView, companyId: Id): OutboxView {
  return recount({ ...view, items: view.items.filter((i) => i.company.id === companyId) });
}
