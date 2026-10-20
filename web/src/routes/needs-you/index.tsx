import { useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router';
import { ChevronDown, CircleCheck, Lock } from 'lucide-react';
import {
  pollMs,
  queryKeys,
  useClient,
  type Id,
  type JobRunSummary,
  type NeedsYouView,
  type NoGoReason,
  type ResolvedReview,
  type ReviewFix,
  type ReviewItem,
  type ShellSummary,
} from '@/api';
import { EmptyState, QueryState, StateBanner } from '@/components/state-views';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableRow } from '@/components/ui/table';
import { useHotkeys } from '@/keyboard/hotkeys';
import { useListNav } from '@/keyboard/list-nav';
import { formatAge, formatDay, formatDayTime, formatWeekdayTime } from '@/lib/format';
import { usePendingWrite, useViewQuery, type Patch } from '@/pending/pending';
import { cn } from '@/lib/utils';
import { NoGoDialog, type NoGoStart } from './no-go-dialog';
import { EDIT_FIELD, ReviewItemRow, TRIGGER } from './review-item';

type Section = { id: string; title: string; items: ReviewItem[] };

/**
 * Sections by what an item holds up (design 5.4): email first, then sponsorship, then job boards. `holdsEmail`
 * comes from the API; anything else that is open lands under Other so nothing is hidden.
 */
function sectionsOf(open: ReviewItem[]): Section[] {
  const email = open.filter((r) => r.holdsEmail);
  const rest = open.filter((r) => !r.holdsEmail);
  const sections: Section[] = [
    { id: 'email', title: 'Holding up email', items: email },
    { id: 'sponsorship', title: 'Sponsorship', items: rest.filter((r) => r.kind === 'sponsorship_unclear') },
    { id: 'boards', title: 'Job boards', items: rest.filter((r) => r.kind === 'board_unreadable' || r.kind === 'board_empty') },
    {
      id: 'other',
      title: 'Other',
      items: rest.filter((r) => r.kind !== 'sponsorship_unclear' && r.kind !== 'board_unreadable' && r.kind !== 'board_empty'),
    },
  ];
  return sections.filter((s) => s.items.length > 0);
}

// What Mark ok leaves in place, for the toast.
const OK_TEXT: Record<ReviewItem['kind'], string> = {
  domain_missing: 'still has no email domain',
  size_unknown: 'stays at unknown size',
  size_guess: 'keeps its guessed size',
  sponsorship_unclear: 'stays at unclear sponsorship',
  board_unreadable: 'keeps its postings open',
  board_empty: 'keeps its postings closed',
  flagged: 'stays as it is',
};

const FIELD_NAME: Record<Extract<ReviewFix, { kind: 'edit' }>['field'], string> = {
  domain: 'email domain',
  size: 'size',
  sponsorship: 'sponsorship',
  board_url: 'board',
};

function SectionTitle({ id, title, count, children }: { id: string; title: string; count: number; children?: ReactNode }) {
  return (
    <div className="mt-6 mb-2 flex items-center gap-2">
      <h2 id={id} className="text-sm font-semibold">
        {title} <span className="font-normal text-muted-foreground tabular-nums">{count}</span>
      </h2>
      {children ? <div className="ml-auto">{children}</div> : null}
    </div>
  );
}

function Resolved({ rows }: { rows: ResolvedReview[] }) {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible open={open} onOpenChange={setOpen} asChild>
      <section aria-labelledby="ny-resolved">
        <SectionTitle id="ny-resolved" title="Resolved this week" count={rows.length}>
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm">
              <ChevronDown aria-hidden className={cn('transition-transform motion-reduce:transition-none', open && 'rotate-180')} />
              {open ? 'Hide' : 'Show'}
            </Button>
          </CollapsibleTrigger>
        </SectionTitle>
        <CollapsibleContent>
          <div className="rounded-lg border">
            <Table>
              <TableBody>
                {rows.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="font-medium">{r.companyName}</TableCell>
                    <TableCell>
                      <Badge variant="outline">{TRIGGER[r.kind].label}</Badge>
                    </TableCell>
                    <TableCell className="text-sm whitespace-normal">{r.note}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{formatDay(r.at)}</TableCell>
                    <TableCell className="w-px">
                      {r.locked ? (
                        <Badge variant="outline">
                          <Lock aria-hidden />
                          Locked
                        </Badge>
                      ) : null}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            A resolved item comes back only when the evidence under it changes, and then it says what changed.
          </p>
        </CollapsibleContent>
      </section>
    </Collapsible>
  );
}

/** The shared stale banner, when the last board read is late or failed: board items depend on it. */
function BoardsBanner({ job, asOf }: { job: JobRunSummary | null; asOf: string }) {
  if (!job || (!job.late && job.status !== 'failed')) return null;
  const detail = job.error || (job.expectedNextAt ? `The read due ${formatDayTime(job.expectedNextAt)} did not run.` : '');
  return (
    <StateBanner tone="warn" title={`Boards last read ${formatAge(job.startedAt, asOf)}.`}>
      {detail}
    </StateBanner>
  );
}

function NeedsYouSkeleton() {
  return (
    <div className="flex flex-col gap-3" aria-hidden>
      <Skeleton className="mt-6 h-4 w-40" />
      <Skeleton className="h-28 w-full" />
      <Skeleton className="h-28 w-full" />
      <Skeleton className="mt-6 h-4 w-32" />
      <Skeleton className="h-28 w-full" />
    </div>
  );
}

function OpenItems({ data }: { data: NeedsYouView }) {
  const client = useClient();
  const navigate = useNavigate();
  const write = usePendingWrite();
  const [editing, setEditing] = useState<Id | null>(null);
  // Kept after close so the dialog keeps its words through the exit animation; `n` keys each open afresh.
  const [noGo, setNoGo] = useState<{ item: ReviewItem; start: NoGoStart; n: number; open: boolean } | null>(null);

  const sections = sectionsOf(data.open);
  const ordered = sections.flatMap((s) => s.items);
  const ids = ordered.map((r) => r.id);
  const byId = new Map(ordered.map((r) => [r.id, r]));
  const { selected, select, rowProps } = useListNav<Id>({
    ids,
    onOpen: (id) => {
      const r = byId.get(id);
      if (r) navigate(`/companies/${r.company.id}`);
    },
    announce: (id) => byId.get(id)?.company.name ?? '',
  });
  const current = selected === null ? undefined : byId.get(selected);

  /** The item leaves the list and the sidebar count drops at once; the selection moves to the next item. */
  const patchesFor = (r: ReviewItem, extra?: (s: ShellSummary) => Partial<ShellSummary>): Patch[] => [
    { key: queryKeys.needsYou, apply: (d: NeedsYouView) => ({ ...d, open: d.open.filter((x) => x.id !== r.id) }) },
    { key: queryKeys.shell, apply: (s: ShellSummary) => ({ ...s, openReviewItems: Math.max(0, s.openReviewItems - 1), ...extra?.(s) }) },
  ];
  const moveOn = (r: ReviewItem) => {
    const at = ids.indexOf(r.id);
    const next = ids[at + 1] ?? ids[at - 1];
    if (next !== undefined) select(next);
  };

  const markOk = (r: ReviewItem) => {
    if (editing === r.id) setEditing(null);
    moveOn(r);
    write({
      id: `review:${r.id}`,
      label: `Marked ok. ${r.company.name} ${OK_TEXT[r.kind]}.`,
      afterCommit: `Marked ok and locked. ${r.company.name} ${OK_TEXT[r.kind]}.`,
      patches: patchesFor(r),
      commit: (o) => client.resolveReview(r.id, { kind: 'ok' }, r.revision, o),
    });
  };

  const saveEdit = (r: ReviewItem, fix: Extract<ReviewFix, { kind: 'edit' }>) => {
    setEditing(null);
    moveOn(r);
    const label =
      fix.field === 'domain'
        ? `Saved and locked. ${r.company.name}'s drafts will be checked now.`
        : `Saved and locked. ${r.company.name}'s ${FIELD_NAME[fix.field]} is ${fix.value}.`;
    write({
      id: `review:${r.id}`,
      label,
      afterCommit: label,
      patches: patchesFor(r),
      commit: (o) => client.resolveReview(r.id, fix, r.revision, o),
    });
  };

  const markNoGo = (r: ReviewItem, reason: NoGoReason, source: string) => {
    if (editing === r.id) setEditing(null);
    moveOn(r);
    write({
      id: `review:${r.id}`,
      label: `${r.company.name} is on the no-go list.`,
      afterCommit: `${r.company.name} is on the no-go list.`,
      patches: patchesFor(r, (s) => ({ noGoCount: s.noGoCount + 1 })),
      commit: (o) => client.resolveReview(r.id, { kind: 'no_go', reason, source }, r.revision, o),
      immediate: true,
    });
  };

  const openNoGo = (r: ReviewItem, source = '') =>
    setNoGo((prev) => ({ item: r, start: { reason: 'no_sponsorship', source }, n: (prev?.n ?? 0) + 1, open: true }));

  const group = 'Needs you';
  // These act on the selected item, so a key right after j, k or a click waits for that move to render.
  useHotkeys(
    [
      {
        keys: 'e',
        label: 'Edit the field, with a source',
        group,
        enabled: current !== undefined && EDIT_FIELD[current.kind] !== null && editing !== current.id,
        run: () => {
          if (current) setEditing(current.id);
        },
      },
      { keys: 'o', label: 'Mark ok', group, enabled: current !== undefined, run: () => current && markOk(current) },
      { keys: 'n', label: 'Mark no-go, with a reason', group, enabled: current !== undefined, run: () => current && openNoGo(current) },
    ],
    { selection: selected },
  );

  if (ordered.length === 0) {
    return (
      <EmptyState icon={CircleCheck} title="Nothing needs you.">
        New items appear after each board read and when you pick a company.
      </EmptyState>
    );
  }

  return (
    <>
      {sections.map((s) => (
        <section key={s.id} aria-labelledby={`ny-${s.id}`}>
          <SectionTitle id={`ny-${s.id}`} title={s.title} count={s.items.length} />
          <Card className="gap-0 overflow-hidden py-0">
            {s.items.map((r) => (
              <ReviewItemRow
                key={r.id}
                item={r}
                row={rowProps(r.id)}
                editing={editing === r.id}
                onEdit={() => {
                  select(r.id);
                  setEditing(r.id);
                }}
                onOk={() => markOk(r)}
                onNoGo={(source) => openNoGo(r, source)}
                onSave={(fix) => saveEdit(r, fix)}
                onCancelEdit={() => setEditing(null)}
              />
            ))}
          </Card>
        </section>
      ))}
      {noGo ? (
        <NoGoDialog
          key={noGo.n}
          name={noGo.item.company.name}
          start={noGo.start}
          open={noGo.open}
          onOpenChange={(o) => {
            if (!o) setNoGo((prev) => (prev ? { ...prev, open: false } : prev));
          }}
          onMark={(reason, source) => markNoGo(noGo.item, reason, source)}
        />
      ) : null}
    </>
  );
}

/** Needs you (design 5.4): every review item with its reason and a one-click fix. A fix is locked. */
export default function NeedsYouPage() {
  const client = useClient();
  const query = useViewQuery(queryKeys.needsYou, () => client.needsYou(), { pollMs: pollMs.default });
  const job = query.data?.boardsJob ?? null;
  return (
    <div className="max-w-[1080px] px-6 pt-5 pb-12">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Needs you</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Things only you can settle. Your fix is locked, so automation never overwrites it. Items that hold up
            today's email come first.
          </p>
        </div>
        {job ? (
          <p className="text-xs text-muted-foreground">
            {`Boards read ${formatDayTime(job.startedAt)}.${job.expectedNextAt ? ` Next read ${formatWeekdayTime(job.expectedNextAt)}` : ''}`}
          </p>
        ) : null}
      </div>
      <QueryState query={query} skeleton={<NeedsYouSkeleton />} empty={null}>
        {(data) => (
          <>
            <div className="mt-4 empty:hidden">
              <BoardsBanner job={data.boardsJob} asOf={data.asOf} />
            </div>
            <OpenItems data={data} />
            {data.resolvedThisWeek.length > 0 ? <Resolved rows={data.resolvedThisWeek} /> : null}
          </>
        )}
      </QueryState>
    </div>
  );
}
