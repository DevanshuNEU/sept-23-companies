import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router';
import { keepPreviousData, useInfiniteQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowUp, Check, SearchX } from 'lucide-react';
import {
  pollMs,
  queryKeys,
  useClient,
  usePageClient,
  type Id,
  type IsoTime,
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
import { gapTag, postedLabel, repostTag } from '@/lib/format';
import { quietLink } from '@/lib/quiet-link';
import { openExternal, safeHref } from '@/lib/safe-href';
import { formatDateMonthDay, formatMonthDay } from '@/lib/short-dates';
import { cn } from '@/lib/utils';
import { usePendingWrite, useViewQuery } from '@/pending/pending';
import { CheckLineView, CheckNowButton, useCheckNow } from './check-now';
import { heldRows, isNew, markOpened, readLastOpened } from './new-rows';

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

/** Scrolled this little or less still counts as at the top: new rows show at once there. */
const TOP_PX = 8;

const LINK =
  'rounded-sm underline decoration-foreground/30 underline-offset-3 outline-none hover:decoration-foreground focus-visible:ring-[3px] focus-visible:ring-ring/70';
/** Small controls on a desk; thumb sized on a phone. */
const CHIP = 'h-8 px-3 text-xs max-sm:h-11';

const plural = (n: number, one: string, many: string) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;
const sizeLine = (j: JobRow) => (j.size === null ? (j.big ? 'Big company, size not known' : 'Size not known') : `${j.big ? 'Big company, ' : ''}${plural(j.size, 'person', 'people')}`);
const windowText = (w: JobsWindow) => (w === 'any' ? '' : ` in the last ${w} days`);
const levelText = (tab: JobsTab) => (tab === 'your_level' ? 'at your level' : 'at higher levels');

/**
 * What he has asked for and the server has not heard yet: postings passed, and Reached (#195): the postings he pressed it
 * on and their companies, whose rows all show reached at once. It is a query only so the pending-write overlay can lay
 * these over it (Undo drops them, a commit settles them).
 */
interface Marks { hidden: Id[]; done: Id[]; reached: Id[] }
const NO_MARKS: Marks = { hidden: [], done: [], reached: [] };

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

/**
 * The row's tags: first what qualifies its time (a repost, a late find), then the company's flags, then the day he
 * reached the company (a person contacted there, or his own mark). `reachedOn` is that day as words.
 */
function Flags({ job, asOf, reachedOn }: { job: JobRow; asOf: IsoTime; reachedOn: string | null }) {
  const repost = repostTag(job.repostedFrom, asOf);
  const gap = gapTag(job);
  if (!repost && !gap && !job.saysNoSponsorship && !job.everify && !job.yc && !reachedOn) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {repost ? <Badge variant="idle">{repost}</Badge> : null}
      {gap ? <Badge variant="idle">{gap}</Badge> : null}
      {job.saysNoSponsorship ? <Badge variant="warn">Says no sponsorship</Badge> : null}
      {job.everify ? <Badge variant="idle">E-Verify</Badge> : null}
      {job.yc ? <Badge variant="idle">YC</Badge> : null}
      {reachedOn ? <Badge variant="info">{`Reached ${reachedOn}`}</Badge> : null}
    </div>
  );
}

function JobItem({
  job,
  asOf,
  current,
  fresh,
  reachedOn,
  answer,
  marking,
  onSelect,
  onPass,
  onReached,
}: {
  job: JobRow;
  asOf: IsoTime;
  current: boolean;
  /** First seen since his last visit. */
  fresh: boolean;
  /** The day his company was reached, as words ("Nov 9"), or null. */
  reachedOn: string | null;
  answer: string | undefined;
  /** Reached on this row waits in its undo window. */
  marking: boolean;
  onSelect: () => void;
  onPass: () => void;
  onReached: () => void;
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
          {fresh ? <Badge variant="info">New</Badge> : null}
        </p>
        <p className="flex flex-wrap gap-x-4 gap-y-0.5 text-muted-foreground">
          <Link to={`/companies/${job.companyId}`} className="rounded-sm font-medium text-foreground outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/70">
            {job.companyName}
          </Link>
          <span className="tabular-nums">{postedLabel(job, asOf)}</span>
          <span>{sizeLine(job)}</span>
          <span>{job.location}</span>
        </p>
        <Flags job={job} asOf={asOf} reachedOn={reachedOn} />
      </div>
      {answer !== undefined ? (
        <p role="status" className="flex max-w-72 items-start gap-1.5 text-xs text-foreground">
          <Check aria-hidden className="mt-0.5 size-3.5 shrink-0 text-ok" />
          {answer}
        </p>
      ) : marking ? (
        <p role="status" className="text-xs text-muted-foreground">
          Marking reached
        </p>
      ) : (
        <div role="group" aria-label="Your answer" className="flex gap-1.5">
          <Button type="button" variant="outline" size="sm" className="h-7 gap-1.5 px-2.5 text-xs max-sm:h-11" aria-keyshortcuts={current ? 'x' : undefined} onClick={onPass}>
            Pass
          </Button>
          <Button type="button" variant="secondary" size="sm" className="h-7 gap-1.5 px-2.5 text-xs max-sm:h-11" title="Mark this company as reached" aria-keyshortcuts={current ? 'a' : undefined} onClick={onReached}>
            Mark reached
          </Button>
        </div>
      )}
    </li>
  );
}

/** The same rows in the same order (the server's copies may differ; `heldRows` takes those). */
function sameRows(a: readonly JobRow[], b: readonly JobRow[]): boolean {
  return a.length === b.length && a.every((r, i) => r.postingId === b[i]?.postingId);
}

/**
 * "Today: 34 sent, 2 replies", opening Progress (design 4.5): the emails the sender sent today (the sidebar's count)
 * and the LinkedIn requests he logged, then today's replies, as Progress counts them. Nothing until both have loaded,
 * and nothing when either did not: the line is a glance, never a guess.
 */
function OutreachLine() {
  const client = useClient();
  const shell = useViewQuery(queryKeys.shell, () => client.shell(), { pollMs: pollMs.default });
  const progress = useViewQuery(queryKeys.progress, () => client.progress(), { pollMs: pollMs.default });
  if (!shell.data || !progress.data) return null;
  const sent = shell.data.sending.sentToday + progress.data.today.requests;
  const replies = progress.data.today.replied;
  return (
    <p aria-live="off">
      <Link to="/progress" className={cn(quietLink, 'max-sm:inline-flex max-sm:min-h-6 max-sm:items-center')}>
        {`Today: ${sent.toLocaleString('en-US')} sent, ${plural(replies, 'reply', 'replies')}`}
      </Link>
    </p>
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
 * Jobs (#156), the page Knock opens on (#195): the open engineering postings, newest first by when each was posted
 * (#193), in two tabs (your level, higher levels). Each row says when: the board's time to the minute, the board's date,
 * or when Knock first saw it, with a tag for a repost or a late find. Rows with no date sit under Date unknown at the
 * end. He reads every posting himself: the title opens it in a new tab, Pass hides that posting for good (never its
 * company) and Reached marks its company as reached, even when he reached it outside Knock. There is no score and no
 * ranking text. Every row of a reached company says "Reached Nov 9" (a person contacted there, or his own mark).
 *
 * Pass takes the row off at once and puts it back, with the reason, if the pass did not go through. Reached keeps the row,
 * tags every row of the company at once with Undo for the window, and then writes the API's own sentence in the row.
 * His mark is taken back later from the company page. The line under the header is today's outreach from the sidebar's
 * send count and Progress, and opens Progress.
 *
 * Check now (#194) asks the crawl job to read every Greenhouse and Ashby board; the line under the header says how it
 * is going. New rows show at once while he is at the top on no row; scrolled down or on a row, the list stays put and an
 * "N new" button shows them. A row first seen since his last visit (not a first read, not a repost) says New.
 *
 * Keys: the arrows move through the rows (also from the search box), Enter opens the posting, X passes, A marks done,
 * C checks now. The page sets no focus on arrival: the keys listen on the whole page.
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
  const marking = useMemo(() => new Set(marks.data?.done), [marks.data]);
  const reachedNow = useMemo(() => new Set(marks.data?.reached), [marks.data]);
  const list = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const searchBox = useRef<HTMLInputElement>(null);
  const check = useCheckNow();
  // His last visit marks what is new, read once a mount. A visit ends when the tab closes or reloads (or at "N new"), not
  // when he steps to a company page and back, so the marks stay for the whole sitting.
  const [since] = useState(readLastOpened);
  useEffect(() => {
    const leave = () => markOpened();
    window.addEventListener('pagehide', leave);
    return () => window.removeEventListener('pagehide', leave);
  }, []);
  const [atTop, setAtTop] = useState(true);
  /** The rows on screen for one ask (`key`), so new rows can wait while he is scrolled down or on a row. */
  const [held, setHeld] = useState<{ key: string; rows: JobRow[] } | null>(null);

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

  // New rows never move the list under him: scrolled down or on a row, they wait behind "N new" (design 4.5). A new ask
  // (a tab, a window, a search) starts from what it brings.
  const listKey = JSON.stringify(params);
  const sameAsk = !stale && query.data !== undefined && held?.key === listKey;
  const shown = sameAsk && held ? heldRows(held.rows, loaded, atTop, active !== null, query.hasNextPage) : { rows: loaded, waiting: 0 };
  if (!stale && query.data !== undefined && (held?.key !== listKey || !sameRows(held.rows, shown.rows))) setHeld({ key: listKey, rows: shown.rows });

  const jobs = useMemo(() => shown.rows.filter((j) => !hidden.has(j.postingId)), [shown.rows, hidden]);
  // A pass the server has not been read after yet still counts: the numbers follow the rows.
  const passed = loaded.filter((j) => hidden.has(j.postingId)).length;
  const last = query.data?.pages.at(-1);
  const total = last ? Math.max(last.total - passed, 0) : undefined;
  const dated = jobs.filter((j) => j.listAt !== null);
  const undated = jobs.filter((j) => j.listAt === null);
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
    if (hidden.has(id) || marking.has(id) || answers.has(id)) return;
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

  /**
   * Reached: every row of the company says reached at once and the toast offers Undo; nothing is sent until the window
   * closes. The API's own sentence then replaces the row's buttons, and the list is read again before the mark is dropped,
   * so the tag does not flash off. Nothing else stops it: Reached on a company already reached just says when.
   */
  const markCompanyReached = (job: JobRow) => {
    const id = job.postingId;
    const company = job.companyId;
    if (hidden.has(id) || marking.has(id) || answers.has(id)) return;
    const label = `Marked ${job.companyName} reached.`;
    write({
      id: `job-done:${id}`,
      label,
      afterCommit: label,
      patches: [{ key: queryKeys.jobsMarks, apply: (m: Marks) => ({ ...m, done: [...m.done, id], reached: [...m.reached, company] }) }],
      commit: (o) =>
        client.markReached(id, o).then(async (res) => {
          setAnswers((a) => new Map(a).set(id, res.text));
          announce(res.text);
          void queryClient.invalidateQueries({ queryKey: queryKeys.company(res.companyId) });
          await queryClient.invalidateQueries({ queryKey: queryKeys.jobsAll });
        }),
    });
  };

  /** "N new": the waiting rows show, the list goes to its top, and this counts as a visit. */
  const showNew = () => {
    if (scroller.current) scroller.current.scrollTop = 0;
    setAtTop(true);
    setActive(null);
    markOpened();
    announce(`${shown.waiting.toLocaleString('en-US')} new at the top.`);
  };

  const asOf = last?.asOf;
  const item = (j: JobRow) => (
    <JobItem
      key={j.postingId}
      job={j}
      asOf={asOf ?? j.firstSeenAt}
      current={j.postingId === current?.postingId}
      fresh={isNew(j, since)}
      reachedOn={reachedNow.has(j.companyId) && asOf ? formatMonthDay(asOf) : j.contactedOn ? formatDateMonthDay(j.contactedOn) : null}
      answer={answers.get(j.postingId)}
      marking={marking.has(j.postingId)}
      onSelect={() => setActive(j.postingId)}
      onPass={() => pass(j)}
      onReached={() => markCompanyReached(j)}
    />
  );

  const open = current !== null;
  const bindings: Binding[] = [
    { keys: 'arrowdown', label: 'Next job', group: GROUP, inInputs: true, enabled: open, run: () => move(1) },
    { keys: 'arrowup', label: 'Previous job', group: GROUP, inInputs: true, enabled: open, run: () => move(-1) },
    { keys: 'enter', label: 'Open the posting', group: GROUP, enabled: open, run: () => current && openExternal(current.url) },
    { keys: 'x', label: 'Pass', group: GROUP, enabled: open, run: () => current && pass(current) },
    { keys: 'a', label: 'Mark the company reached', group: GROUP, enabled: open, run: () => current && markCompanyReached(current) },
    { keys: 'c', label: 'Check now', group: GROUP, run: check.ask },
  ];
  useHotkeys(bindings);

  const on = { window: windowDays !== 'any', q: q !== '', size: size !== 'any', notContacted };
  const other: JobsTab = tab === 'your_level' ? 'higher' : 'your_level';
  const otherCount = counts?.[other] ?? 0;
  const headline = total === undefined || stale ? 'Loading the jobs' : `${total.toLocaleString('en-US')} ${levelText(tab)}${windowText(windowDays)}`;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="grid flex-none gap-3 border-b px-6 pt-5 pb-3">
        <PageHeader
          title="Jobs"
          summary={
            <>
              <p>{headline}</p>
              <OutreachLine />
              {check.line ? <CheckLineView line={check.line} /> : null}
            </>
          }
          aside={<CheckNowButton onClick={check.ask} />}
        />
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
            <span className="inline-flex items-center gap-1"><Kbd>A</Kbd> mark reached</span>
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

      <div ref={scroller} data-jobs-scroll className="relative min-h-0 flex-1 overflow-auto" onScroll={(e) => setAtTop(e.currentTarget.scrollTop <= TOP_PX)}>
        {shown.waiting > 0 ? (
          // Zero height and sticky: the button floats over the rows and moves none of them.
          <div className="pointer-events-none sticky top-0 z-10 h-0">
            <div className="flex justify-center pt-2">
              <Button type="button" size="sm" className="pointer-events-auto h-7 gap-1.5 rounded-full px-3 text-xs shadow-sm max-sm:h-11" title="Show the new jobs at the top" onClick={showNew}>
                <ArrowUp aria-hidden className="size-3.5" />
                {`${shown.waiting.toLocaleString('en-US')} new`}
              </Button>
            </div>
          </div>
        ) : null}
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
          <div ref={list} aria-busy={stale} className={cn(stale && 'opacity-60 transition-opacity')}>
            {dated.length > 0 ? <ul aria-label="Jobs">{dated.map(item)}</ul> : null}
            {undated.length > 0 ? (
              <section aria-labelledby="date-unknown-h">
                <div className="grid gap-0.5 border-b px-6 pt-6 pb-3">
                  <h2 id="date-unknown-h" className="text-sm font-semibold">
                    Date unknown
                  </h2>
                  <p className="text-[13px] text-muted-foreground">These boards give no posting date, and Knock found these jobs on its first read.</p>
                </div>
                <ul aria-label="Date unknown">{undated.map(item)}</ul>
              </section>
            ) : null}
          </div>
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
