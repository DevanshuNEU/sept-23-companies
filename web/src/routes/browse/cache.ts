import type { InfiniteData, QueryClient } from '@tanstack/react-query';
import { queryKeys, type Id, type IsoDate, type JobCounts, type JobsTab, type JobsView } from '@/api';

/*
 * What a write the server has taken changes in the job list already on the page. The list takes seconds to read on the
 * live store and can answer 503 busy, so the page must not wait for that read to show what he did: the pending write's
 * overlay goes when the commit settles, and the list on screen was read before the write.
 */

type Pages = InfiniteData<JobsView, string | null>;
const COUNT_KEY: Record<JobsTab, keyof JobCounts> = { your_level: 'yourLevel', higher: 'higher', other: 'other' };

function eachList(queryClient: QueryClient, edit: (data: Pages, tab: JobsTab | undefined) => Pages | undefined): void {
  for (const [key, data] of queryClient.getQueriesData<Pages>({ queryKey: queryKeys.jobsAll })) {
    if (!data?.pages) continue;
    const tab = (key[1] as { view?: JobsTab } | undefined)?.view;
    const next = edit(data, tab);
    if (next !== undefined) queryClient.setQueryData<Pages>(key, next);
  }
}

/** Pass or Applied went through: the posting leaves every list that holds it, and that list's numbers drop by one. */
export function dropPosting(queryClient: QueryClient, postingId: Id): void {
  eachList(queryClient, (data, tab) => {
    if (!data.pages.some((p) => p.rows.some((r) => r.postingId === postingId))) return undefined;
    const counted = tab === undefined ? undefined : COUNT_KEY[tab];
    return {
      ...data,
      pages: data.pages.map((p) => ({
        ...p,
        rows: p.rows.filter((r) => r.postingId !== postingId),
        total: Math.max(p.total - 1, 0),
        counts: counted === undefined ? p.counts : { ...p.counts, [counted]: Math.max(p.counts[counted] - 1, 0) },
      })),
    };
  });
}

/** Reached went through: every row of the company says reached on that day (a later day it already has stays). */
export function markCompanyReached(queryClient: QueryClient, companyId: Id, on: IsoDate): void {
  eachList(queryClient, (data) => {
    if (!data.pages.some((p) => p.rows.some((r) => r.companyId === companyId))) return undefined;
    return {
      ...data,
      pages: data.pages.map((p) => ({
        ...p,
        rows: p.rows.map((r) => (r.companyId === companyId && (r.contactedOn === null || r.contactedOn < on) ? { ...r, contactedOn: on } : r)),
      })),
    };
  });
}
