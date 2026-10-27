import { useEffect, useMemo, useRef, useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { ArrowRightLeft } from 'lucide-react';
import { pollMs, queryKeys, usePageClient, type BrowseCompany, type BrowseParams, type PlanRow } from '@/api';
import { Monogram } from '@/components/monogram';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Kbd } from '@/components/ui/kbd';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { useHotkeys, type Binding } from '@/keyboard/hotkeys';
import { cn } from '@/lib/utils';
import { CompanyRowBody } from './bench-drawer';
import { replaceable } from './picks';

const GROUP = 'Browse companies';
/** How long typing waits before the search is asked. */
const TYPING_MS = 180;

const FILTERS = [
  { key: 'levelRole', label: 'Role at my level' },
  { key: 'visaOk', label: 'Visa not a no' },
  { key: 'unworked', label: 'Not worked yet' },
] as const;
type FilterKey = (typeof FILTERS)[number]['key'];

/**
 * "Use X instead of...": today's picks that can give up their slot, numbered 1 to 9 (the digit picks it, the arrows
 * and Enter work too). Nothing here is a Not now: the pick goes back to More options with no cooldown.
 */
function Chooser({ company, picks, at, onAt, onChoose }: { company: BrowseCompany; picks: PlanRow[]; at: number; onAt: (i: number) => void; onChoose: (row: PlanRow) => void }) {
  // Focus moves here, so keys typed after Enter reach the chooser and not the search box.
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => box.current?.focus(), []);
  return (
    <div ref={box} tabIndex={-1} role="group" aria-label={`Use ${company.company.name} instead of`} className="grid gap-2 rounded-lg border bg-subtle px-3 py-3 outline-none">
      <p className="text-[13px]">
        <b className="font-medium">{`Use ${company.company.name} instead of...`}</b>{' '}
        <span className="text-muted-foreground">The pick goes back to More options. It is not a Not now, and it uses no room.</span>
      </p>
      {picks.length === 0 ? (
        <p className="text-[13px] text-muted-foreground">Every pick is reached, done or carried, so none can give up its slot today.</p>
      ) : (
        <ol className="grid gap-1">
          {picks.slice(0, 9).map((row, i) => (
            <li key={row.entryId}>
              <button
                type="button"
                data-choice={row.entryId}
                aria-current={i === at ? 'true' : undefined}
                aria-keyshortcuts={String(i + 1)}
                onClick={() => onChoose(row)}
                onMouseEnter={() => onAt(i)}
                className={cn(
                  'flex w-full items-center gap-2.5 rounded-md border bg-background px-2.5 py-1.5 text-left text-[13px] outline-none',
                  'focus-visible:ring-[3px] focus-visible:ring-ring/70',
                  i === at ? 'border-ring bg-muted/60' : 'border-border hover:bg-muted/40',
                )}
              >
                <Kbd aria-hidden>{i + 1}</Kbd>
                <Monogram name={row.company.name} className="size-6 text-xs" />
                <span className="grid min-w-0 flex-1">
                  <span className="truncate font-medium">{row.company.name}</span>
                  <span className="truncate text-muted-foreground">{row.queue?.whyToday ?? row.reason}</span>
                </span>
              </button>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

/**
 * Browse companies (F): every company the plan could pick, best ranked first, a page at a time, with a name search and
 * three filters. "Use instead of..." chooses which of today's picks gives up its slot; the swap is not a Not now and
 * uses no room, so it works when Add to today is off. A company he said Not now to or applied to is listed with the
 * reason and has no button. The list never names a company already on today's list. Nothing here hides a company.
 *
 * Keys: the arrows move through the rows (also from the search box), Enter opens "Use instead of...", a digit or Enter
 * chooses the pick, Escape closes the chooser and then the drawer.
 */
export function BrowseDrawer({ open, onOpenChange, rows, onSwap }: { open: boolean; onOpenChange: (open: boolean) => void; rows: PlanRow[]; onSwap: (pick: PlanRow, company: BrowseCompany) => boolean }) {
  const client = usePageClient('plan');
  const [text, setText] = useState('');
  const [q, setQ] = useState('');
  const [on, setOn] = useState<Record<FilterKey, boolean>>({ levelRole: false, visaOk: false, unworked: false });
  const [active, setActive] = useState<number | null>(null);
  const [choosing, setChoosing] = useState<number | null>(null);
  const [at, setAt] = useState(0);
  const list = useRef<HTMLUListElement>(null);
  const searchBox = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const id = setTimeout(() => setQ(text.trim()), TYPING_MS);
    return () => clearTimeout(id);
  }, [text]);

  const params: Omit<BrowseParams, 'after'> = { q, ...on };
  const query = useInfiniteQuery({
    queryKey: queryKeys.browse(params),
    queryFn: ({ pageParam }) => client.browse({ ...params, after: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (last) => last.nextAfter ?? undefined,
    enabled: open,
    // Like the bench: the pulse refetches it when anything is written, and this keeps ages and cooldowns fresh while it is open.
    refetchInterval: open ? pollMs.default : false,
  });

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

  // Closing puts the search, the choice and the chooser away; the filters stay.
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (!open) {
      setText('');
      setQ('');
      setActive(null);
      setChoosing(null);
    }
  }

  useEffect(() => {
    if (current) list.current?.querySelector(`[data-browse="${current.company.id}"]`)?.scrollIntoView?.({ block: 'nearest' });
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
    if (!chosen || !row) return;
    if (onSwap(row, chosen)) onOpenChange(false);
  };

  const inChooser = chosen !== null;
  const bindings: Binding[] = [
    { keys: 'arrowdown', label: 'Next company', group: GROUP, inDialogs: true, inInputs: true, enabled: open && !inChooser, run: () => move(1) },
    { keys: 'arrowup', label: 'Previous company', group: GROUP, inDialogs: true, inInputs: true, enabled: open && !inChooser, run: () => move(-1) },
    { keys: 'enter', label: 'Use instead of...', group: GROUP, inDialogs: true, inInputs: true, enabled: open && !inChooser && current !== null, run: () => current && askFor(current) },
    { keys: 'arrowdown', label: 'Next pick', group: GROUP, inDialogs: true, inInputs: true, enabled: open && inChooser, run: () => setAt((i) => Math.min(i + 1, Math.min(picks.length, 9) - 1)) },
    { keys: 'arrowup', label: 'Previous pick', group: GROUP, inDialogs: true, inInputs: true, enabled: open && inChooser, run: () => setAt((i) => Math.max(i - 1, 0)) },
    { keys: 'enter', label: 'Use instead of this pick', group: GROUP, inDialogs: true, inInputs: true, enabled: open && inChooser, run: () => choose(picks[at]) },
    ...Array.from({ length: 9 }, (_, i): Binding => ({ keys: String(i + 1), label: `Use instead of pick ${i + 1}`, group: GROUP, inDialogs: true, inInputs: true, enabled: open && inChooser && i < picks.length, run: () => choose(picks[i]) })),
  ];
  useHotkeys(bindings);

  const description = query.data
    ? `${total ?? companies.length} ${total === 1 ? 'company' : 'companies'}, best ranked first. A swap uses no room this week.`
    : 'Loading the companies.';

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-[480px] gap-0 sm:max-w-[480px]"
        onEscapeKeyDown={(e) => {
          // One step back: the chooser first, then the drawer.
          if (inChooser) {
            e.preventDefault();
            setChoosing(null);
            searchBox.current?.focus();
          }
        }}
      >
        <SheetHeader className="gap-3 border-b">
          <div className="grid gap-1">
            <SheetTitle className="flex items-center gap-2">
              Browse companies
              <Kbd aria-hidden>F</Kbd>
            </SheetTitle>
            <SheetDescription>{description}</SheetDescription>
          </div>
          <Input
            ref={searchBox}
            type="search"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Search by name"
            aria-label="Search companies by name"
            autoComplete="off"
            className="h-8"
          />
          <div role="group" aria-label="Filters" className="flex flex-wrap gap-1.5">
            {FILTERS.map((f) => (
              <Button
                key={f.key}
                type="button"
                variant={on[f.key] ? 'secondary' : 'outline'}
                size="sm"
                aria-pressed={on[f.key]}
                className="h-7 px-2.5 text-xs"
                onClick={() => setOn((o) => ({ ...o, [f.key]: !o[f.key] }))}
              >
                {f.label}
              </Button>
            ))}
          </div>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {query.isError ? (
            <p role="alert" className="px-4 py-6 text-sm">
              The companies did not load. Nothing was changed.{' '}
              <button type="button" className="underline underline-offset-3" onClick={() => void query.refetch()}>
                Try again
              </button>
            </p>
          ) : query.data && companies.length === 0 ? (
            <p className="px-4 py-6 text-sm text-muted-foreground">
              {q || on.levelRole || on.visaOk || on.unworked ? 'No company matches. Clear the search or a filter.' : 'Every company is on today\'s list or has a wave out.'}
            </p>
          ) : (
            <ul ref={list} aria-label="Companies" className="grid">
              {companies.map((c) => {
                const isCurrent = c.company.id === current?.company.id;
                return (
                  <li
                    key={c.company.id}
                    data-browse={c.company.id}
                    aria-current={isCurrent ? 'true' : undefined}
                    onClick={() => setActive(c.company.id)}
                    className={cn('grid gap-2 border-b border-l-2 px-4 py-3 last:border-b-0', isCurrent ? 'border-l-ring bg-muted/40' : 'border-l-transparent')}
                  >
                    <CompanyRowBody
                      b={c}
                      action={
                        c.blocked ? null : (
                          <Button type="button" variant="outline" size="sm" className="h-7 flex-none gap-1.5 px-2.5 text-xs" aria-keyshortcuts={isCurrent ? 'enter' : undefined} onClick={() => askFor(c)}>
                            <ArrowRightLeft aria-hidden />
                            Use instead of...
                            {isCurrent ? <Kbd aria-hidden>Enter</Kbd> : null}
                          </Button>
                        )
                      }
                      footer={
                        <>
                          {c.blocked ? <p className="text-[13px] text-muted-foreground">{c.blocked}</p> : null}
                          {c.worked && !c.blocked ? <p className="text-[13px] text-muted-foreground">You reached this company before.</p> : null}
                          {chosen?.company.id === c.company.id ? <Chooser company={c} picks={picks} at={at} onAt={setAt} onChoose={choose} /> : null}
                        </>
                      }
                    />
                  </li>
                );
              })}
            </ul>
          )}
          {query.hasNextPage ? (
            <div className="grid justify-items-center border-t px-4 py-3">
              <Button type="button" variant="ghost" size="sm" className="h-7 px-2.5 text-xs" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
                {query.isFetchingNextPage ? 'Loading' : 'Show more'}
              </Button>
            </div>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}
