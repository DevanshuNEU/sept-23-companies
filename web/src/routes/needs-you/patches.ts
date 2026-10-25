import type { NeedsYouView, ResolvedReview, ReviewItem } from '@/api';

export type Settling = Pick<ResolvedReview, 'resolution' | 'note' | 'locked'>;

/**
 * A fix as the page shows it before the server answers: the item leaves `open`, today's settled count rises and
 * Resolved shows the settling in the API's words until the refetch brings the stored row.
 *
 * The pending store lays the patch over every result until its refetch lands, the refetched one included. Data
 * where the item has already left `open` holds the settling (the server's row, with its Undo and revision, or this
 * patch's own result), so it comes back unchanged: nothing is counted twice and the real row is never replaced.
 */
export function settleView(d: NeedsYouView, r: ReviewItem, settled: Settling): NeedsYouView {
  if (!d.open.some((x) => x.id === r.id)) return d;
  return {
    ...d,
    open: d.open.filter((x) => x.id !== r.id),
    settledToday: d.settledToday + 1,
    resolvedThisWeek: [
      { id: r.id, kind: r.kind, companyName: r.company.name, at: d.asOf, revision: r.revision + 1, canUndo: false, ...settled },
      ...d.resolvedThisWeek.filter((x) => x.id !== r.id),
    ],
  };
}
