import { useState } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router';
import { Ban, Search } from 'lucide-react';
import { pollMs, queryKeys, useClient, type NoGoReason, type NoGoView, type ShellSummary } from '@/api';
import { EmptyState, QueryState } from '@/components/state-views';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { useHotkeys } from '@/keyboard/hotkeys';
import { useListNav } from '@/keyboard/list-nav';
import { useUrlState } from '@/lib/url-state';
import { usePendingWrite, useViewQuery, type Patch } from '@/pending/pending';
import { LiftDialog } from './lift-dialog';
import {
  CompaniesTable,
  entryCompanyId,
  entryId,
  entryName,
  PeopleTable,
  reasonLabel,
  type CompanyEntry,
  type EntryActions,
} from './no-go-table';

type Tab = 'companies' | 'people';
type ReasonFilter = 'all' | NoGoReason | 'removed';

const PATH: Record<Tab, string> = { companies: '/no-go', people: '/no-go/people' };

function reasonMatches(row: CompanyEntry, reason: ReasonFilter): boolean {
  if (reason === 'all') return true;
  return row.kind === 'removed' ? reason === 'removed' : row.reason === reason;
}

/** The words a row can be found by: its name, what it says, and where that came from. */
function textOf(row: CompanyEntry): string {
  if (row.kind === 'removed') return `${row.company.name} ${row.note}`;
  const who = row.target.kind === 'company' ? row.target.company.name : `${row.target.person.name} ${row.target.person.title} ${row.target.companyName}`;
  return `${who} ${row.quote} ${row.note} ${row.sourceRef}`;
}

/** The view without one row, and its reason count one lower, for the patch that applies at action time. */
function withoutEntry(view: NoGoView, row: CompanyEntry): NoGoView {
  const id = entryId(row);
  const bucket = row.kind === 'removed' ? 'removed' : row.target.kind === 'company' ? row.reason : null;
  return {
    ...view,
    companies: view.companies.filter((r) => entryId(r) !== id),
    people: view.people.filter((r) => entryId(r) !== id),
    countsByReason: view.countsByReason.map((c) => (c.reason === bucket ? { ...c, count: Math.max(0, c.count - 1) } : c)),
  };
}

function knownReasons(view: NoGoView | undefined): Set<string> {
  return new Set(['all', ...(view?.countsByReason.map((c) => c.reason) ?? [])]);
}

function TableSkeleton() {
  return (
    <div className="grid gap-2" aria-hidden>
      {Array.from({ length: 6 }, (_, i) => (
        <Skeleton key={i} className="h-9 w-full" />
      ))}
    </div>
  );
}

export default function NoGoPage() {
  const client = useClient();
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const query = useViewQuery(queryKeys.noGo, () => client.noGo(), { pollMs: pollMs.default });
  const write = usePendingWrite();

  const tab: Tab = pathname.startsWith(PATH.people) ? 'people' : 'companies';
  const [, setParams] = useSearchParams();
  const [reasonParam, setReason] = useUrlState('reason', 'all');
  const [q, setQ] = useUrlState('q', '');
  const [lifting, setLifting] = useState<CompanyEntry | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  // Each open gets a fresh dialog, so one landing during the last one's exit animation starts empty and can submit.
  const [opens, setOpens] = useState(0);

  const view = query.data;
  // A reason the list does not know (a hand-edited URL) reads as All reasons.
  const reason: ReasonFilter = knownReasons(view).has(reasonParam) ? (reasonParam as ReasonFilter) : 'all';
  const needle = q.trim().toLowerCase();
  const byText = (row: CompanyEntry) => needle === '' || textOf(row).toLowerCase().includes(needle);
  const companies = (view?.companies ?? []).filter((r) => reasonMatches(r, reason) && byText(r));
  const people = (view?.people ?? []).filter(byText);
  const rows: CompanyEntry[] = tab === 'companies' ? companies : people;

  const openCompany = (companyId: number) => navigate(`/companies/${companyId}`);
  const { selected, rowProps } = useListNav<string>({
    ids: rows.map(entryId),
    onOpen:
      tab === 'companies'
        ? (id) => {
            const row = rows.find((r) => entryId(r) === id);
            const companyId = row ? entryCompanyId(row) : null;
            if (companyId !== null) openCompany(companyId);
          }
        : undefined,
    announce: (id) => {
      const row = rows.find((r) => entryId(r) === id);
      return row ? entryName(row) : '';
    },
  });
  const current = rows.find((r) => entryId(r) === selected) ?? null;

  const goTab = (next: Tab) => {
    if (next === tab) return;
    // The selection and the reason chips belong to one tab; the text filter and the scenario carry over.
    const params = new URLSearchParams(search);
    params.delete('sel');
    params.delete('reason');
    const rest = params.toString();
    navigate({ pathname: PATH[next], search: rest ? `?${rest}` : '' });
  };

  /** The posting, the reply or the page the reason came from; else the company page. */
  const sourceOf = (row: CompanyEntry): (() => void) | null => {
    if (row.kind === 'no_go' && row.sourceUrl) {
      const url = row.sourceUrl;
      return () => window.open(url, '_blank', 'noopener');
    }
    const companyId = row.kind === 'no_go' && row.target.kind === 'person' ? null : entryCompanyId(row);
    return companyId === null ? null : () => openCompany(companyId);
  };

  const takeOff = (row: CompanyEntry) => {
    setLifting(row);
    setOpens((n) => n + 1);
    setDialogOpen(true);
  };

  const confirm = (row: CompanyEntry, why: string) => {
    const name = entryName(row);
    const patches: Patch[] = [
      { key: queryKeys.noGo, apply: (v: NoGoView) => withoutEntry(v, row) } satisfies Patch<NoGoView>,
      { key: queryKeys.shell, apply: (s: ShellSummary) => ({ ...s, noGoCount: Math.max(0, s.noGoCount - 1) }) } satisfies Patch<ShellSummary>,
    ];
    if (row.kind === 'removed') {
      const text = `${name} is back in the backlog.`;
      write({
        id: `restore:${row.company.id}`,
        label: text,
        afterCommit: text,
        patches,
        immediate: true,
        commit: (o) => client.restoreCompany(row.company.id, why, row.company.revision, o),
      });
      return;
    }
    const text = row.target.kind === 'company' ? `${name} is off the no-go list. It can be proposed again.` : `${name} is off the no-go list.`;
    write({
      id: `lift:${row.noGoId}`,
      label: text,
      afterCommit: text,
      patches,
      immediate: true,
      commit: (o) => client.liftNoGo(row.noGoId, why, o),
    });
  };

  const actions: EntryActions = {
    onOpenSource: (row) => sourceOf(row)?.(),
    onOpenCompany: openCompany,
    onTakeOff: takeOff,
  };

  const openSource = current ? sourceOf(current) : null;
  const group = 'No-go list';
  useHotkeys([
    { keys: '1', label: 'Companies', group, run: () => goTab('companies') },
    { keys: '2', label: 'People', group, run: () => goTab('people') },
    { keys: 'o', label: 'Open the source', group, enabled: openSource !== null, run: () => openSource?.() },
    {
      keys: 'backspace',
      label: 'Take off the list',
      group,
      enabled: current !== null,
      run: () => {
        if (current) takeOff(current);
      },
    },
  ]);

  const filtered = needle !== '' || (tab === 'companies' && reason !== 'all');
  const clearFilters = () => {
    // One update for both: each url-state setter builds from the params of this render, so two calls would overwrite each other.
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete('q');
        next.delete('reason');
        return next;
      },
      { replace: true },
    );
  };
  const noMatch = (
    <EmptyState
      icon={Search}
      title="Nothing matches this filter."
      action={
        filtered ? (
          <Button variant="outline" size="sm" onClick={clearFilters}>
            Clear the filter
          </Button>
        ) : undefined
      }
    />
  );

  return (
    <div className="px-6 pt-5 pb-12">
      <div className="mb-4">
        <h1 className="text-xl font-semibold tracking-tight">No-go list</h1>
        <p className="mt-0.5 text-[13px] text-muted-foreground">
          Every company and person kept out, why, and where that came from. Nothing here is proposed or emailed.
        </p>
      </div>

      <QueryState
        query={query}
        skeleton={<TableSkeleton />}
        isEmpty={(v) => v.companies.length === 0 && v.people.length === 0}
        empty={
          <EmptyState icon={Ban} title="Nothing is on the no-go list.">
            Companies and people kept out show here, with why and where that came from.
          </EmptyState>
        }
      >
        {(v) => (
          <Tabs value={tab} onValueChange={(next) => goTab(next as Tab)} className="gap-3">
            <div className="flex flex-wrap items-center gap-3">
              <TabsList aria-label="No-go list">
                <TabsTrigger value="companies" className="px-2.5">
                  Companies <span className="text-xs text-muted-foreground tabular-nums">{v.companies.length}</span>
                </TabsTrigger>
                <TabsTrigger value="people" className="px-2.5">
                  People <span className="text-xs text-muted-foreground tabular-nums">{v.people.length}</span>
                </TabsTrigger>
              </TabsList>
              {tab === 'companies' ? (
                <ToggleGroup
                  type="single"
                  variant="outline"
                  size="sm"
                  spacing={1}
                  aria-label="Filter by reason"
                  value={reason}
                  onValueChange={(next) => {
                    if (next) setReason(next);
                  }}
                  className="flex-wrap"
                >
                  <ToggleGroupItem value="all" className="data-[state=on]:bg-secondary">
                    All reasons
                  </ToggleGroupItem>
                  {v.countsByReason.map((c) => (
                    <ToggleGroupItem key={c.reason} value={c.reason} className="data-[state=on]:bg-secondary">
                      {reasonLabel(c.reason)} <span className="text-muted-foreground tabular-nums">{c.count}</span>
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
              ) : null}
              <div className="relative ml-auto w-60">
                <Search aria-hidden className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input aria-label="Filter" placeholder="Filter" value={q} onChange={(e) => setQ(e.target.value)} className="h-8 pl-8" />
              </div>
            </div>

            <TabsContent value="companies" className="grid gap-2">
              {companies.length === 0 ? noMatch : <CompaniesTable rows={companies} rowProps={rowProps} actions={actions} />}
              <p className="text-xs text-muted-foreground">
                Postings hidden for asking for a 2027 graduation or current students are hidden one posting at a time. Their company stays
                eligible and is not listed here.
              </p>
            </TabsContent>
            <TabsContent value="people">
              {people.length === 0 ? (
                needle === '' ? (
                  <EmptyState icon={Ban} title="No one is on the no-go list." />
                ) : (
                  noMatch
                )
              ) : (
                <PeopleTable rows={people} rowProps={rowProps} actions={actions} />
              )}
            </TabsContent>
          </Tabs>
        )}
      </QueryState>

      <LiftDialog key={opens} row={lifting} open={dialogOpen} onOpenChange={setDialogOpen} onConfirm={confirm} />
    </div>
  );
}
