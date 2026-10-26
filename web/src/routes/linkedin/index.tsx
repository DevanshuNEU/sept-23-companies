import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { toast } from 'sonner';
import { Search, Users } from 'lucide-react';
import {
  pollMs,
  queryKeys,
  usePageClient,
  type Id,
  type LinkedInGroup,
  type LinkedInPerson,
  type LinkedInStep,
  type LinkedInView,
  type ShellSummary,
} from '@/api';
import { ReplyKindDialog, type LoggedReply } from '@/components/reply-kind-dialog';
import { EmptyState, QueryState } from '@/components/state-views';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useHotkeys, type Binding } from '@/keyboard/hotkeys';
import { useListNav } from '@/keyboard/list-nav';
import { replyKindStatus } from '@/lib/status';
import { useUrlState } from '@/lib/url-state';
import { usePendingWrite, useViewQuery, type Patch } from '@/pending/pending';
import { openExternal } from '@/lib/safe-href';
import { clampPage, pageGroups, pageOfPerson, peopleRange } from './linkedin-pages';
import { NEXT, nextStep, ORDER, STEP_WORD } from './linkedin-steps';
import { LinkedInTable, type ShownGroup } from './linkedin-table';

type PersonTab = LinkedInPerson['tab'];
type Tab = PersonTab | 'all';

const TABS: { value: Tab; label: string; count: (c: LinkedInView['counts']) => number }[] = [
  { value: 'to_reach', label: 'To reach', count: (c) => c.toReach },
  { value: 'waiting', label: 'Waiting', count: (c) => c.waiting },
  { value: 'replied', label: 'Replied', count: (c) => c.replied },
  { value: 'all', label: 'All', count: (c) => c.all },
];
const isTab = (v: string): v is Tab => TABS.some((t) => t.value === v);

const COUNT_FIELD = { to_reach: 'toReach', waiting: 'waiting', replied: 'replied' } as const satisfies Record<PersonTab, keyof LinkedInView['counts']>;

const EMPTY: Record<Tab, { title: string; text: string }> = {
  to_reach: { title: 'Nobody to reach.', text: 'People past the email budget land here when Claude adds them.' },
  waiting: { title: 'Nobody waiting.', text: 'People you requested or messaged wait here until they answer.' },
  replied: { title: 'No replies yet.', text: 'People who answer on LinkedIn land here.' },
  all: { title: 'Nobody on the LinkedIn list.', text: 'People past the email budget land here when Claude adds them.' },
};

// Said no and Asked to stop commit at once (they add a no-go).
const AT_ONCE = new Set<LoggedReply>(['said_no', 'asked_to_stop']);

function loggedText(name: string, step: LinkedInStep, kind?: LoggedReply): string {
  if (step === 'request_sent') return `Logged: request sent to ${name}.`;
  if (step === 'accepted') return `Logged: ${name} accepted.`;
  if (step === 'message_sent') return `Logged: message sent to ${name}.`;
  return kind ? `Logged: reply from ${name} (${replyKindStatus[kind].label}).` : `Logged: reply from ${name}.`;
}

/**
 * The tab a person shows under while a logged step waits to commit. It mirrors what the server will
 * store, so the row and the counts move with the action (a reply moves them to Replied, a first step
 * moves them out of To reach); the refetch after the commit replaces it with the stored value.
 */
const tabAfter = (from: PersonTab, step: LinkedInStep): PersonTab =>
  step === 'replied' ? 'replied' : from === 'to_reach' ? 'waiting' : from;

/** Lays one logged step over the view. A step already there (the commit has landed) changes nothing. */
function withStep(v: LinkedInView, personId: Id, step: LinkedInStep, kind: LoggedReply | undefined): LinkedInView {
  const p = v.groups.flatMap((g) => g.people).find((x) => x.person.id === personId);
  if (!p || p.steps.some((s) => s.step === step)) return v;
  const from = p.tab;
  const to = tabAfter(from, step);
  const counts = { ...v.counts };
  if (from !== to) {
    counts[COUNT_FIELD[from]] -= 1;
    counts[COUNT_FIELD[to]] += 1;
  }
  return {
    ...v,
    counts,
    groups: v.groups.map((g) => ({
      ...g,
      people: g.people.map((x) =>
        x.person.id === personId
          ? { ...x, tab: to, steps: [...x.steps, { step, at: v.asOf }], replyKind: kind ?? x.replyKind }
          : x,
      ),
    })),
  };
}

const matches = (q: string, g: LinkedInGroup, p: LinkedInPerson) =>
  [p.person.name, p.person.title, g.company.name].some((s) => s.toLowerCase().includes(q));

/**
 * The selected person stays in the tab they were seen in after a log moves them, until the selection
 * moves (Review focus 4). State adjusted during render, as in useStableOrder.
 */
function useKeptInTab(selected: Id | null, tab: Tab, selectedTab: PersonTab | null): Id | null {
  const [kept, setKept] = useState<{ id: Id; tab: Tab } | null>(null);
  let next: { id: Id; tab: Tab } | null = null;
  if (selected !== null && selectedTab !== null) {
    if (tab === 'all' || selectedTab === tab) next = { id: selected, tab };
    else if (kept?.id === selected && kept.tab === tab) next = kept;
  }
  if (next?.id !== kept?.id || next?.tab !== kept?.tab) setKept(next);
  return next?.id ?? null;
}

function TableSkeleton() {
  return (
    <div className="flex flex-col gap-2 rounded-lg border p-3" aria-hidden>
      {Array.from({ length: 6 }, (_, i) => (
        <Skeleton key={i} className="h-9 w-full" />
      ))}
    </div>
  );
}

/** LinkedIn list (design 5.2): who to reach per company, which role to name, and a log of what happened. */
export default function LinkedInPage() {
  const client = usePageClient('linkedin');
  const navigate = useNavigate();
  const write = usePendingWrite();
  const query = useViewQuery(queryKeys.linkedin, () => client.linkedin(), { pollMs: pollMs.default });
  const view = query.data;

  const [tabRaw] = useUrlState('tab', 'to_reach');
  const tab: Tab = isTab(tabRaw) ? tabRaw : 'to_reach';
  const [filter] = useUrlState('q', '');
  const [sel] = useUrlState('sel', '');
  const [pageRaw, setPage] = useUrlState('page', '1');
  const [replyFor, setReplyFor] = useState<Id | null>(null);

  // A new tab or filter starts at page 1 with nothing selected. One write for all of it: two `useUrlState` writes in
  // one tick would each start from the same URL, and the second would drop the first.
  const [, setParams] = useSearchParams();
  const restart = (key: 'tab' | 'q', value: string, initial: string) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value === initial) next.delete(key);
        else next.set(key, value);
        next.delete('page');
        next.delete('sel');
        return next;
      },
      { replace: true },
    );
  const setTab = (v: string) => {
    if (v !== tab) restart('tab', v, 'to_reach');
  };
  const setFilter = (v: string) => restart('q', v, '');

  const all = (view?.groups ?? []).flatMap((group) => group.people.map((person) => ({ group, person })));
  const find = (id: Id | null) => (id === null ? undefined : all.find((x) => x.person.person.id === id));
  const selectedId = all.find((x) => String(x.person.person.id) === sel)?.person.person.id ?? null;
  const kept = useKeptInTab(selectedId, tab, find(selectedId)?.person.tab ?? null);

  const q = filter.trim().toLowerCase();
  const shown: ShownGroup[] = (view?.groups ?? [])
    .map((group) => ({
      group,
      people: group.people.filter(
        (p) => (tab === 'all' || p.tab === tab || p.person.id === kept) && (q === '' || matches(q, group, p)),
      ),
    }))
    .filter((g) => g.people.length > 0);
  // j and k walk every page in order, so the last row of a page leads to the first row of the next.
  const ids = shown.flatMap((g) => g.people.map((p) => p.person.id));
  const pages = pageGroups(shown);
  const urlPage = clampPage(pageRaw, pages.length);
  const firstOf = (n: number) => pages[n - 1]?.[0]?.people[0]?.person.id;

  const { selected, select, rowProps } = useListNav<Id>({
    ids,
    start: firstOf(urlPage),
    onOpen: (id) => {
      const hit = find(id);
      if (hit) navigate(`/companies/${hit.group.company.id}`);
    },
    announce: (id) => find(id)?.person.person.name ?? '',
  });
  const current = find(selected);

  // The page in sight is the one holding the selected row, so j and k carry the page with them, a `sel` in the URL
  // opens its page, and a log that keeps its row (useKeptInTab) keeps the page. With no row selected it is the URL's
  // page, held to the pages there are: when the last page empties, the one before it shows.
  const page = pageOfPerson(pages, selected) ?? urlPage;
  const loaded = view !== undefined;
  useEffect(() => {
    if (loaded && String(page) !== pageRaw) setPage(String(page));
  }, [loaded, page, pageRaw, setPage]);

  // Turning the page selects its first row, so the step keys always act on a row in sight.
  const turnTo = (n: number) => {
    const id = firstOf(n);
    if (id !== undefined) select(id);
  };

  // A new page starts at its top, unless k came back to the last row of the page before.
  const topRef = useRef<HTMLDivElement>(null);
  const lastPage = useRef(page);
  useEffect(() => {
    if (lastPage.current === page) return;
    lastPage.current = page;
    const row = selected !== null && selected !== firstOf(page) ? topRef.current?.querySelector('tbody tr[aria-selected="true"]') : null;
    if (row) row.scrollIntoView({ block: 'nearest' });
    else topRef.current?.scrollIntoView({ block: 'start' });
    // Only a new page scrolls; `selected` and `firstOf` are read as they are at that moment.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  /**
   * Only the person's next step can be logged. An earlier or the same step is already there; a later one would
   * skip a step, so it says what comes next. False means nothing is logged and a toast said why.
   */
  const canLog = (p: LinkedInPerson, step: LinkedInStep): boolean => {
    const next = nextStep(p);
    if (next === step) return true;
    if (next === null || p.steps.some((s) => s.step === step) || ORDER.indexOf(step) < ORDER.indexOf(next)) {
      toast(`Already logged: ${STEP_WORD[step]}`);
    } else {
      toast(`Next for ${p.person.name}: ${NEXT[next].label.toLowerCase()} (${NEXT[next].key}).`);
    }
    return false;
  };

  const log = (p: LinkedInPerson, step: LinkedInStep, kind?: LoggedReply) => {
    if (!canLog(p, step)) return;
    const name = p.person.name;
    const toReachDelta = p.tab === 'to_reach' && tabAfter(p.tab, step) !== 'to_reach' ? -1 : 0;
    const viewPatch: Patch<LinkedInView> = { key: queryKeys.linkedin, apply: (v) => withStep(v, p.person.id, step, kind) };
    // Also refetches the shell after the commit, so the sidebar and the no-go count catch up.
    const shellPatch: Patch<ShellSummary> = {
      key: queryKeys.shell,
      apply: (s) => ({ ...s, linkedinToReach: s.linkedinToReach + toReachDelta }),
    };
    const text = loggedText(name, step, kind);
    write({
      id: `linkedin:${p.person.id}:${step}`,
      label: text,
      afterCommit: text,
      immediate: kind !== undefined && AT_ONCE.has(kind),
      patches: [viewPatch, shellPatch],
      commit: (o) => (kind ? client.logLinkedIn(p.person.id, step, o, kind) : client.logLinkedIn(p.person.id, step, o)),
    });
    // A step that takes the person out of the tab he is working in (request sent leaves To reach, a reply leaves
    // Waiting) moves the selection to the next person, so the next key acts on someone still to do. The last one
    // looks back to the one before. Undo brings the person back; the selection stays where it moved.
    if (tab !== 'all' && p.tab === tab && tabAfter(p.tab, step) !== tab) {
      const at = ids.indexOf(p.person.id);
      const next = ids[at + 1] ?? ids[at - 1];
      if (next !== undefined) select(next);
    }
  };

  const askReply = (p: LinkedInPerson) => {
    if (canLog(p, 'replied')) setReplyFor(p.person.id);
  };

  const onLog = (p: LinkedInPerson, step: LinkedInStep) => (step === 'replied' ? askReply(p) : log(p, step));

  const copyRole = (g: LinkedInGroup) => {
    if (!g.role) {
      toast(`No role to name at ${g.company.name} yet.`);
      return;
    }
    const title = g.role.title;
    const failed = () => toast.error('Could not copy. Nothing was copied.');
    if (!('clipboard' in navigator)) return failed();
    navigator.clipboard.writeText(title).then(() => toast(`Copied: ${title}`), failed);
  };

  const openProfile = (p: LinkedInPerson) => {
    if (!openExternal(p.person.linkedinUrl)) toast(`No LinkedIn profile saved for ${p.person.name}.`);
  };

  const group = 'LinkedIn list';
  const on = current !== undefined;
  const onSelected = (fn: (hit: { group: LinkedInGroup; person: LinkedInPerson }) => void) => () => {
    if (current) fn(current);
  };
  const onRow: Binding[] = [
    { keys: 'c', label: 'Copy role title', group, enabled: on, run: onSelected((h) => copyRole(h.group)) },
    { keys: 'l', label: 'Open their profile', group, enabled: on, run: onSelected((h) => openProfile(h.person)) },
    { keys: 'r', label: 'Log request sent', group, enabled: on, run: onSelected((h) => log(h.person, 'request_sent')) },
    { keys: 'a', label: 'Log accepted', group, enabled: on, run: onSelected((h) => log(h.person, 'accepted')) },
    { keys: 'm', label: 'Log message sent', group, enabled: on, run: onSelected((h) => log(h.person, 'message_sent')) },
    { keys: 'y', label: 'Log reply', group, enabled: on, run: onSelected((h) => askReply(h.person)) },
  ];
  // These act on the selected person, so a key right after j, k or a click waits for that move to render.
  useHotkeys(onRow, { selection: selected });
  useHotkeys([
    ...TABS.map((t, i): Binding => ({ keys: String(i + 1), label: `${t.label} tab`, group, run: () => setTab(t.value) })),
    { keys: '[', label: 'Previous page', group, enabled: page > 1, run: () => turnTo(page - 1) },
    { keys: ']', label: 'Next page', group, enabled: page < pages.length, run: () => turnTo(page + 1) },
  ]);

  const replyPerson = find(replyFor)?.person ?? null;

  return (
    <div ref={topRef} className="max-w-[1360px] px-6 pt-5 pb-12">
      <div className="mb-4 flex items-end gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">LinkedIn list</h1>
          <p className="mt-0.5 text-[13px] text-muted-foreground">
            People past the email budget for their company, or with no email. Name the role, then log what happened.
          </p>
          <p className="mt-0.5 text-[13px] text-muted-foreground">
            Who to reach first each day is on{' '}
            <Link
              to="/today"
              className="rounded-sm text-foreground underline decoration-border underline-offset-3 outline-none hover:decoration-current focus-visible:ring-[3px] focus-visible:ring-ring/50"
            >
              Today&apos;s plan
            </Link>
            .
          </p>
        </div>
        {view ? (
          <p className="ml-auto text-sm text-muted-foreground">
            <b className="font-semibold text-foreground tabular-nums">{view.requestsThisWeek}</b> requests this week
          </p>
        ) : null}
      </div>

      <Tabs value={tab} onValueChange={(v) => setTab(v)} className="gap-3">
        <div className="flex items-center gap-3">
          <TabsList aria-label="LinkedIn status">
            {TABS.map((t) => (
              <TabsTrigger key={t.value} value={t.value} className="px-2.5">
                {t.label}
                {view ? <span className="text-xs text-muted-foreground tabular-nums">{t.count(view.counts)}</span> : null}
              </TabsTrigger>
            ))}
          </TabsList>
          <div className="relative ml-auto w-[280px]">
            <Search aria-hidden className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-muted-foreground" />
            <Input
              className="h-9 pl-8"
              placeholder="Filter people or companies"
              aria-label="Filter"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </div>
        </div>

        <TabsContent value={tab}>
          <QueryState
            query={query}
            skeleton={<TableSkeleton />}
            isEmpty={() => shown.length === 0}
            empty={
              <div className="rounded-lg border bg-card">
                {q !== '' && (view?.groups.length ?? 0) > 0 ? (
                  <EmptyState icon={Search} title={`Nobody matches "${filter.trim()}".`}>
                    Clear the filter to see everyone in this tab.
                  </EmptyState>
                ) : (
                  <EmptyState icon={Users} title={EMPTY[tab].title}>
                    {EMPTY[tab].text}
                  </EmptyState>
                )}
              </div>
            }
          >
            {(v) => (
              <LinkedInTable
                groups={pages[page - 1] ?? []}
                asOf={v.asOf}
                rowProps={rowProps}
                onLog={onLog}
                onCopy={copyRole}
                pageInfo={{
                  page,
                  count: pages.length,
                  ...peopleRange(pages, page),
                  onPrevious: () => turnTo(page - 1),
                  onNext: () => turnTo(page + 1),
                }}
              />
            )}
          </QueryState>
        </TabsContent>
      </Tabs>

      <p className="mt-3 text-xs text-muted-foreground">
        Logging only records what you did, so the company page shows the whole story. Nothing here touches LinkedIn.
      </p>

      <ReplyKindDialog
        from={replyPerson ? `${replyPerson.person.name} on LinkedIn` : null}
        onClose={() => setReplyFor(null)}
        onPick={(kind) => {
          if (replyPerson) log(replyPerson, 'replied', kind);
          setReplyFor(null);
        }}
      />
    </div>
  );
}
