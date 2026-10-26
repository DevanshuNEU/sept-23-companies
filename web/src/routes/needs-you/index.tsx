import { Fragment, useRef, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { useNavigate } from 'react-router';
import { ChevronDown, CircleCheck, Lock, Undo2 } from 'lucide-react';
import {
  ApiError,
  newRequestId,
  pollMs,
  queryKeys,
  usePageClient,
  type Id,
  type JobRunSummary,
  type NeedsYouView,
  type NoGoReason,
  type ResolvedReview,
  type ReviewFix,
  type ReviewItem,
  type ReviewKind,
  type ShellSummary,
} from '@/api';
import { EmptyState, QueryState, StateBanner } from '@/components/state-views';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableRow } from '@/components/ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useHotkeys } from '@/keyboard/hotkeys';
import { useListNav } from '@/keyboard/list-nav';
import { ageDays, formatAge, formatDay, formatDayTime, formatLocalTime, formatWeekdayTime } from '@/lib/format';
import { usePendingWrite, useViewQuery, type Patch } from '@/pending/pending';
import { cn } from '@/lib/utils';
import { NoGoDialog, type NoGoStart } from './no-go-dialog';
import { mergeName, possessive } from './merge-compare';
import { EDIT_FIELD, ReviewItemRow, TRIGGER, hasNoGo } from './review-item';
import { SettledPanel } from './settled-panel';
import { resolvedGroups } from './groups';
import { settleView, type Settling } from './patches';

type Section = { id: string; title: string; items: ReviewItem[] };

const DETAILS: readonly ReviewKind[] = ['size_unknown', 'size_guess', 'size_check', 'domain_missing'];
const BOARDS: readonly ReviewKind[] = ['board_unreadable', 'board_empty'];

/**
 * Sections by what an item holds up (design 5.4), always in this order: email first (`holdsEmail` comes from the
 * API), then sponsorship, company details, job boards, possible duplicates, and Other for the rest (a flag, the
 * reader, a changed careers page, or a kind this page does not know yet), so nothing is hidden. Empty ones are dropped.
 */
function sectionsOf(open: ReviewItem[]): Section[] {
  const email = open.filter((r) => r.holdsEmail);
  const rest = open.filter((r) => !r.holdsEmail);
  const placed = new Set<ReviewKind>(['sponsorship_unclear', ...DETAILS, ...BOARDS, 'merge_candidate']);
  const sections: Section[] = [
    { id: 'email', title: 'Holding up email', items: email },
    { id: 'sponsorship', title: 'Sponsorship', items: rest.filter((r) => r.kind === 'sponsorship_unclear') },
    { id: 'details', title: 'Company details', items: rest.filter((r) => DETAILS.includes(r.kind)) },
    { id: 'boards', title: 'Job boards', items: rest.filter((r) => BOARDS.includes(r.kind)) },
    { id: 'merge', title: 'Possible duplicates', items: rest.filter((r) => r.kind === 'merge_candidate') },
    { id: 'other', title: 'Other', items: rest.filter((r) => !placed.has(r.kind)) },
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
  merge_candidate: 'stays a separate company',
  reader_disagrees: 'keeps what the rules decided',
  careers_changed: 'stays as it is',
  size_check: 'keeps the size on file',
};

const FIELD_NAME: Record<Extract<ReviewFix, { kind: 'edit' }>['field'], string> = {
  domain: 'email domain',
  size: 'size',
  sponsorship: 'sponsorship',
  board_url: 'board',
};

// The API's words for a settling (outreach.review.words), for the row the page shows until its refetch lands.
const FIELD_LABEL: Record<Extract<ReviewFix, { kind: 'edit' }>['field'], string> = {
  domain: 'Email domain',
  size: 'Size',
  sponsorship: 'Sponsorship',
  board_url: 'Job board',
};
const NO_GO_WORDS: Partial<Record<NoGoReason, string>> = {
  no_sponsorship: 'does not sponsor',
  citizenship: 'needs citizenship',
  over_300: 'over 300 people',
  other: 'another reason',
};

/** What comes back on Undo from Resolved, for its toast: "Undone. Railway's sponsorship question is open again." */
const QUESTION: Record<ReviewKind, string> = {
  domain_missing: 'email domain question',
  size_unknown: 'size question',
  size_guess: 'size question',
  size_check: 'size question',
  sponsorship_unclear: 'sponsorship question',
  board_unreadable: 'board question',
  board_empty: 'board question',
  flagged: 'flag',
  merge_candidate: 'duplicate question',
  reader_disagrees: 'reader question',
  careers_changed: 'careers page question',
};

const isMerge = (r: ResolvedReview) => r.kind === 'merge_candidate' && r.resolution === 'field_edited';

function undoWords(r: ResolvedReview): { toast: string; tip: string } {
  if (r.resolution === 'marked_no_go') {
    return {
      toast: `Undone. ${r.companyName} is off the no-go list and its question is open again.`,
      tip: `Takes ${r.companyName} off the no-go list and puts the question back`,
    };
  }
  if (isMerge(r)) {
    // The row names the question's company, which may be the one that was folded in, so the words name no direction.
    return {
      toast: `Undone. The merge is taken back, and ${possessive(r.companyName)} duplicate question is open again.`,
      tip: 'Makes two companies again and puts the question back',
    };
  }
  return { toast: `Undone. ${possessive(r.companyName)} ${QUESTION[r.kind]} is open again.`, tip: 'Puts the question back in Needs you' };
}

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

function ResolvedRow({ row, asOf, busy, onUndo }: { row: ResolvedReview; asOf: string; busy: boolean; onUndo: (r: ResolvedReview) => void }) {
  const today = ageDays(row.at, asOf) === 0;
  return (
    <TableRow>
      <TableCell className="font-medium">{row.companyName}</TableCell>
      <TableCell>
        <Badge variant="outline">{TRIGGER[row.kind].label}</Badge>
      </TableCell>
      <TableCell className="text-sm whitespace-normal">
        <span>{row.note}</span>
        {row.locked ? <span>{`${row.note.endsWith('.') ? ' ' : '. '}To change it, set it again on the company page.`}</span> : null}
      </TableCell>
      <TableCell className="w-px text-sm whitespace-nowrap text-muted-foreground tabular-nums">
        {today ? formatLocalTime(row.at) : formatDay(row.at)}
      </TableCell>
      <TableCell className="w-px">
        {row.canUndo ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => onUndo(row)}>
                <Undo2 aria-hidden />
                Undo
              </Button>
            </TooltipTrigger>
            <TooltipContent>{undoWords(row).tip}</TooltipContent>
          </Tooltip>
        ) : row.locked ? (
          <span className="inline-flex items-center gap-1.5 text-xs whitespace-nowrap text-muted-foreground">
            <Lock aria-hidden className="size-3.5" />
            Edited, locked
          </span>
        ) : null}
      </TableCell>
    </TableRow>
  );
}

function GroupRow({ title }: { title: string }) {
  return (
    <TableRow className="hover:bg-transparent">
      <TableCell colSpan={5} className="h-8 bg-subtle text-xs font-medium text-muted-foreground">
        {title}
      </TableCell>
    </TableRow>
  );
}

/**
 * What he settled this week, collapsed, grouped by the real day (Today, Earlier this week, then a date). An ok, a no-go or a merge has Undo
 * (`canUndo`, from the API); a value he set is locked and says how to change it.
 */
function Resolved({ data }: { data: NeedsYouView }) {
  const [open, setOpen] = useState(false);
  const client = usePageClient('needs-you');
  const rows = data.resolvedThisWeek;
  const groups = resolvedGroups(rows, data.asOf);

  // Undo from Resolved changes nothing on screen until the server has taken the settling back: then the list and the
  // sidebar refetch and the item is open again. While the call is out its Undo is off, so a double click sends one
  // reopen. A refusal leaves everything as it was and says why.
  const queryClient = useQueryClient();
  const inFlight = useRef(new Set<Id>());
  const [busy, setBusy] = useState<ReadonlySet<Id>>(new Set());
  const undo = async (r: ResolvedReview) => {
    if (inFlight.current.has(r.id)) return;
    inFlight.current.add(r.id);
    setBusy(new Set(inFlight.current));
    try {
      await client.reopenReview(r.id, r.revision, { requestId: newRequestId() });
      toast(undoWords(r).toast);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'The change did not go through. Nothing was saved. Try again.');
    } finally {
      await Promise.all([queryKeys.needsYou, queryKeys.shell].map((queryKey) => queryClient.invalidateQueries({ queryKey, exact: true })));
      inFlight.current.delete(r.id);
      setBusy(new Set(inFlight.current));
    }
  };

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
                {groups.map((g) => (
                  <Fragment key={g.title}>
                    {groups.length > 1 ? <GroupRow title={g.title} /> : null}
                    {g.rows.map((r) => (
                      <ResolvedRow key={r.id} row={r} asOf={data.asOf} busy={busy.has(r.id)} onUndo={(row) => void undo(row)} />
                    ))}
                  </Fragment>
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

/**
 * Data gaps (#9, company-first ruling): open items for companies he is not working today, by the company's rank.
 * Collapsed and quiet: no badge, no alarm. Each row still settles the same way as in Needs you.
 */
function DataGaps({ count, open, onOpenChange, children }: { count: number; open: boolean; onOpenChange: (open: boolean) => void; children: ReactNode }) {
  return (
    <Collapsible open={open} onOpenChange={onOpenChange} asChild>
      <section aria-labelledby="ny-gaps">
        <SectionTitle id="ny-gaps" title="Data gaps" count={count}>
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm">
              <ChevronDown aria-hidden className={cn('transition-transform motion-reduce:transition-none', open && 'rotate-180')} />
              {open ? 'Hide gaps' : 'Show gaps'}
            </Button>
          </CollapsibleTrigger>
        </SectionTitle>
        <p className="mb-2 text-xs text-muted-foreground">
          Things the crawler could not read, and questions about companies outside your plan. Nothing here holds up an email.
        </p>
        <CollapsibleContent>
          <Card className="gap-0 overflow-hidden py-0">{children}</Card>
        </CollapsibleContent>
      </section>
    </Collapsible>
  );
}

function OpenItems({ data }: { data: NeedsYouView }) {
  const client = usePageClient('needs-you');
  const shellClient = usePageClient('shell');
  const shell = useViewQuery(queryKeys.shell, () => shellClient.shell());
  const navigate = useNavigate();
  const write = usePendingWrite();
  const [editing, setEditing] = useState<Id | null>(null);
  // Kept after close so the dialog keeps its words through the exit animation; `n` keys each open afresh.
  const [noGo, setNoGo] = useState<{ item: ReviewItem; start: NoGoStart; n: number; open: boolean } | null>(null);
  const [gapsOpen, setGapsOpen] = useState(false);

  const sections = sectionsOf(data.open);
  const ordered = sections.flatMap((s) => s.items);
  const gaps = data.dataGaps;
  const gapIds = new Set(gaps.map((r) => r.id));
  // A data gap joins the keyboard list only while its section is open.
  const listed = gapsOpen ? [...ordered, ...gaps] : ordered;
  const ids = listed.map((r) => r.id);
  const byId = new Map(listed.map((r) => [r.id, r]));
  const { selected, select, rowProps } = useListNav<Id>({
    ids,
    // One next action: the first item is selected when he arrives, so the first key settles it.
    initial: ids[0],
    onOpen: (id) => {
      const r = byId.get(id);
      if (r) navigate(`/companies/${r.company.id}`);
    },
    announce: (id) => {
      const r = byId.get(id);
      return r ? mergeName(r) : '';
    },
  });
  const current = selected === null ? undefined : byId.get(selected);

  /**
   * The item leaves the list, the sidebar count drops (a data gap was never in it) and today's settled count rises
   * at once, and Resolved shows the settling in the API's words until the refetch brings the stored row. That row has
   * no Undo of its own: the toast holds it.
   */
  const patchesFor = (
    r: ReviewItem,
    settled: Settling,
    extra?: (s: ShellSummary) => Partial<ShellSummary>,
  ): Patch[] => [
    {
      key: queryKeys.needsYou,
      apply: (d: NeedsYouView) => settleView(d, r, settled),
    },
    {
      key: queryKeys.shell,
      apply: (s: ShellSummary) => ({ ...s, openReviewItems: gapIds.has(r.id) ? s.openReviewItems : Math.max(0, s.openReviewItems - 1), ...extra?.(s) }),
    },
  ];
  const moveOn = (r: ReviewItem) => {
    const at = ids.indexOf(r.id);
    const next = ids[at + 1] ?? ids[at - 1];
    if (next !== undefined) select(next);
  };

  const markOk = (r: ReviewItem) => {
    if (editing === r.id) setEditing(null);
    moveOn(r);
    const merge = r.kind === 'merge_candidate';
    const label = merge
      ? r.merge
        ? `Kept ${mergeName(r)} separate.`
        : `Kept separate. ${r.company.name} ${OK_TEXT[r.kind]}.`
      : `Marked ok. ${r.company.name} ${OK_TEXT[r.kind]}.`;
    write({
      id: `review:${r.id}`,
      label,
      afterCommit: merge ? label : `Marked ok and locked. ${r.company.name} ${OK_TEXT[r.kind]}.`,
      patches: patchesFor(r, { resolution: 'marked_ok', note: merge ? 'Kept separate by you' : 'Marked ok by you', locked: false }),
      commit: (o) => client.resolveReview(r.id, { kind: 'ok' }, r.revision, o),
    });
  };

  const merge = (r: ReviewItem, keep: 'a' | 'b') => {
    if (!r.merge) return;
    moveOn(r);
    const keeper = r.merge[keep];
    const loser = r.merge[keep === 'a' ? 'b' : 'a'];
    const label = `Merged ${loser.name} into ${keeper.name}.`;
    write({
      id: `review:${r.id}`,
      label,
      afterCommit: label,
      patches: patchesFor(r, { resolution: 'field_edited', note: `Merged ${loser.name} into ${keeper.name} by you`, locked: false }),
      commit: (o) => client.resolveReview(r.id, { kind: 'merge', keep }, r.revision, o),
    });
  };

  const saveEdit = (r: ReviewItem, fix: Extract<ReviewFix, { kind: 'edit' }>) => {
    setEditing(null);
    moveOn(r);
    const label =
      fix.field === 'domain'
        ? `Saved and locked. ${possessive(r.company.name)} drafts will be checked now.`
        : `Saved and locked. ${possessive(r.company.name)} ${FIELD_NAME[fix.field]} is ${fix.value}.`;
    write({
      id: `review:${r.id}`,
      label,
      afterCommit: label,
      patches: patchesFor(r, { resolution: 'field_edited', note: `${FIELD_LABEL[fix.field]} set to ${fix.value} by you, from ${fix.source}`, locked: true }),
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
      patches: patchesFor(
        r,
        { resolution: 'marked_no_go', note: `Added to the no-go list by you: ${NO_GO_WORDS[reason] ?? reason.replace(/_/g, ' ')}`, locked: false },
        (s) => ({ noGoCount: s.noGoCount + 1 }),
      ),
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
      { keys: 'o', label: 'Mark ok, or keep a possible duplicate separate', group, enabled: current !== undefined, run: () => current && markOk(current) },
      { keys: 'n', label: 'Mark no-go, with a reason', group, enabled: current !== undefined && hasNoGo(current.kind), run: () => current && openNoGo(current) },
      {
        keys: 'm',
        label: 'Merge into the suggested company',
        group,
        enabled: current?.merge != null,
        run: () => {
          if (current?.merge) merge(current, current.merge.suggestedKeep);
        },
      },
    ],
    { selection: selected },
  );

  const row = (r: ReviewItem) => (
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
      onMerge={(keep) => merge(r, keep)}
    />
  );

  let main: ReactNode;
  if (ordered.length === 0) {
    main =
      data.settledToday > 0 ? (
        <SettledPanel
          settledToday={data.settledToday}
          today={data.resolvedThisWeek.filter((r) => ageDays(r.at, data.asOf) === 0)}
          nextReadAt={data.boardsJob?.expectedNextAt ?? null}
          drafts={shell.data?.draftsToReview ?? 0}
        />
      ) : (
        <EmptyState icon={CircleCheck} title="Nothing needs you.">
          New items appear after each board read and when you pick a company.
        </EmptyState>
      );
  } else {
    main = sections.map((s) => (
      <section key={s.id} aria-labelledby={`ny-${s.id}`}>
        <SectionTitle id={`ny-${s.id}`} title={s.title} count={s.items.length} />
        <Card className="gap-0 overflow-hidden py-0">{s.items.map(row)}</Card>
      </section>
    ));
  }

  return (
    <>
      {main}
      {gaps.length > 0 ? (
        <DataGaps count={gaps.length} open={gapsOpen} onOpenChange={setGapsOpen}>
          {gaps.map(row)}
        </DataGaps>
      ) : null}
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

/** "2 settled today, 3 open" in grey, or "5 open" with nothing settled yet; nothing once nothing is open (the panel says it). */
function ProgressLine({ data }: { data: NeedsYouView }) {
  const open = data.open.length;
  if (open === 0) return null;
  const text = data.settledToday > 0 ? `${data.settledToday} settled today, ${open} open` : `${open} open`;
  return (
    <p data-testid="ny-progress" className="mt-0.5 text-sm text-muted-foreground tabular-nums">
      {text}
    </p>
  );
}

/**
 * Needs you (design 5.4): the review items of companies he is working, each with its reason and a one-click fix. A
 * fix is locked. Under the title, the plain progress line; when nothing is open and he settled something today, the
 * settled panel. Below, the quiet Data gaps: every other open item, collapsed.
 */
export default function NeedsYouPage() {
  const client = usePageClient('needs-you');
  const query = useViewQuery(queryKeys.needsYou, () => client.needsYou(), { pollMs: pollMs.default });
  const job = query.data?.boardsJob ?? null;
  return (
    <div className="max-w-[1080px] px-6 pt-5 pb-12">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Needs you</h1>
          {query.data ? <ProgressLine data={query.data} /> : null}
          <p className="mt-1.5 max-w-2xl text-sm text-muted-foreground">
            Things only you can settle. Your answer sticks.
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
            {data.resolvedThisWeek.length > 0 ? <Resolved data={data} /> : null}
          </>
        )}
      </QueryState>
    </div>
  );
}
