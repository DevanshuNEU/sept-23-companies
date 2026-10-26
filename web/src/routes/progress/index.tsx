import { ChartColumn } from 'lucide-react';
import { Link } from 'react-router';
import { pollMs, queryKeys, usePageClient, type ProgressPeriod, type ProgressView } from '@/api';
import { EmptyState, QueryState } from '@/components/state-views';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useHotkeys } from '@/keyboard/hotkeys';
import { formatWeekday } from '@/lib/format';
import { cn } from '@/lib/utils';
import { useUrlState } from '@/lib/url-state';
import { useViewQuery } from '@/pending/pending';
import { Findings } from './findings';
import { EffortList, FunnelLedger } from './funnel-ledger';
import { periodHeading } from './period-label';
import { QuietWeek } from './quiet-week';

type Period = 'week' | 'month';

const link =
  'rounded-sm text-foreground underline decoration-border underline-offset-3 outline-none hover:decoration-current focus-visible:ring-[3px] focus-visible:ring-ring/50';

/** Nothing counted in the period: no stage, no effort. */
const nothingLogged = (p: ProgressPeriod) => p.stages.every((s) => s.count === 0) && p.effort.every((e) => e.count === 0);

/** Today against the plan: people done of the plan, then what he logged. A plan of nothing says so, never "of 0". */
function TodayLedger({ today }: { today: ProgressView['today'] }) {
  const grey = (n: number) => cn('h-10 px-3 py-2', n === 0 && 'text-muted-foreground');
  return (
    <div className="max-w-[720px] overflow-hidden rounded-lg border">
      <table aria-label="Today" className="w-full text-[13px] leading-[18px] tabular-nums">
        <thead>
          <tr className="border-b text-xs text-muted-foreground">
            {['People done', 'Requests sent', 'Applications sent', 'Accepted', 'Replied'].map((h) => (
              <th key={h} scope="col" className="h-8 px-3 text-left font-medium whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr>
            <td className={grey(today.done)}>
              {today.planned > 0 ? (
                <>
                  <b className="font-semibold">{today.done}</b> <span className="text-muted-foreground">of {today.planned} on the plan</span>
                </>
              ) : (
                <span className="text-muted-foreground">No plan today</span>
              )}
            </td>
            <td className={grey(today.requests)}>{today.requests}</td>
            <td className={grey(today.applications)}>{today.applications}</td>
            <td className={grey(today.accepted)}>{today.accepted}</td>
            <td className={grey(today.replied)}>{today.replied}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function PageSkeleton() {
  return (
    <div aria-hidden className="grid gap-3">
      <Skeleton className="h-16 w-full max-w-[720px]" />
      <Skeleton className="h-56 w-full max-w-[760px]" />
    </div>
  );
}

/**
 * Progress (design 16.3): today against the plan, then the week or month as a ledger with the work done beside it.
 * Counts always sit beside their denominators; the API says whether a period has enough requests for a rate.
 */
export default function ProgressPage() {
  const client = usePageClient('progress');
  const query = useViewQuery(queryKeys.progress, () => client.progress(), { pollMs: pollMs.default });
  const [raw, setRaw] = useUrlState('period', 'week');
  const period: Period = raw === 'month' ? 'month' : 'week';

  useHotkeys([
    { keys: '1', label: 'Week', group: 'Progress', run: () => setRaw('week') },
    { keys: '2', label: 'Month', group: 'Progress', run: () => setRaw('month') },
  ]);

  return (
    <div className="max-w-[1120px] px-6 pt-5 pb-12">
      <div className="mb-4">
        <h1 className="text-xl font-semibold tracking-tight">Progress</h1>
        {query.data ? (
          <p className="mt-0.5 text-[13px] text-muted-foreground">
            What you logged, counted plainly. A rate shows once a period has {query.data.base.minRequests} requests.
          </p>
        ) : null}
      </div>
      <QueryState query={query} skeleton={<PageSkeleton />} empty={null}>
        {(v) => {
          const current = period === 'month' ? v.month : v.week;
          return (
            <div className="grid gap-7">
              <section aria-labelledby="today-h" className="grid gap-2">
                <h2 id="today-h" className="text-sm font-semibold">{`Today, ${formatWeekday(v.asOf)}`}</h2>
                <TodayLedger today={v.today} />
              </section>

              <Tabs value={period} onValueChange={(next) => setRaw(next)} className="gap-3">
                <div className="flex max-w-[760px] flex-wrap items-center justify-between gap-3">
                  <h2 className="text-sm font-semibold">{periodHeading(current)}</h2>
                  <TabsList aria-label="Period">
                    <TabsTrigger value="week" className="px-2.5" aria-keyshortcuts="1">
                      Week
                    </TabsTrigger>
                    <TabsTrigger value="month" className="px-2.5" aria-keyshortcuts="2">
                      Month
                    </TabsTrigger>
                  </TabsList>
                </div>
                <TabsContent value={period}>
                  {current.quiet && !nothingLogged(current) ? (
                    <div className="max-w-[760px]">
                      <QuietWeek period={current} base={v.base} noun={period} />
                    </div>
                  ) : nothingLogged(current) ? (
                    <div className="max-w-[760px] rounded-lg border">
                      <EmptyState icon={ChartColumn} title="Nothing logged yet.">
                        Log a request on Today and it counts here.
                      </EmptyState>
                    </div>
                  ) : (
                    <div className="grid items-start gap-8 lg:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
                      <FunnelLedger period={current} minRequests={v.base.minRequests} />
                      <section aria-labelledby="effort-h" className="grid gap-2">
                        <h2 id="effort-h" className="text-sm font-semibold">{`Your work this ${period}`}</h2>
                        <EffortList period={current} label={`Your work this ${period}`} />
                        <p className="mt-3 text-sm">
                          <Link to="/wins" className={link}>
                            Open the win log
                          </Link>{' '}
                          <span className="text-muted-foreground">for every referral, interview and interested reply.</span>
                        </p>
                      </section>
                    </div>
                  )}
                </TabsContent>
              </Tabs>

              <Findings />
            </div>
          );
        }}
      </QueryState>
    </div>
  );
}
