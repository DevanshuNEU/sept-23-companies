import type { CompanyRef, Id, MessageState, OutboxCounts, OutboxDetail, OutboxGroup, OutboxItem, OutboxView, ShellSummary } from '@/api';

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

/** The same view with `change` applied to the one row for `touchId`, and the counts to match. */
export function patchRow(view: OutboxView, touchId: Id, change: (i: OutboxItem) => OutboxItem): OutboxView {
  return recount({ ...view, items: view.items.map((i) => (i.touchId === touchId ? change(i) : i)) });
}

/** Every row of one company gets `change` applied to its company. */
export function patchCompany(view: OutboxView, companyId: Id, change: Partial<CompanyRef>): OutboxView {
  return { ...view, items: view.items.map((i) => (i.company.id === companyId ? { ...i, company: { ...i.company, ...change } } : i)) };
}

/** The same change on the row inside a detail response. */
export function patchDetailItem(d: OutboxDetail, change: Partial<OutboxItem>): OutboxDetail {
  return { ...d, item: { ...d.item, ...change } };
}

const OPEN_STATES: MessageState[] = ['drafted', 'approved', 'planned'];

/** True for a row a company close stops: an open touch with a draft or an approved email waiting. */
export const isQueuedFor = (i: OutboxItem, companyId: Id) =>
  i.company.id === companyId && i.touchState === 'open' && OPEN_STATES.includes(i.state);

/** The row as a closed company leaves it. */
export const closedRow = (i: OutboxItem, summary: string): OutboxItem => ({ ...i, touchState: 'closed', group: 'closed', schedule: null, nextDue: null, summary });

/** A closed company stops its queued emails: every open draft or approved row moves to Closed. */
export function closeCompanyRows(view: OutboxView, companyId: Id, summary: string): OutboxView {
  return recount({
    ...view,
    items: view.items.map((i) => ({ ...(isQueuedFor(i, companyId) ? closedRow(i, summary) : i), ...(i.company.id === companyId ? { company: { ...i.company, status: 'closed' as const } } : {}) })),
  });
}

/** The sidebar's "drafts to review" count, `n` lower. */
export function fewerDrafts(s: ShellSummary, n = 1): ShellSummary {
  return { ...s, draftsToReview: Math.max(0, s.draftsToReview - n) };
}
