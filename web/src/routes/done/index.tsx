import type { ReactNode } from 'react';
import { CircleCheck } from 'lucide-react';
import { Link, useNavigate } from 'react-router';
import { pollMs, queryKeys, usePageClient, type DoneCompany, type DoneView } from '@/api';
import { EmptyState, QueryState } from '@/components/state-views';
import { StatusBadge } from '@/components/status-badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { useListNav } from '@/keyboard/list-nav';
import { formatDay, formatMonth } from '@/lib/format';
import { MIN_SAMPLE } from '@/lib/progress';
import { cn } from '@/lib/utils';
import { useViewQuery } from '@/pending/pending';
import { RepliesChart } from './replies-chart';

export default function DonePage() {
  const client = usePageClient('done');
  const query = useViewQuery(queryKeys.done, () => client.done(), { pollMs: pollMs.default });
  return (
    <div className="max-w-[1360px] px-6 pt-5 pb-12">
      <div className="mb-4">
        <h1 className="text-xl font-semibold tracking-tight">Done companies</h1>
        <p className="mt-0.5 text-[13px] text-muted-foreground">
          Closed with an interview or a referral. They never come back to Today, and nothing is sent there.
        </p>
      </div>
      <QueryState query={query} skeleton={<DoneSkeleton />} empty={null}>
        {(view) => <DoneBody view={view} />}
      </QueryState>
    </div>
  );
}

function DoneBody({ view }: { view: DoneView }) {
  return (
    <>
      <Metrics view={view} />
      {view.companies.length === 0 ? (
        <EmptyState icon={CircleCheck} title="Nothing done yet.">
          A company lands here when you close it with Referral or Interview.
        </EmptyState>
      ) : (
        <>
          <DoneTable companies={view.companies} />
          <p className="mt-2 text-xs text-muted-foreground">
            Reopen from the company page if a process ends: it goes back to the backlog with its full history.
          </p>
        </>
      )}
    </>
  );
}

function Metric({ label, value, sub, children }: { label: string; value: string; sub?: string; children?: ReactNode }) {
  return (
    <div role="group" aria-label={label} className="grid content-start gap-1 border-r px-4 py-3.5 last:border-r-0">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-2xl leading-8 font-semibold tracking-[-0.015em]">{value}</span>
      {children}
      {sub ? <span className="text-xs text-muted-foreground">{sub}</span> : null}
    </div>
  );
}

/** "6.4%": one decimal, trailing zero dropped. */
function percent(part: number, whole: number): string {
  return `${Number(((part / whole) * 100).toFixed(1))}%`;
}

/**
 * The reply rate, honest (design 16.3): a percent only from `MIN_SAMPLE` first emails up. Below it the big number is
 * the plain count ("9 of 20"), the line beneath says it in words, and nothing is red for a small sample.
 */
function replyRate({ replies, firstEmails }: DoneView['metrics']['replyRate']) {
  if (firstEmails <= 0) return { value: 'None yet', sub: 'No first emails this month', note: null };
  const words = `${replies} ${replies === 1 ? 'reply' : 'replies'} from ${firstEmails} first emails this month`;
  if (firstEmails < MIN_SAMPLE) return { value: `${replies} of ${firstEmails}`, sub: words, note: 'Too few first emails for a percent yet.' };
  return { value: percent(replies, firstEmails), sub: words, note: null };
}

function Metrics({ view }: { view: DoneView }) {
  const { metrics } = view;
  const rate = replyRate(metrics.replyRate);
  return (
    <div data-slot="done-strip" className="mb-5 grid grid-cols-4 overflow-hidden rounded-md border bg-card">
      <Metric label={`Replies in ${formatMonth(view.asOf)}`} value={String(metrics.repliesThisMonth)} sub={`Per week, last ${metrics.repliesPerWeek.length} weeks`}>
        <RepliesChart weeks={metrics.repliesPerWeek} />
      </Metric>
      <Metric label="Reply rate" value={rate.value} sub={rate.sub}>
        {rate.note ? <span className="text-xs text-muted-foreground">{rate.note}</span> : null}
      </Metric>
      <Metric label="Referrals" value={String(metrics.referrals)} />
      <Metric label="Interviews" value={String(metrics.interviews)} />
    </div>
  );
}

function DoneTable({ companies }: { companies: DoneCompany[] }) {
  const navigate = useNavigate();
  const open = (id: number) => void navigate(`/companies/${id}`);
  const { rowProps } = useListNav({
    ids: companies.map((d) => d.company.id),
    onOpen: open,
    announce: (id) => companies.find((d) => d.company.id === id)?.company.name ?? '',
  });
  const head = 'h-9 bg-subtle px-3 text-muted-foreground';
  const cell = 'px-3 py-1.5 text-[13px] whitespace-normal';
  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      <Table className="text-[13px]">
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className={head}>Company</TableHead>
            <TableHead className={head}>Outcome</TableHead>
            <TableHead className={head}>How it started</TableHead>
            <TableHead className={head}>Role</TableHead>
            <TableHead className={head}>Closed</TableHead>
            <TableHead className={head}>Note</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {companies.map((d) => {
            const props = rowProps(d.company.id);
            return (
              <TableRow
                key={d.company.id}
                {...props}
                className={cn('h-9 cursor-default', props['data-active'] && 'bg-muted shadow-[inset_2px_0_0_var(--foreground)] hover:bg-muted')}
              >
                <TableCell className={cn(cell, 'font-medium')}>
                  <Link to={`/companies/${d.company.id}`} className="hover:underline" onClick={(e) => e.stopPropagation()}>
                    {d.company.name}
                  </Link>
                </TableCell>
                <TableCell className={cell}>
                  <StatusBadge status={{ label: d.outcomeLabel, tone: 'ok' }} dot />
                </TableCell>
                <TableCell className={cell}>
                  <span className="inline-flex items-center gap-1.5">
                    {d.howStarted}
                    {d.imported ? (
                      <span className="rounded px-1.5 py-px text-[11px] font-medium text-muted-foreground bg-muted">Imported</span>
                    ) : null}
                  </span>
                </TableCell>
                <TableCell className={cn(cell, d.role === null && 'text-muted-foreground')}>{d.role ?? 'Not recorded'}</TableCell>
                <TableCell className={cn(cell, 'text-muted-foreground tabular-nums')}>{formatDay(d.closedAt)}</TableCell>
                <TableCell className={cn(cell, d.note === '' && 'text-muted-foreground')}>{d.note === '' ? 'No note' : d.note}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function DoneSkeleton() {
  return (
    <div className="flex flex-col gap-5" aria-hidden>
      <Skeleton className="h-28 w-full rounded-md" />
      <Skeleton className="h-48 w-full rounded-lg" />
    </div>
  );
}
