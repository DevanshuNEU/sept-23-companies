import { useSearchParams } from 'react-router';
import { CircleCheck, Hourglass, Radar } from 'lucide-react';
import { pollMs, queryKeys, usePageClient } from '@/api';
import { EmptyState } from '@/components/state-views';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useViewQuery } from '@/pending/pending';
import { WinStrip } from './win-strip';
import { InFlightList } from './in-flight';
import { OutcomePrompt } from './last-week';
import { WatchList } from './watch-list';

const TABS = ['last-week', 'in-flight', 'watch'] as const;
type Tab = (typeof TABS)[number];

const plural = (n: number, one: string, many: string) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;

function Count({ n }: { n: number | undefined }) {
  return n === undefined ? null : <span className="text-xs font-normal text-muted-foreground tabular-nums">{n.toLocaleString('en-US')}</span>;
}

/**
 * Pipeline (2026-11-06): Last week is the 7-day question, In flight is every company with a wave out or a reply, and
 * the Watch list is what changed on the outside. One tab at a time, with each count on its tab, and the tab is in the
 * address (`?tab=`) so a link opens it. It opens on the first tab with something in it. Email today went with Today
 * (#195), so an old `?tab=email-today` link opens like `/pipeline`.
 */
export default function PipelinePage() {
  const client = usePageClient('plan');
  const [params, setParams] = useSearchParams();
  const plan = useViewQuery(queryKeys.plan, () => client.plan(), { pollMs: pollMs.default });
  const outcomes = useViewQuery(queryKeys.outcomePrompt, () => client.outcomePrompt(), { pollMs: pollMs.default });
  const watch = useViewQuery(queryKeys.watch, () => client.watchList(), { pollMs: pollMs.default });

  const counts: Record<Tab, number | undefined> = {
    'last-week': outcomes.data?.touches.length,
    'in-flight': plan.data?.inFlight.length,
    watch: watch.data?.items.length,
  };
  const loaded = TABS.every((t) => counts[t] !== undefined);
  const asked = params.get('tab');
  const firstFull = TABS.find((t) => (counts[t] ?? 0) > 0) ?? 'in-flight';
  // Until every count is in, the first tab stands in, so the selected tab does not jump about while the page loads.
  const tab: Tab = TABS.includes(asked as Tab) ? (asked as Tab) : loaded ? firstFull : 'last-week';

  const waiting = counts['last-week'] ?? 0;
  const flying = counts['in-flight'] ?? 0;
  const summary = loaded
    ? [waiting > 0 ? `${plural(waiting, 'question', 'questions')} to answer` : 'No questions waiting', plural(flying, 'company', 'companies') + ' in flight'].join(', ')
    : undefined;

  return (
    <div>
      {/* A win arrives here too: answering Accepted is where it happens now, so the moment shows where the answer is given. */}
      <WinStrip />
      <div className="max-w-[820px] px-6 pt-5 pb-12">
      <header className="mb-5 grid gap-0.5">
        <h1 className="text-xl leading-7 font-semibold tracking-[-0.01em]">Pipeline</h1>
        {summary ? (
          <p className="text-[13px] leading-5 text-muted-foreground tabular-nums" aria-live="polite">
            {summary}
          </p>
        ) : null}
      </header>
      <Tabs value={tab} onValueChange={(next) => setParams({ tab: next }, { replace: true })} className="gap-5">
        <TabsList variant="line" aria-label="Pipeline" className="h-10 w-full justify-start gap-5 border-b p-0">
          <TabsTrigger value="last-week" className="h-10 flex-none gap-2 px-0.5">
            Last week
            <Count n={counts['last-week']} />
          </TabsTrigger>
          <TabsTrigger value="in-flight" className="h-10 flex-none gap-2 px-0.5">
            In flight
            <Count n={counts['in-flight']} />
          </TabsTrigger>
          <TabsTrigger value="watch" className="h-10 flex-none gap-2 px-0.5">
            Watch list
            <Count n={counts.watch} />
          </TabsTrigger>
        </TabsList>

        {!loaded ? (
          <div role="status" aria-label="Loading the pipeline" className="grid gap-3">
            <Skeleton className="h-16 w-full rounded-xl" />
            <Skeleton className="h-16 w-full rounded-xl" />
          </div>
        ) : (
          <>
            <TabsContent value="last-week">
              {counts['last-week'] === 0 ? (
                <EmptyState icon={CircleCheck} title="Nothing to answer.">
                  A request or an application you sent a week ago shows here, one at a time.
                </EmptyState>
              ) : (
                <OutcomePrompt />
              )}
            </TabsContent>
            <TabsContent value="in-flight">
              {counts['in-flight'] === 0 ? (
                <EmptyState icon={Hourglass} title="Nothing in flight.">
                  A company you asked in the last 14 days, or one that replied, shows here.
                </EmptyState>
              ) : (
                <InFlightList items={plan.data?.inFlight ?? []} />
              )}
            </TabsContent>
            <TabsContent value="watch">
              {counts.watch === 0 ? (
                <EmptyState icon={Radar} title="Nothing to watch.">
                  Careers pages that changed, new companies to reach and possible duplicates show here.
                </EmptyState>
              ) : (
                <WatchList />
              )}
            </TabsContent>
          </>
        )}
      </Tabs>
      </div>
    </div>
  );
}
