import { useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router';
import { toast } from 'sonner';
import { Archive, CalendarX, Inbox, Terminal } from 'lucide-react';
import {
  pollMs,
  queryKeys,
  useClient,
  type DailyEntry,
  type Id,
  type JobRunSummary,
  type TodayView,
} from '@/api';
import { EmptyState, QueryState, StateBanner } from '@/components/state-views';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useHotkeys, type Binding } from '@/keyboard/hotkeys';
import { useListNav } from '@/keyboard/list-nav';
import { formatDayTime } from '@/lib/format';
import { useUrlState } from '@/lib/url-state';
import { useViewQuery } from '@/pending/pending';
import { openExternal } from '@/lib/safe-href';
import { useTodayActions } from './actions';
import { groupInOrder } from './grouping';
import { PreviewPanel } from './preview-panel';
import { ProposalsTable } from './proposals-table';
import { RemoveDialog } from './remove-dialog';
import { StillEmailingTable } from './still-emailing';

type List = 'proposed' | 'backlog';

const LONG_DATE = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', weekday: 'long', month: 'long', day: 'numeric' });
/** "Tuesday, November 10": the day of `asOf` in the app's zone. */
const longDate = (asOf: string) => LONG_DATE.format(new Date(asOf));

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const capacityText = (capacity: number) => `Today's limit carries ${capacity} companies. Unpick one first.`;

/** The block he pastes in the side terminal so Claude adds people for the company. */
function handoffOf(e: DailyEntry): string {
  const role = e.role ? `Role to name: ${e.role.title}.` : 'Role to name: none yet.';
  return `Add people for ${e.company.name} (company ${e.company.id}).\n${role}`;
}

async function copy(text: string, done: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast(done);
  } catch {
    toast.error('Copy failed. Nothing is on the clipboard. Try again, or select the text and copy it.');
  }
}

function CapItem({ k, children, sub }: { k: string; children: ReactNode; sub?: ReactNode }) {
  return (
    <div className="grid content-start gap-0.5 border-r px-4 py-3 last:border-r-0">
      <dt className="text-xs text-muted-foreground">{k}</dt>
      <dd className="text-xl leading-7 font-semibold tracking-tight tabular-nums">{children}</dd>
      {sub ? <dd className="text-xs text-muted-foreground">{sub}</dd> : null}
    </div>
  );
}

function Capacity({ t }: { t: TodayView }) {
  const held = t.stillEmailing.filter((s) => s.state === 'held').length;
  const paused = t.stillEmailing.filter((s) => s.state === 'paused').length;
  const stillSub = [held ? `${held} held by a reply` : '', paused ? `${paused} paused` : ''].filter(Boolean).join(', ');
  const atLevel = t.proposed.filter((e) => e.listGroup === 'level_role').length;
  return (
    <dl aria-label="Capacity" className="mb-5 grid grid-cols-4 overflow-hidden rounded-md border">
      <CapItem
        k="Picked today"
        sub={
          <span aria-hidden className="mt-1.5 flex gap-1">
            {Array.from({ length: t.capacity }, (_, i) => (
              <i key={i} data-on={i < t.picked} className="h-1.5 w-[18px] rounded-[2px] bg-muted data-[on=true]:bg-foreground" />
            ))}
          </span>
        }
      >
        {t.picked} <small className="text-[13px] font-normal tracking-normal text-muted-foreground">{`of ${t.capacity}`}</small>
      </CapItem>
      <CapItem k="Still emailing" sub={stillSub || undefined}>
        {t.stillEmailing.length}
      </CapItem>
      <CapItem k="Proposed" sub={atLevel ? `${atLevel} with a role at your level` : undefined}>
        {t.proposed.length}
      </CapItem>
      <CapItem k="Backlog" sub="Unpicked roll here at 08:00 tomorrow">
        {t.backlog.length}
      </CapItem>
    </dl>
  );
}

/** The shared stale banner when the daily list job failed or is late, with the job's error. */
function ListJobBanner({ job }: { job: JobRunSummary | null }) {
  if (!job) return null;
  if (job.status === 'failed') {
    return (
      <div className="mb-4">
        <StateBanner tone="warn" title={`Daily list failed ${formatDayTime(job.startedAt)}`}>
          {job.error || 'The job stopped without an error message.'}
        </StateBanner>
      </div>
    );
  }
  if (job.late) {
    return (
      <div className="mb-4">
        <StateBanner tone="warn" title="Daily list is late">
          {job.expectedNextAt ? `It was expected ${formatDayTime(job.expectedNextAt)}.` : 'It has not run on time.'}
        </StateBanner>
      </div>
    );
  }
  return null;
}

function SectionTitle({ title, count, right }: { title: string; count: number; right?: ReactNode }) {
  return (
    <div className="mt-6 mb-2 flex items-center gap-2">
      <h2 className="text-sm font-semibold">{title}</h2>
      <span className="text-sm text-muted-foreground tabular-nums">{count}</span>
      {right ? <div className="ml-auto">{right}</div> : null}
    </div>
  );
}

function TodaySkeleton() {
  return (
    <div aria-hidden className="grid gap-3">
      <Skeleton className="h-7 w-64" />
      <Skeleton className="h-4 w-[32rem]" />
      <div className="mt-2 grid grid-cols-4 gap-3">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-[76px]" />
        ))}
      </div>
      {Array.from({ length: 8 }, (_, i) => (
        <Skeleton key={i} className="h-9 w-full" />
      ))}
    </div>
  );
}

function TodayContent({ t, limitToday }: { t: TodayView; limitToday: number | undefined }) {
  const navigate = useNavigate();
  const actions = useTodayActions();
  const [tabRaw, setTab] = useUrlState('tab', 'proposed');
  const tab: List = tabRaw === 'backlog' ? 'backlog' : 'proposed';
  const [removing, setRemoving] = useState<DailyEntry | null>(null);

  const rows = tab === 'proposed' ? t.proposed : t.backlog;
  // The table lists rows group by group, so j and k walk that order, not the API's.
  const ids = groupInOrder(rows, (e) => e.listGroup).flatMap((g) => g.items.map((e) => e.company.id));
  const byId = new Map(rows.map((e) => [e.company.id, e]));
  const { selected, select, rowProps } = useListNav<number>({
    ids,
    onOpen: (id) => navigate(`/companies/${id}`),
    announce: (id) => byId.get(id)?.company.name ?? '',
  });
  const current = selected === null ? null : (byId.get(selected) ?? null);

  const atCapacity = t.picked >= t.capacity;
  const blockedText = atCapacity ? capacityText(t.capacity) : null;
  const picks = [...t.proposed, ...t.backlog].filter((e) => e.state === 'picked');

  /** After a row leaves the list (backlog, remove), the cursor moves to the next row, or the one before at the end. */
  const moveOn = (id: Id) => {
    const at = ids.indexOf(id);
    const next = ids[at + 1] ?? ids[at - 1];
    if (next !== undefined) select(next);
  };

  const toggle = (e: DailyEntry) => {
    if (e.state === 'picked') return actions.unpick(e);
    if (atCapacity) {
      toast(capacityText(t.capacity));
      return;
    }
    actions.pick(e);
  };
  const toBacklog = (e: DailyEntry) => {
    if (tab !== 'proposed' || e.state === 'backlog') return;
    moveOn(e.company.id);
    actions.backlog(e);
  };
  const copyPicks = () => {
    if (picks.length === 0) {
      toast('No picks yet. Press X on a company to pick it.');
      return;
    }
    void copy(picks.map(handoffOf).join('\n\n'), `Copied ${plural(picks.length, 'company', 'companies')} for the side terminal.`);
  };
  const copyTitle = (e: DailyEntry) => {
    if (e.role) void copy(e.role.title, `Copied: ${e.role.title}`);
  };
  const openPosting = (e: DailyEntry) => {
    if (e.role) openExternal(e.role.url);
  };

  const group = 'Today';
  const onRow: Binding[] = [
    { keys: 'x', label: 'Pick or unpick', group, enabled: current !== null, run: () => current && toggle(current) },
    {
      keys: 'b',
      label: 'Move to the backlog',
      group,
      enabled: current !== null && tab === 'proposed' && current.state !== 'backlog',
      run: () => current && toBacklog(current),
    },
    { keys: 'r', label: 'Remove with a reason', group, enabled: current !== null, run: () => setRemoving(current) },
    { keys: 'c', label: 'Copy role title', group, enabled: current?.role != null, run: () => current && copyTitle(current) },
    { keys: 'o', label: 'Open the posting', group, enabled: current?.role != null, run: () => current && openPosting(current) },
  ];
  // These act on the selected company, so a key right after j, k or a click waits for that move to render.
  useHotkeys(onRow, { selection: selected });
  useHotkeys([
    { keys: 'shift+c', label: 'Copy picks for Claude', group, run: copyPicks },
    { keys: '1', label: 'Proposed tab', group, run: () => setTab('proposed') },
    { keys: '2', label: 'Backlog tab', group, run: () => setTab('backlog') },
  ]);

  const tableActions = { onToggle: toggle, onBacklog: toBacklog, onRemove: (e: DailyEntry) => setRemoving(e) };
  const lede = `Pick up to ${t.capacity} companies.${
    limitToday === undefined
      ? ''
      : ` Today's limit of ${limitToday} emails carries that many new companies on top of follow-ups and day 2 and 3 sends.`
  }`;

  const proposedBody = t.notReady ? (
    <EmptyState
      icon={CalendarX}
      title={t.notReady.text}
      action={
        t.backlog.length > 0 ? (
          <Button variant="outline" size="sm" onClick={() => setTab('backlog')}>
            Open the backlog
          </Button>
        ) : undefined
      }
    />
  ) : t.proposed.length === 0 ? (
    <EmptyState icon={Inbox} title="No companies were proposed today.">
      The next list is built at 08:00 on weekdays.
    </EmptyState>
  ) : (
    <ProposalsTable entries={t.proposed} list="proposed" asOf={t.asOf} blockedText={blockedText} rowProps={rowProps} actions={tableActions} />
  );
  const backlogBody =
    t.backlog.length === 0 ? (
      <EmptyState icon={Archive} title="The backlog is empty.">
        Unpicked proposals roll here at the next 08:00 run.
      </EmptyState>
    ) : (
      <ProposalsTable entries={t.backlog} list="backlog" asOf={t.asOf} blockedText={blockedText} rowProps={rowProps} actions={tableActions} />
    );

  return (
    <div className="grid h-full min-h-0 grid-cols-[minmax(0,1fr)_380px]">
      <section aria-label="Today's companies" className="min-h-0 overflow-y-auto">
        <div className="max-w-[1360px] px-6 pt-5 pb-12">
          <div className="mb-4 flex items-end gap-4">
            <div>
              <h2 className="text-xl font-semibold tracking-tight">{longDate(t.asOf)}</h2>
              <p className="mt-0.5 text-[13px] text-muted-foreground">{lede}</p>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <Button variant="outline" onClick={copyPicks}>
                <Terminal aria-hidden />
                Copy picks for Claude
              </Button>
            </div>
          </div>

          <ListJobBanner job={t.listJob} />
          <Capacity t={t} />

          <SectionTitle title="Still emailing" count={t.stillEmailing.length} right={<span className="text-xs text-muted-foreground">Picked on earlier days</span>} />
          {t.stillEmailing.length === 0 ? (
            <div className="rounded-md border">
              <EmptyState icon={Inbox} title="No companies are being emailed.">
                Pick companies below to start.
              </EmptyState>
            </div>
          ) : (
            <StillEmailingTable rows={t.stillEmailing} />
          )}

          <Tabs value={tab} onValueChange={(v) => setTab(v === 'backlog' ? 'backlog' : 'proposed')} className="gap-0">
            <SectionTitle
              title={tab === 'proposed' ? 'Proposed' : 'Backlog'}
              count={rows.length}
              right={
                <TabsList aria-label="Proposal list" className="h-[34px]">
                  <TabsTrigger value="proposed" className="px-3 text-[13px]">
                    Proposed<span className="text-xs text-muted-foreground tabular-nums">{t.proposed.length}</span>
                  </TabsTrigger>
                  <TabsTrigger value="backlog" className="px-3 text-[13px]">
                    Backlog<span className="text-xs text-muted-foreground tabular-nums">{t.backlog.length}</span>
                  </TabsTrigger>
                </TabsList>
              }
            />
            <TabsContent value="proposed">{proposedBody}</TabsContent>
            <TabsContent value="backlog">{backlogBody}</TabsContent>
          </Tabs>
        </div>
      </section>

      <PreviewPanel
        entry={current}
        list={tab}
        asOf={t.asOf}
        handoff={current ? handoffOf(current) : ''}
        actions={{
          onToggle: () => current && toggle(current),
          onBacklog: () => current && toBacklog(current),
          onRemove: () => setRemoving(current),
          onCopyTitle: () => current && copyTitle(current),
          onCopyHandoff: () => current && void copy(handoffOf(current), 'Copied the handoff for Claude.'),
        }}
      />

      <RemoveDialog
        name={removing?.company.name ?? ''}
        open={removing !== null}
        onOpenChange={(o) => {
          if (!o) setRemoving(null);
        }}
        onRemove={(reason, note) => {
          if (!removing) return;
          if (removing.company.id === selected) moveOn(removing.company.id);
          actions.remove(removing, reason, note);
        }}
      />
    </div>
  );
}

/**
 * Today (design 5.1): pick today's companies from the proposals, see the companies still being emailed.
 * Every group, why, capacity and not-ready reason comes from the client; the page lays them out.
 */
export default function TodayPage() {
  const client = useClient();
  const today = useViewQuery(queryKeys.today, () => client.today(), { pollMs: pollMs.default });
  const shell = useViewQuery(queryKeys.shell, () => client.shell(), { pollMs: pollMs.default });
  return (
    <div className="h-full min-h-0">
      {/* The view's name for the outline; the visible heading is the date. */}
      <h1 className="sr-only">All proposed companies</h1>
      <QueryState
        query={today}
        skeleton={
          <div className="px-6 pt-5">
            <TodaySkeleton />
          </div>
        }
        empty={null}
      >
        {(t) => <TodayContent t={t} limitToday={shell.data?.sending.limitToday} />}
      </QueryState>
    </div>
  );
}
