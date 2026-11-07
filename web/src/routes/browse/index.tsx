import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router';
import { keepPreviousData, useInfiniteQuery, useQueryClient } from '@tanstack/react-query';
import { Check, SearchX } from 'lucide-react';
import {
  pollMs,
  queryKeys,
  usePageClient,
  type Id,
  type JobRow,
  type JobsParams,
  type JobsSize,
  type JobsTab,
  type JobsWindow,
} from '@/api';
import { PageHeader } from '@/components/page-header';
import { EmptyState } from '@/components/state-views';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Kbd } from '@/components/ui/kbd';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useAnnounce, useHotkeys, type Binding } from '@/keyboard/hotkeys';
import { formatDueDay } from '@/lib/format';
import { openExternal, safeHref } from '@/lib/safe-href';
import { cn } from '@/lib/utils';
import { usePendingWrite, useViewQuery } from '@/pending/pending';

const GROUP = 'Jobs';
/** How long typing waits before the search is asked. */
const TYPING_MS = 180;
/** Postings per page: the API's most. */
const PAGE = 50;

const WINDOWS: { value: JobsWindow; label: string }[] = [
  { value: '7', label: '7 days' },
  { value: '30', label: '30 days' },
  { value: '60', label: '60 days' },
  { value: '90', label: '90 days' },
  { value: 'any', label: 'Any time' },
];
const SIZES: { value: JobsSize; label: string; hint: string }[] = [
  { value: 'any', label: 'Any size', hint: 'Every company' },
  { value: 'small', label: 'Small', hint: '300 people or fewer' },
  { value: 'big', label: 'Big', hint: 'More than 300 people, or many open roles' },
  { value: 'unknown', label: 'Not known', hint: 'No size on file and no sign of being big' },
];

const LINK =
  'rounded-sm underline decoration-foreground/30 underline-offset-3 outline-none hover:decoration-foreground focus-visible:ring-[3px] focus-visible:ring-ring/70';
/** Small controls on a desk; thumb sized on a phone. */
const CHIP = 'h-8 px-3 text-xs max-sm:h-11';

const plural = (n: number, one: string, many: string) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;
const ago = (days: number) => (days === 0 ? 'today' : days === 1 ? '1 day ago' : `${days} days ago`);
const sizeLine = (j: JobRow) => (j.size === null ? (j.big ? 'Big company, size not known' : 'Size not known') : `${j.big ? 'Big company, ' : ''}${plural(j.size, 'person', 'people')}`);
const windowText = (w: JobsWindow) => (w === 'any' ? '' : ` in the last ${w} days`);
const levelText = (tab: JobsTab) => (tab === 'your_level' ? 'at your level' : 'at higher levels');

/**
 * What he has asked for and the server has not heard yet: postings passed and companies being gone ahead with. It is a
 * query only so the pending-write overlay can lay these over it (Undo drops them, a commit settles them).
 */
interface Marks { hidden: Id[]; ahead: Id[] }
const NO_MARKS: Marks = { hidden: [], ahead: [] };

/** "Widen the window, clear the search, or turn off Not contacted yet." Names only what is on. */
function whatToWiden(on: { window: boolean; q: boolean; size: boolean; notContacted: boolean }): string {
  const steps = [
    on.window ? 'widen the window' : null,
    on.q ? 'clear the search' : null,
    on.size ? 'clear the size' : null,
    on.notContacted ? 'turn off Not contacted yet' : null,
  ].filter((s): s is string => s !== null);
  if (steps.length === 0) return 'Nothing is open here right now. New postings appear after the next crawl.';
  const last = steps[steps.length - 1] as string;
  const text = steps.length === 1 ? last : `${steps.slice(0, -1).join(', ')}${steps.length > 2 ? ',' : ''} or ${last}`;
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}.`;
}

function Flags({ job }: { job: JobRow }) {
  if (!job.saysNoSponsorship && !job.everify && !job.yc && !job.contactedOn) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {job.saysNoSponsorship ? <Badge variant="warn">Says no sponsorship</Badge> : null}
      {job.everify ? <Badge variant="idle">E-Verify</Badge> : null}
      {job.yc ? <Badge variant="idle">YC</Badge> : null}
      {job.contactedOn ? <Badge variant="info">You contacted them on {formatDueDay(job.contactedOn)}</Badge> : null}
    </div>
  );
}

function JobItem({
  job,
  current,
  answer,
  going,
  onSelect,
  onPass,
  onGoAhead,
}: {
  job: JobRow;
  current: boolean;
  answer: string | undefined;
  going: boolean;
  onSelect: () => void;
  onPass: () => void;
  onGoAhead: () => void;
}) {
  const href = safeHref(job.url);
  return (
    <li
      data-job={job.postingId}
      data-company={job.companyName}
      aria-current={current ? 'true' : undefined}
      onClick={onSelect}
      className={cn('grid items-start gap-x-6 gap-y-2 border-b border-l-2 py-3 pr-6 pl-[22px] text-[13px] sm:grid-cols-[minmax(0,1fr)_auto]', current ? 'border-l-ring bg-muted/40' : 'border-l-transparent hover:bg-muted/20')}
    >
      <div className="grid min-w-0 gap-1.5">
        <p className="flex flex-wrap items-baseline gap-x-2">
          {href ? (
            <a href={href} target="_blank" rel="noopener noreferrer" className={cn(LINK, 'text-sm font-medium')}>
              {job.title}
            </a>
          ) : (
            <span className="text-sm font-medium">{job.title}</span>
          )}
          <span className="text-xs text-muted-foreground">{job.levelTag}</span>
        </p>
        <p className="flex flex-wrap gap-x-4 gap-y-0.5 text-muted-foreground">
          <Link to={`/companies/${job.companyId}`} className="rounded-sm font-medium text-foreground outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/70">
            {job.companyName}
          </Link>
          <span>{`${job.dateKind} ${ago(job.ageDays)}`}</span>
          <span>{sizeLine(job)}</span>
          <span>{job.location}</span>
        </p>
        <Flags job={job} />
      </div>
      {answer !== undefined ? (
        <p role="status" className="flex max-w-72 items-start gap-1.5 text-xs text-foreground">
          <Check aria-hidden className="mt-0.5 size-3.5 shrink-0 text-ok" />
          {answer}
        </p>
      ) : going ? (
        <p role="status" className="text-xs text-muted-foreground">
          Going ahead
        </p>
      ) : (
        <div role="group" aria-label="Your answer" className="flex gap-1.5">
          <Button type="button" variant="outline" size="sm" className="h-7 gap-1.5 px-2.5 text-xs max-sm:h-11" aria-keyshortcuts={current ? 'x' : undefined} onClick={onPass}>
            Pass
          </Button>
          <Button type="button" variant="secondary" size="sm" className="h-7 gap-1.5 px-2.5 text-xs max-sm:h-11" aria-keyshortcuts={current ? 'a' : undefined} onClick={onGoAhead}>
            Go ahead
          </Button>
        </div>
      )}
    </li>
  );
}

function Skeletons() {
  return (
    <div aria-busy className="grid" role="status" aria-label="Loading the jobs">
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className="grid gap-2 border-b px-6 py-3">
          <Skeleton className="h-4 w-80" />
          <Skeleton className="h-3.5 w-[28rem]" />
        </div>
      ))}
    </div>
  );
}

/**
 * Jobs (sidebar, or F on Today; #156): the open engineering postings, newest first, in two tabs (your level, higher
 * levels). He reads every posting himself: the title opens it in a new tab, Pass hides that posting for good (never
 * its company) and Go ahead puts its company on today's list as his own pick. There is no score and no ranking text.
 *
 * Pass takes the row off at once and puts it back, with the reason, if the pass did not go through. Go ahead keeps the
 * row and writes the API's own sentence in it ("Added ... to today's list.", or when it is kept for the next list).
 *
 * Keys: the arrows move through the rows (also from the search box), Enter opens the posting, X passes, A goes ahead.
 * The page sets no focus on arrival: the keys listen on the whole page.
 */
export default function BrowsePage() {
  const client = usePageClient('plan');
  const queryClient = useQueryClient();
  const announce = useAnnounce();
  const [tab, setTab] = useState<JobsTab>('your_level');
  const [windowDays, setWindowDays] = useState<JobsWindow>('30');
  const [size, setSize] = useState<JobsSize>('any');
  const [notContacted, setNotContacted] = useState(false);
  const [text, setText] = useState('');
  const [q, setQ] = useState('');
  const [active, setActive] = useState<Id | null>(null);
  const [answers, setAnswers] = useState<ReadonlyMap<Id, string>>(new Map());
  const write = usePendingWrite();
  const marks = useViewQuery(queryKeys.jobsMarks, () => Promise.resolve(NO_MARKS));
  const hidden = useMemo(() => new Set(marks.data?.hidden), [marks.data]);
  const ahead = useMemo(() => new Set(marks.data?.ahead), [marks.data]);
  const list = useRef<HTMLUListElement>(null);
  const searchBox = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const id = setTimeout(() => setQ(text.trim()), TYPING_MS);
    return () => clearTimeout(id);
  }, [text]);

  const params: Omit<JobsParams, 'after'> = { view: tab, window: windowDays, size, notContacted, q, limit: PAGE };
  const query = useInfiniteQuery({
    queryKey: queryKeys.jobs(params),
    queryFn: ({ pageParam }) => client.jobs({ ...params, after: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.nextAfter ?? undefined,
    // Rows of the last ask stay on screen, dimmed, while a new ask loads. The 60 s refetchInterval brings in anything written since.
    placeholderData: keepPreviousData,
    refetchInterval: pollMs.default,
  });
  const stale = query.isPlaceholderData;

  const loaded = useMemo(() => {
    const seen = new Set<Id>();
    return (query.data?.pages ?? []).flatMap((p) => p.rows).filter((j) => !seen.has(j.postingId) && !!seen.add(j.postingId));
  }, [query.data]);
  const jobs = useMemo(() => loaded.filter((j) => !hidden.has(j.postingId)), [loaded, hidden]);
  // A pass the server has not been read after yet still counts: the numbers follow the rows.
  const passed = loaded.length - jobs.length;
  const last = query.data?.pages.at(-1);
  const total = last ? Math.max(last.total - passed, 0) : undefined;
  const counts = last ? { your_level: last.counts.yourLevel - (tab === 'your_level' ? passed : 0), higher: last.counts.higher - (tab === 'higher' ? passed : 0) } : undefined;

  // A pass taken back (Undo, or it did not go through) puts the row back and puts him back on it. Done while rendering,
  // so the very next key already acts on that row.
  const [seenHidden, setSeenHidden] = useState(hidden);
  if (hidden !== seenHidden) {
    setSeenHidden(hidden);
    const back = [...seenHidden].find((id) => !hidden.has(id) && loaded.some((j) => j.postingId === id));
    if (back !== undefined) setActive(back);
  }

  const current = jobs.find((j) => j.postingId === active) ?? jobs[0] ?? null;
  const index = current ? jobs.indexOf(current) : -1;

  useEffect(() => {
    if (current) list.current?.querySelector(`[data-job="${current.postingId}"]`)?.scrollIntoView?.({ block: 'nearest' });
  }, [current?.postingId]); // eslint-disable-line react-hooks/exhaustive-deps

  const move = (step: 1 | -1) => {
    if (document.activeElement === searchBox.current) searchBox.current?.blur();
    const next = jobs[Math.min(Math.max(index + step, 0), jobs.length - 1)];
    if (next) setActive(next.postingId);
  };

  /** Nothing is sent now: the row leaves at once and the toast offers Undo. The pass goes out when the window closes, or when he leaves the page. */
  const pass = (job: JobRow) => {
    const id = job.postingId;
    if (hidden.has(id) || ahead.has(id) || answers.has(id)) return;
    // The row he is on leaves, and the one below it (else the one above) becomes the current one.
    if (current?.postingId === id) setActive((jobs[index + 1] ?? jobs[index - 1])?.postingId ?? null);
    announce(`Passed. ${job.title} is off the list.`);
    write({
      id: `job-pass:${id}`,
      label: 'Passed.',
      afterCommit: 'Passed.',
      patches: [{ key: queryKeys.jobsMarks, apply: (m: Marks) => ({ ...m, hidden: [...m.hidden, id] }) }],
      // The list is read again before the row's mark is dropped, so the row does not flash back.
      commit: (o) => client.passJob(id, o).then(() => queryClient.invalidateQueries({ queryKey: queryKeys.jobsAll })),
    });
  };

  /** The same: the row says "Going ahead" with Undo for the window; the API's own sentence replaces it once it answers. */
  const goAhead = (job: JobRow) => {
    const id = job.postingId;
    if (hidden.has(id) || ahead.has(id) || answers.has(id)) return;
    const label = `Going ahead with ${job.companyName}.`;
    write({
      id: `job-ahead:${id}`,
      label,
      afterCommit: label,
      patches: [{ key: queryKeys.jobsMarks, apply: (m: Marks) => ({ ...m, ahead: [...m.ahead, id] }) }],
      commit: (o) =>
        client.goAhead(id, o).then((res) => {
          setAnswers((a) => new Map(a).set(id, res.text));
          announce(res.text);
          void queryClient.invalidateQueries({ queryKey: queryKeys.plan, exact: true });
        }),
    });
  };

  const open = current !== null;
  const bindings: Binding[] = [
    { keys: 'arrowdown', label: 'Next job', group: GROUP, inInputs: true, enabled: open, run: () => move(1) },
    { keys: 'arrowup', label: 'Previous job', group: GROUP, inInputs: true, enabled: open, run: () => move(-1) },
    { keys: 'enter', label: 'Open the posting', group: GROUP, enabled: open, run: () => current && openExternal(current.url) },
    { keys: 'x', label: 'Pass', group: GROUP, enabled: open, run: () => current && pass(current) },
    { keys: 'a', label: 'Go ahead', group: GROUP, enabled: open, run: () => current && goAhead(current) },
  ];
  useHotkeys(bindings);

  const on = { window: windowDays !== 'any', q: q !== '', size: size !== 'any', notContacted };
  const other: JobsTab = tab === 'your_level' ? 'higher' : 'your_level';
  const otherCount = counts?.[other] ?? 0;
  const headline = total === undefined || stale ? 'Loading the jobs' : `${total.toLocaleString('en-US')} ${levelText(tab)}${windowText(windowDays)}`;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="grid flex-none gap-3 border-b px-6 pt-5 pb-3">
        <PageHeader title="Jobs" summary={headline} />
        <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
          <Tabs
            value={tab}
            onValueChange={(next) => {
              setTab(next as JobsTab);
              setActive(null);
            }}
          >
            <TabsList aria-label="Level" className="max-sm:min-h-[3.25rem]">
              <TabsTrigger value="your_level" className="gap-1.5 px-3 max-sm:min-h-11">
                Your level
                {counts ? <span className="text-xs tabular-nums text-muted-foreground">{counts.your_level.toLocaleString('en-US')}</span> : null}
              </TabsTrigger>
              <TabsTrigger value="higher" className="gap-1.5 px-3 max-sm:min-h-11">
                Higher levels
                {counts ? <span className="text-xs tabular-nums text-muted-foreground">{counts.higher.toLocaleString('en-US')}</span> : null}
              </TabsTrigger>
            </TabsList>
          </Tabs>
          <p className="flex items-center gap-3 text-xs text-muted-foreground" aria-hidden>
            <span className="inline-flex items-center gap-1"><Kbd>↑</Kbd><Kbd>↓</Kbd> move</span>
            <span className="inline-flex items-center gap-1"><Kbd>Enter</Kbd> open</span>
            <span className="inline-flex items-center gap-1"><Kbd>X</Kbd> pass</span>
            <span className="inline-flex items-center gap-1"><Kbd>A</Kbd> go ahead</span>
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <Input
            ref={searchBox}
            type="search"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Search by company name"
            aria-label="Search by company name"
            autoComplete="off"
            className="h-8 w-64 max-sm:h-11"
          />
          <div role="group" aria-label="Posted within" className="flex flex-wrap gap-1.5">
            {WINDOWS.map((w) => (
              <Button key={w.value} type="button" variant={windowDays === w.value ? 'secondary' : 'outline'} size="sm" aria-pressed={windowDays === w.value} className={CHIP} onClick={() => setWindowDays(w.value)}>
                {w.label}
              </Button>
            ))}
          </div>
          <div role="group" aria-label="Company size" className="flex flex-wrap gap-1.5">
            {SIZES.map((s) => (
              <Button key={s.value} type="button" variant={size === s.value ? 'secondary' : 'outline'} size="sm" aria-pressed={size === s.value} title={s.hint} className={CHIP} onClick={() => setSize(s.value)}>
                {s.label}
              </Button>
            ))}
          </div>
          <Button type="button" variant={notContacted ? 'secondary' : 'outline'} size="sm" aria-pressed={notContacted} title="Hide companies you have already contacted" className={CHIP} onClick={() => setNotContacted((v) => !v)}>
            Not contacted yet
          </Button>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-auto">
        {query.isError && !query.data ? (
          <p role="alert" className="px-6 py-6 text-sm">
            The jobs did not load. Nothing was changed.{' '}
            <button type="button" className="underline underline-offset-3" onClick={() => void query.refetch()}>
              Try again
            </button>
          </p>
        ) : !query.data ? (
          <Skeletons />
        ) : jobs.length === 0 && !stale ? (
          <EmptyState
            icon={SearchX}
            title={`No jobs ${levelText(tab)}${windowText(windowDays)}.`}
            action={
              otherCount > 0 ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setTab(other);
                    setActive(null);
                  }}
                >
                  {`Show ${otherCount.toLocaleString('en-US')} ${levelText(other)}`}
                </Button>
              ) : undefined
            }
          >
            {whatToWiden(on)}
          </EmptyState>
        ) : (
          <ul ref={list} aria-label="Jobs" aria-busy={stale} className={cn(stale && 'opacity-60 transition-opacity')}>
            {jobs.map((j) => (
              <JobItem
                key={j.postingId}
                job={j}
                current={j.postingId === current?.postingId}
                answer={answers.get(j.postingId)}
                going={ahead.has(j.postingId)}
                onSelect={() => setActive(j.postingId)}
                onPass={() => pass(j)}
                onGoAhead={() => goAhead(j)}
              />
            ))}
          </ul>
        )}
        {query.hasNextPage ? (
          <div className="grid justify-items-center border-t px-6 py-4">
            <Button type="button" variant="outline" size="sm" className={CHIP} disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
              {query.isFetchingNextPage ? 'Loading' : 'Show more'}
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
