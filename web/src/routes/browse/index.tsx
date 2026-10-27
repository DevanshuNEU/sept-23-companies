import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router';
import { useInfiniteQuery } from '@tanstack/react-query';
import { ArrowRightLeft, Briefcase, Building, Globe } from 'lucide-react';
import { pollMs, queryKeys, usePageClient, type BrowseCompany, type BrowseParams, type PlanRow } from '@/api';
import { EmailChip } from '@/components/email-chip';
import { FitChip } from '@/components/fit-chip';
import { Monogram } from '@/components/monogram';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Kbd } from '@/components/ui/kbd';
import { useHotkeys, type Binding } from '@/keyboard/hotkeys';
import { safeHref } from '@/lib/safe-href';
import { cn } from '@/lib/utils';
import { useViewQuery } from '@/pending/pending';
import { FreshTag } from '@/routes/today/why-today';
import { replaceable } from '@/routes/today/picks';
import { usePlanActions } from '@/routes/today/use-plan-actions';
import { Chooser } from './chooser';

const GROUP = 'Browse companies';
/** How long typing waits before the search is asked. */
const TYPING_MS = 180;
/** Companies per page: the API's most. */
const PAGE = 50;

const FILTERS = [
  { key: 'levelRole', label: 'Role at my level', hint: 'Has an open role at your level' },
  { key: 'visaOk', label: 'Visa not a no', hint: 'Sponsorship is not marked no' },
  { key: 'unworked', label: 'Not worked yet', hint: 'You have not reached or applied to it' },
] as const;
type FilterKey = (typeof FILTERS)[number]['key'];

const COLUMNS = ['Rank', 'Company', 'Fit', 'Why it ranked', 'Open role', 'Size and stage', 'Visa', 'Email', 'Research', 'Action'] as const;

const LINK =
  'inline-flex items-center gap-1 rounded-sm text-xs text-muted-foreground underline decoration-foreground/30 underline-offset-3 outline-none hover:text-foreground hover:decoration-foreground focus-visible:ring-[3px] focus-visible:ring-ring/70';

function Research({ c }: { c: BrowseCompany }) {
  const q = c.queue;
  const items = [
    { href: safeHref(q.careersUrl), label: 'Careers', icon: Briefcase },
    { href: safeHref(q.websiteUrl), label: 'Website', icon: Globe },
    { href: safeHref(q.linkedinCompanyUrl), label: 'LinkedIn page', icon: Building },
  ];
  return (
    <div role="group" aria-label="Research" className="grid gap-1">
      {items.map(({ href, label, icon: Icon }) =>
        href ? (
          <a key={label} href={href} target="_blank" rel="noopener noreferrer" className={LINK}>
            <Icon aria-hidden className="size-3" />
            {label}
          </a>
        ) : null,
      )}
    </div>
  );
}

function OpenRole({ c }: { c: BrowseCompany }) {
  const o = c.queue.opening;
  if (!o) return <span className="text-muted-foreground">No open role</span>;
  const href = safeHref(o.url);
  return (
    <div className="grid gap-0.5">
      {href ? (
        <a href={href} target="_blank" rel="noopener noreferrer" className="w-fit rounded-sm font-medium underline decoration-foreground/30 underline-offset-3 outline-none hover:decoration-foreground focus-visible:ring-[3px] focus-visible:ring-ring/70">
          {o.title}
        </a>
      ) : (
        <span className="font-medium">{o.title}</span>
      )}
      <span className="text-muted-foreground">{[o.level, o.age].filter(Boolean).join(', ')}</span>
      {o.appliedNote ? <span className="text-muted-foreground">{o.appliedNote}</span> : null}
    </div>
  );
}

/**
 * Browse companies (sidebar, or F on Today; #120): every company the plan could pick, best ranked first, in one table
 * with a name search and three filters. "Use instead of..." chooses which of today's picks gives up its slot; the swap is
 * not a Not now and uses no room, so it works when Add to today is off. A company he said Not now to or applied to is
 * listed with the reason and has no button. The list never names a company already on today's list. Nothing here hides
 * a company.
 *
 * Keys: the arrows move through the rows (also from the search box), Enter opens "Use instead of...", a digit or Enter
 * chooses the pick, Escape closes the chooser.
 */
export default function BrowsePage() {
  const client = usePageClient('plan');
  const plan = useViewQuery(queryKeys.plan, () => client.plan(), { pollMs: pollMs.default });
  const actions = usePlanActions();
  const [text, setText] = useState('');
  const [q, setQ] = useState('');
  const [on, setOn] = useState<Record<FilterKey, boolean>>({ levelRole: false, visaOk: false, unworked: false });
  const [active, setActive] = useState<number | null>(null);
  const [choosing, setChoosing] = useState<number | null>(null);
  const [at, setAt] = useState(0);
  const body = useRef<HTMLTableSectionElement>(null);
  const searchBox = useRef<HTMLInputElement>(null);

  // The search box has focus on arrival, so the arrows and Enter work at once (a focused sidebar link would take Enter).
  useEffect(() => searchBox.current?.focus(), []);

  useEffect(() => {
    const id = setTimeout(() => setQ(text.trim()), TYPING_MS);
    return () => clearTimeout(id);
  }, [text]);

  const params: Omit<BrowseParams, 'after'> = { q, ...on, limit: PAGE };
  const query = useInfiniteQuery({
    queryKey: queryKeys.browse(params),
    queryFn: ({ pageParam }) => client.browse({ ...params, after: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (last) => last.nextAfter ?? undefined,
    // Like the bench: the pulse refetches it when anything is written, and this keeps ages and cooldowns fresh.
    refetchInterval: pollMs.default,
  });

  const rows = useMemo<PlanRow[]>(() => plan.data?.rows ?? [], [plan.data]);
  const onToday = useMemo(() => new Set(rows.map((r) => r.company.id)), [rows]);
  const companies = useMemo(() => {
    const seen = new Set<number>();
    return (query.data?.pages ?? []).flatMap((p) => p.companies).filter((c) => {
      if (onToday.has(c.company.id) || seen.has(c.company.id)) return false;
      seen.add(c.company.id);
      return true;
    });
  }, [query.data, onToday]);
  const total = query.data?.pages.at(-1)?.total;
  const picks = replaceable(rows);

  const current = companies.find((c) => c.company.id === active) ?? companies[0] ?? null;
  const chosen = choosing === null ? null : (companies.find((c) => c.company.id === choosing) ?? null);
  const index = current ? companies.indexOf(current) : -1;

  // The company he is choosing for left the list (it was swapped in, or the list changed): put the chooser away.
  if (choosing !== null && chosen === null) setChoosing(null);

  useEffect(() => {
    if (current) body.current?.querySelector(`[data-browse="${current.company.id}"]`)?.scrollIntoView?.({ block: 'nearest' });
  }, [current?.company.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const move = (step: 1 | -1) => {
    const next = companies[Math.min(Math.max(index + step, 0), companies.length - 1)];
    if (next) setActive(next.company.id);
  };
  const askFor = (c: BrowseCompany) => {
    if (c.blocked) return;
    setActive(c.company.id);
    setChoosing(c.company.id);
    setAt(0);
  };
  const choose = (row: PlanRow | undefined) => {
    if (chosen && row) actions.swapPick(row, chosen);
  };

  const inChooser = chosen !== null;
  const bindings: Binding[] = [
    { keys: 'arrowdown', label: 'Next company', group: GROUP, inInputs: true, enabled: !inChooser, run: () => move(1) },
    { keys: 'arrowup', label: 'Previous company', group: GROUP, inInputs: true, enabled: !inChooser, run: () => move(-1) },
    { keys: 'enter', label: 'Use instead of...', group: GROUP, inInputs: true, enabled: !inChooser && current !== null, run: () => current && askFor(current) },
    { keys: 'arrowdown', label: 'Next pick', group: GROUP, inInputs: true, enabled: inChooser, run: () => setAt((i) => Math.min(i + 1, Math.min(picks.length, 9) - 1)) },
    { keys: 'arrowup', label: 'Previous pick', group: GROUP, inInputs: true, enabled: inChooser, run: () => setAt((i) => Math.max(i - 1, 0)) },
    { keys: 'enter', label: 'Use instead of this pick', group: GROUP, inInputs: true, enabled: inChooser, run: () => choose(picks[at]) },
    {
      keys: 'escape',
      label: 'Close the list of picks',
      group: GROUP,
      enabled: inChooser,
      run: () => {
        setChoosing(null);
        searchBox.current?.focus();
      },
    },
    ...Array.from({ length: 9 }, (_, i): Binding => ({ keys: String(i + 1), label: `Use instead of pick ${i + 1}`, group: GROUP, inInputs: true, enabled: inChooser && i < picks.length, run: () => choose(picks[i]) })),
  ];
  useHotkeys(bindings);

  const any = q !== '' || on.levelRole || on.visaOk || on.unworked;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="grid flex-none gap-3 border-b px-6 pt-5 pb-3">
        <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-1">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Browse companies</h1>
            <p className="mt-0.5 text-[13px] text-muted-foreground">
              Every company Knock could pick, best ranked first. Use one instead of a pick on Today. A swap uses no room this week.
            </p>
          </div>
          <p className="text-[13px] text-muted-foreground tabular-nums" aria-live="polite">
            {query.data ? `Showing ${companies.length} of ${total ?? companies.length} ${total === 1 ? 'company' : 'companies'}` : 'Loading the companies'}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            ref={searchBox}
            type="search"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Search by company name"
            aria-label="Search companies by name"
            autoComplete="off"
            className="h-8 w-72"
          />
          <div role="group" aria-label="Filters" className="flex flex-wrap gap-1.5">
            {FILTERS.map((f) => (
              <Button
                key={f.key}
                type="button"
                variant={on[f.key] ? 'secondary' : 'outline'}
                size="sm"
                aria-pressed={on[f.key]}
                title={f.hint}
                className="h-8 px-3 text-xs"
                onClick={() => setOn((o) => ({ ...o, [f.key]: !o[f.key] }))}
              >
                {f.label}
              </Button>
            ))}
          </div>
          <p className="ml-auto flex items-center gap-3 text-xs text-muted-foreground" aria-hidden>
            <span className="inline-flex items-center gap-1"><Kbd>↑</Kbd><Kbd>↓</Kbd> move</span>
            <span className="inline-flex items-center gap-1"><Kbd>Enter</Kbd> use instead of...</span>
            <span className="inline-flex items-center gap-1"><Kbd>Esc</Kbd> cancel</span>
          </p>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-auto">
        {query.isError ? (
          <p role="alert" className="px-6 py-6 text-sm">
            The companies did not load. Nothing was changed.{' '}
            <button type="button" className="underline underline-offset-3" onClick={() => void query.refetch()}>
              Try again
            </button>
          </p>
        ) : query.data && companies.length === 0 ? (
          <p className="px-6 py-6 text-sm text-muted-foreground">{any ? 'No company matches. Clear the search or a filter.' : "Every company is on today's list or has a wave out."}</p>
        ) : (
          <table aria-label="Companies" className="w-full min-w-[1240px] border-separate border-spacing-0 text-[13px]">
            <thead className="sticky top-0 z-10 bg-background">
              <tr>
                {COLUMNS.map((name) => (
                  <th key={name} scope="col" className={cn('border-b px-3 py-2 text-left text-xs font-medium text-muted-foreground first:pl-6 last:pr-6', name === 'Rank' && 'w-14 text-right')}>
                    {name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody ref={body}>
              {companies.map((c) => {
                const isCurrent = c.company.id === current?.company.id;
                const cell = 'border-b px-3 py-3 align-top first:pl-6 last:pr-6';
                return (
                  <Fragment key={c.company.id}>
                    <tr
                      data-browse={c.company.id}
                      aria-current={isCurrent ? 'true' : undefined}
                      onClick={() => setActive(c.company.id)}
                      className={cn(isCurrent ? 'bg-muted/40 shadow-[inset_2px_0_0_var(--ring)]' : 'hover:bg-muted/20')}
                    >
                      <td className={cn(cell, 'text-right text-muted-foreground tabular-nums')}>{c.position}</td>
                      <td className={cn(cell, 'w-[230px]')}>
                        <div className="flex items-start gap-2.5">
                          <Monogram name={c.company.name} className="mt-0.5 size-7 text-[13px]" />
                          <div className="grid min-w-0 gap-0.5">
                            <Link to={`/companies/${c.company.id}`} className="w-fit rounded-sm font-medium outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/70">
                              {c.company.name}
                            </Link>
                            <FreshTag opening={c.queue.opening} />
                            {c.worked && !c.blocked ? <span className="text-xs text-muted-foreground">You reached this company before.</span> : null}
                          </div>
                        </div>
                      </td>
                      <td className={cn(cell, 'w-[84px] whitespace-nowrap')}>{c.queue.judgeFit !== null ? <FitChip fit={c.queue.judgeFit} /> : <span className="text-xs text-muted-foreground">Not judged</span>}</td>
                      <td className={cn(cell, 'w-[260px]')}>
                        <p>{c.queue.whyToday}</p>
                        <p className="mt-0.5 text-muted-foreground">{c.queue.reasons.filter((r) => r !== c.queue.whyToday).slice(0, 2).join('. ')}</p>
                      </td>
                      <td className={cn(cell, 'w-[230px]')}>
                        <OpenRole c={c} />
                      </td>
                      <td className={cn(cell, 'w-[140px]')}>{c.queue.sizeLine ?? <span className="text-muted-foreground">Size not known</span>}</td>
                      <td className={cn(cell, 'w-[170px]')}>{c.queue.sponsorshipLine}</td>
                      <td className={cell}>
                        <EmailChip fit={c.queue.emailFit} />
                      </td>
                      <td className={cn(cell, 'w-[120px] whitespace-nowrap')}>
                        <Research c={c} />
                      </td>
                      <td className={cn(cell, 'w-[190px]')}>
                        {c.blocked ? (
                          <p className="text-xs text-muted-foreground">{c.blocked}</p>
                        ) : (
                          <Button type="button" variant="outline" size="sm" className="h-7 gap-1.5 px-2.5 text-xs" aria-keyshortcuts={isCurrent ? 'enter' : undefined} onClick={() => askFor(c)}>
                            <ArrowRightLeft aria-hidden />
                            Use instead of...
                          </Button>
                        )}
                      </td>
                    </tr>
                    {chosen?.company.id === c.company.id ? (
                      <tr>
                        <td colSpan={COLUMNS.length} className="border-b bg-muted/20 px-6 py-3">
                          <Chooser company={c} picks={picks} at={at} onAt={setAt} onChoose={choose} />
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
        {query.hasNextPage ? (
          <div className="grid justify-items-center border-t px-6 py-4">
            <Button type="button" variant="outline" size="sm" className="h-8 px-3 text-xs" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
              {query.isFetchingNextPage ? 'Loading' : 'Show more'}
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
