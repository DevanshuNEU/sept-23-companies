import { useEffect, useEffectEvent, useState, type ReactNode } from 'react';
import { Link } from 'react-router';
import { Award, ChevronRight } from 'lucide-react';
import { pollMs, queryKeys, usePageClient, type Id, type Win, type WinsView } from '@/api';
import { EmptyState, QueryState } from '@/components/state-views';
import { StatusBadge } from '@/components/status-badge';
import { Badge } from '@/components/ui/badge';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useHotkeys } from '@/keyboard/hotkeys';
import { ageDays } from '@/lib/format';
import { winKindLabel } from '@/lib/status';
import { useUrlState } from '@/lib/url-state';
import { cn } from '@/lib/utils';
import { useViewQuery } from '@/pending/pending';
import { formatDateMonthDay, formatMonthDay, monthOf } from '@/routes/today/short-dates';
import { usePlanActions } from '@/routes/today/use-plan-actions';

type Show = 'all' | 'big';
const BIG_KINDS: ReadonlySet<Win['kind']> = new Set<Win['kind']>(['referred', 'interview', 'offer']);

const link =
  'rounded-sm text-foreground underline decoration-border underline-offset-3 outline-none hover:decoration-current focus-visible:ring-[3px] focus-visible:ring-ring/70';

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

/** "Since Oct 18: 1 interview, 1 referral, 3 replies, 0 accepted requests." Every count beside its word. */
function Counts({ view }: { view: WinsView }) {
  const c = view.counts;
  const parts: [number, string, string][] = [
    ...(c.offer > 0 ? [[c.offer, 'offer', 'offers'] as [number, string, string]] : []),
    [c.interview, 'interview', 'interviews'],
    [c.referred, 'referral', 'referrals'],
    [c.replied, 'reply', 'replies'],
    [c.accepted, 'accepted request', 'accepted requests'],
  ];
  const nodes: ReactNode[] = [];
  parts.forEach(([n, one, many], i) => {
    nodes.push(
      <span key={one}>
        {i > 0 ? ', ' : ''}
        <b className="font-semibold">{n}</b> {plural(n, one, many)}
      </span>,
    );
  });
  return (
    <p data-testid="counts" className="text-sm tabular-nums">
      {`Since ${formatDateMonthDay(view.since)}: `}
      {nodes}.
    </p>
  );
}

/** One kept win: its date, the API's line, who and what for, what he sent (folded), and its kind. */
function WinRow({ win, asOf, isNew }: { win: Win; asOf: string; isNew: boolean }) {
  const meta = `${win.person?.title ? `, ${win.person.title}` : ''}.${win.role ? ` For ${win.role}.` : ''}`;
  return (
    <li
      data-testid="win"
      className={cn(
        'grid grid-cols-[64px_minmax(0,1fr)_auto] items-start gap-x-4 border-t px-4 py-3.5 first:border-t-0',
        isNew && 'border-l-2 border-l-ok-dot',
      )}
    >
      <span className="pt-0.5 text-[13px] text-muted-foreground tabular-nums">{ageDays(win.at, asOf) <= 0 ? 'Today' : formatMonthDay(win.at)}</span>
      <div className="grid min-w-0 gap-1">
        <p data-line className={cn('max-w-[62ch] text-sm font-medium', BIG_KINDS.has(win.kind) && 'text-base')}>
          {win.line}
        </p>
        <p className="text-[13px] text-muted-foreground">
          <Link to={`/companies/${win.company.id}`} className={link}>
            {win.company.name}
          </Link>
          {meta}
        </p>
        {win.sentNote ? (
          <Collapsible>
            <CollapsibleTrigger className="group -ml-1 flex items-center gap-1 rounded-sm px-1 py-1 text-[13px] text-muted-foreground outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/70">
              <ChevronRight aria-hidden className="size-3.5 transition-transform group-data-[state=open]:rotate-90" />
              What you sent
            </CollapsibleTrigger>
            <CollapsibleContent>
              <blockquote className="mt-1 max-w-[62ch] rounded-md border-l-2 bg-muted/50 px-3 py-2 text-[13px] leading-[22px]">
                {win.sentNote}
              </blockquote>
            </CollapsibleContent>
          </Collapsible>
        ) : null}
      </div>
      <div className="flex items-center gap-2">
        {isNew ? <Badge>New</Badge> : null}
        <StatusBadge status={{ label: winKindLabel[win.kind], tone: 'ok' }} dot />
      </div>
    </li>
  );
}

function WinsSkeleton() {
  return (
    <div aria-hidden className="grid gap-2 rounded-lg border p-3">
      {Array.from({ length: 5 }, (_, i) => (
        <Skeleton key={i} className="h-14 w-full" />
      ))}
    </div>
  );
}

/**
 * The win log (design 16.4): every win the API keeps, newest first, grouped by month, each with what he sent. The
 * counts and the line of each win are the API's. Opening the page marks every unseen win seen at once, but the New
 * marks stay for this visit (they are remembered here, not read again from the data), so the page does not erase
 * what it is showing. A said-no reply is never listed: the API does not keep it as a win.
 */
export default function WinsPage() {
  const client = usePageClient('wins');
  const actions = usePlanActions('wins');
  const query = useViewQuery(queryKeys.wins, () => client.wins(), { pollMs: pollMs.default });
  const [show, setShow] = useUrlState('show', 'all');
  const filter: Show = show === 'big' ? 'big' : 'all';

  useHotkeys([
    { keys: '1', label: 'All wins', group: 'Wins', run: () => setShow('all') },
    { keys: '2', label: 'Referrals and interviews', group: 'Wins', run: () => setShow('big') },
  ]);

  // The wins unseen when the page showed them, kept for the visit. Adjusted while rendering.
  const [fresh, setFresh] = useState<ReadonlySet<Id>>(new Set());
  const unseen = (query.data?.wins ?? []).filter((w) => w.unseen).map((w) => w.winId);
  if (unseen.some((id) => !fresh.has(id))) setFresh(new Set([...fresh, ...unseen]));
  // Every poll that still finds one unseen asks again, so a win that arrives while the page is open is marked too.
  const markSeen = useEffectEvent((ids: Id[]) => actions.seeWins(ids));
  const unseenKey = unseen.join(',');
  useEffect(() => {
    if (unseenKey !== '') markSeen(unseenKey.split(',').map(Number));
  }, [unseenKey, query.dataUpdatedAt]);

  return (
    <div data-testid="wins-page" className="max-w-[880px] px-6 pt-5 pb-12">
      <div className="mb-4">
        <h1 className="text-xl font-semibold tracking-tight">Wins</h1>
        <p className="mt-0.5 text-[13px] text-muted-foreground">Every win you have logged, newest first, with what you sent.</p>
      </div>
      <QueryState
        query={query}
        skeleton={<WinsSkeleton />}
        isEmpty={(v) => v.wins.length === 0}
        empty={
          <div className="rounded-lg border">
            <EmptyState icon={Award} title="No wins yet.">
              A request that is accepted or a reply lands here the moment you know.
            </EmptyState>
          </div>
        }
      >
        {(v) => {
          const wins = [...v.wins].sort((a, b) => Date.parse(b.at) - Date.parse(a.at)).filter((w) => filter === 'all' || BIG_KINDS.has(w.kind));
          const months: { key: string; heading: string; wins: Win[] }[] = [];
          for (const win of wins) {
            const m = monthOf(win.at, v.asOf);
            const last = months.at(-1);
            if (last?.key === m.key) last.wins.push(win);
            else months.push({ ...m, wins: [win] });
          }
          return (
            <div className="grid gap-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <Counts view={v} />
                <Tabs value={filter} onValueChange={(next) => setShow(next)}>
                  <TabsList aria-label="Which wins">
                    <TabsTrigger value="all" className="px-2.5" aria-keyshortcuts="1">
                      All
                    </TabsTrigger>
                    <TabsTrigger value="big" className="px-2.5" aria-keyshortcuts="2">
                      Referrals and interviews
                    </TabsTrigger>
                  </TabsList>
                  <TabsContent value={filter} className="sr-only">
                    {filter === 'all' ? 'Showing every win.' : 'Showing referrals, interviews and offers.'}
                  </TabsContent>
                </Tabs>
              </div>
              {months.length === 0 ? (
                <div className="rounded-lg border">
                  <EmptyState icon={Award} title="No referrals or interviews yet.">
                    They land here the moment you log them.
                  </EmptyState>
                </div>
              ) : (
                <div className="grid gap-4">
                  {months.map((m) => (
                    <section key={m.key} aria-label={m.heading} className="overflow-hidden rounded-lg border">
                      <h2 className="border-b bg-muted/40 px-4 py-2 text-xs font-medium text-muted-foreground">{m.heading}</h2>
                      <ul>
                        {m.wins.map((w) => (
                          <WinRow key={w.winId} win={w} asOf={v.asOf} isNew={fresh.has(w.winId) || w.unseen} />
                        ))}
                      </ul>
                    </section>
                  ))}
                </div>
              )}
              <p className="text-xs text-muted-foreground">
                The log keeps every win. To change what you logged, open the company's page.
              </p>
            </div>
          );
        }}
      </QueryState>
    </div>
  );
}
