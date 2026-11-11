import { useMemo, useState } from 'react';
import { ClipboardCheck, SearchX } from 'lucide-react';
import { Link } from 'react-router';
import { pollMs, queryKeys, usePageClient, type AppliedRow, type AppliedView } from '@/api';
import { PageHeader } from '@/components/page-header';
import { EmptyState, QueryState } from '@/components/state-views';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { useListNav } from '@/keyboard/list-nav';
import { openExternal, safeHref } from '@/lib/safe-href';
import { formatDateMonthDay } from '@/lib/short-dates';
import { cn } from '@/lib/utils';
import { useViewQuery } from '@/pending/pending';

/** The job link, drawn as on Jobs: it opens the posting in a new tab. */
const LINK =
  'rounded-sm underline decoration-foreground/30 underline-offset-3 outline-none hover:decoration-foreground focus-visible:ring-[3px] focus-visible:ring-ring/70';
const plural = (n: number) => `${n.toLocaleString('en-US')} ${n === 1 ? 'job' : 'jobs'}`;

/**
 * Applied (#218): every posting he marked applied on Jobs, newest first. Each row has the role (it opens the job link
 * in a new tab), the company (its page), the day he applied and whether the posting is still open. A search narrows by
 * company or role, so "did I apply there?" is one look. The arrows move through the rows and Enter opens the job.
 */
export default function AppliedPage() {
  const client = usePageClient('plan');
  const query = useViewQuery(queryKeys.applied, () => client.applied(), { pollMs: pollMs.default });
  return (
    <div className="max-w-[1360px] px-6 pt-5 pb-12">
      <QueryState query={query} skeleton={<Loading />} empty={null}>
        {(view) => <AppliedBody view={view} />}
      </QueryState>
    </div>
  );
}

function AppliedBody({ view }: { view: AppliedView }) {
  const [text, setText] = useState('');
  const q = text.trim().toLowerCase();
  const rows = useMemo(
    () => (q === '' ? view.rows : view.rows.filter((r) => r.companyName.toLowerCase().includes(q) || r.title.toLowerCase().includes(q))),
    [view.rows, q],
  );
  return (
    <>
      <PageHeader
        className="mb-4"
        title="Applied jobs"
        summary={view.rows.length === 0 ? 'The jobs you applied to online.' : `${plural(view.rows.length)} you applied to, newest first.`}
      />
      {view.rows.length === 0 ? (
        <EmptyState
          icon={ClipboardCheck}
          title="No applications yet."
          action={
            <Button asChild variant="outline" size="sm">
              <Link to="/browse">Open Jobs</Link>
            </Button>
          }
        >
          Press Mark applied on a job and it stays here.
        </EmptyState>
      ) : (
        <>
          <Input
            type="search"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Search by company or role"
            aria-label="Search by company or role"
            autoComplete="off"
            className="mb-3 h-8 w-72 max-sm:h-11 max-sm:w-full"
          />
          {rows.length === 0 ? (
            <EmptyState
              icon={SearchX}
              title="No applications match your search."
              action={
                <Button type="button" variant="outline" size="sm" onClick={() => setText('')}>
                  Clear search
                </Button>
              }
            />
          ) : (
            <AppliedTable rows={rows} />
          )}
        </>
      )}
    </>
  );
}

function AppliedTable({ rows }: { rows: AppliedRow[] }) {
  const byId = new Map(rows.map((r) => [r.postingId, r]));
  const { rowProps } = useListNav({
    ids: rows.map((r) => r.postingId),
    onOpen: (id) => {
      const url = byId.get(id)?.url;
      if (url) openExternal(url);
    },
    announce: (id) => {
      const r = byId.get(id);
      return r ? `${r.title} at ${r.companyName}` : '';
    },
  });
  const head = 'h-9 bg-subtle px-3 text-muted-foreground';
  const cell = 'px-3 py-1.5 text-[13px] whitespace-normal';
  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      <Table className="text-[13px]">
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className={head}>Role</TableHead>
            <TableHead className={head}>Company</TableHead>
            <TableHead className={head}>Applied</TableHead>
            <TableHead className={head}>Posting</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => {
            const props = rowProps(r.postingId);
            const href = safeHref(r.url);
            return (
              <TableRow
                key={r.postingId}
                {...props}
                className={cn('h-9 cursor-default', props['data-active'] && 'bg-muted shadow-[inset_2px_0_0_var(--foreground)] hover:bg-muted')}
              >
                <TableCell className={cn(cell, 'font-medium')}>
                  {href ? (
                    <a href={href} target="_blank" rel="noopener noreferrer" className={LINK} onClick={(e) => e.stopPropagation()}>
                      {r.title}
                    </a>
                  ) : (
                    r.title
                  )}
                </TableCell>
                <TableCell className={cell}>
                  <Link to={`/companies/${r.companyId}`} className="hover:underline" onClick={(e) => e.stopPropagation()}>
                    {r.companyName}
                  </Link>
                </TableCell>
                <TableCell className={cn(cell, 'text-muted-foreground tabular-nums')}>{formatDateMonthDay(r.appliedOn)}</TableCell>
                <TableCell className={cn(cell, !r.open && 'text-muted-foreground')}>{r.open ? 'Open' : 'Closed'}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function Loading() {
  return (
    <div className="flex flex-col gap-3" aria-hidden>
      <Skeleton className="h-7 w-48" />
      <Skeleton className="h-8 w-72" />
      <Skeleton className="h-64 w-full rounded-lg" />
    </div>
  );
}
