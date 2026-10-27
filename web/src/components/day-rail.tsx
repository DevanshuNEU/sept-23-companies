import { Check, Flag, Sparkles } from 'lucide-react';
import { FitChip } from '@/components/fit-chip';
import { Monogram } from '@/components/monogram';
import type { Id, IsoTime, PlanRow, PlanView } from '@/api';
import type { RowProps } from '@/keyboard/list-nav';
import { formatCarriedDay } from '@/lib/format';
import { cn } from '@/lib/utils';

/** How a node is drawn. `current` is the selected row while it is still to do. */
type Node = 'todo' | 'current' | 'logged' | 'done' | 'carried';

const WORD: Record<Node, string> = {
  todo: 'To do',
  current: 'To do',
  logged: 'Logged',
  done: 'Done',
  carried: 'Carried over',
};

function nodeOf(row: PlanRow, selected: boolean): Node {
  if (row.state === 'done') return 'done';
  // A skipped row is carried to tomorrow; a company removed as not a fit is off the plan altogether (the API).
  if (row.state === 'skipped') return 'carried';
  if (row.steps.some((s) => s.at !== null)) return 'logged';
  return selected ? 'current' : 'todo';
}

/** A company row (company first, #9): nobody named, not an email row. */
const companyRow = (row: PlanRow) => row.person === null && row.channel !== 'email';

function title(row: PlanRow): string {
  if (row.channel === 'email' || companyRow(row)) return row.company.name;
  return row.person?.person.name ?? 'Find someone';
}

function sub(row: PlanRow): string {
  // A pick says why today in one line ("New grad role, first seen 6 hours ago"), as the API words it (top picks).
  if (row.entryId < 0) return 'Coming in from the bench';
  if (companyRow(row)) return row.queue?.whyToday || row.queue?.standing.text || row.role?.title || 'A company to reach';
  if (row.channel !== 'email') return row.company.name;
  return row.state === 'done' ? 'By email, reviewed' : 'By email, in the Outbox';
}

const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

/**
 * The small state mark on a row's monogram: a check when done, the working dot once something is logged, a half
 * disc when carried to tomorrow. A row still to do has none. The word is in the row's label, so the mark is the
 * second signal.
 */
function StateMark({ node }: { node: Node }) {
  if (node === 'todo' || node === 'current') return null;
  return (
    <span
      aria-hidden
      className={cn(
        'absolute -right-1 -bottom-1 grid size-4 place-items-center rounded-full border-2 border-background',
        node === 'done' && 'bg-ok-dot text-white',
        node === 'logged' && 'bg-info-dot',
        node === 'carried' && 'bg-muted-foreground',
      )}
    >
      {node === 'done' ? <Check className="size-2.5" strokeWidth={4} /> : null}
    </span>
  );
}

const option = cn(
  'group relative grid cursor-default grid-cols-[32px_minmax(0,1fr)] items-start gap-x-3 rounded-lg border border-transparent px-2.5 py-2.5 outline-none transition-colors duration-150',
  'hover:bg-muted/55 aria-selected:border-border aria-selected:bg-card aria-selected:shadow-sm aria-selected:hover:bg-card',
  "aria-selected:before:absolute aria-selected:before:top-3 aria-selected:before:bottom-3 aria-selected:before:-left-px aria-selected:before:w-[3px] aria-selected:before:rounded-r-full aria-selected:before:bg-foreground aria-selected:before:content-['']",
  'focus-visible:ring-[3px] focus-visible:ring-ring/70',
);

function RailRow({ row, props, current, asOf }: { row: PlanRow; props: RowProps; current: boolean; asOf: IsoTime }) {
  const { ref, onClick, ...aria } = props;
  const node = nodeOf(row, aria['aria-selected']);
  const word = WORD[node];
  const carried = row.carriedFrom !== null && row.state === 'todo' ? `Carried over from ${formatCarriedDay(row.carriedFrom, asOf)}` : null;
  const ask = row.state === 'todo' && row.note?.needsYourLine === true ? 'Your line to confirm' : null;
  const fresh = row.queue?.opening?.fresh ? row.queue.opening.freshTag : null;
  // The profile judge's fit, only when it has judged the company (the panel says when it has not).
  const judged = row.queue?.judgeFit ?? null;
  const where = row.channel === 'email' ? `${title(row)} by email` : companyRow(row) ? title(row) : `${title(row)} at ${row.company.name}`;
  const label = [`${row.position}. ${where}`, word, ...[fresh, carried, ask].flatMap((p) => (p ? [lowerFirst(p)] : []))].join(', ');
  const left = node === 'done' || node === 'carried';

  return (
    <div
      role="option"
      {...aria}
      aria-label={label}
      aria-current={current ? 'step' : undefined}
      data-entry={row.entryId}
      data-node={node}
      data-provisional={row.entryId < 0 ? true : undefined}
      tabIndex={-1}
      ref={ref}
      onClick={onClick}
      className={option}
    >
      <span className={cn('relative mt-px', left && 'opacity-70')}>
        <Monogram name={row.company.name} />
        <StateMark node={node} />
      </span>
      <span className="grid min-w-0 gap-0.5">
        <span className="flex min-w-0 items-center gap-2">
          <b className={cn('min-w-0 flex-1 truncate text-sm leading-5 font-medium', left && 'font-normal text-muted-foreground')}>{title(row)}</b>
          <FitChip fit={judged} />
        </span>
        {fresh ? (
          <span data-fresh className="flex min-w-0 items-center gap-1 text-xs leading-4 font-medium text-info">
            <Sparkles aria-hidden className="size-3 flex-none" />
            <span className="truncate">{fresh}</span>
          </span>
        ) : null}
        <span className="truncate text-[13px] leading-5 text-muted-foreground">{sub(row)}</span>
        {node === 'done' || node === 'logged' || node === 'carried' ? (
          <span className={cn('text-xs leading-4', node === 'logged' ? 'text-info' : 'text-muted-foreground')}>{word}</span>
        ) : null}
        {carried ? <span className="truncate text-xs leading-4 text-muted-foreground">{carried}</span> : null}
        {ask ? <span className="truncate text-xs leading-4 text-muted-foreground">{ask}</span> : null}
      </span>
    </div>
  );
}

/** The day so far as a slim bar: "2 of 8 reached". Calm: a count of what is done, never of what is left. */
function Progress({ done, total, queue }: { done: number; total: number; queue: boolean }) {
  const word = `${done} of ${total} ${queue ? 'reached' : 'done'}`;
  return (
    <div className="mb-3 grid gap-1.5 px-3">
      <p className="text-xs leading-4 font-medium text-muted-foreground tabular-nums">{word}</p>
      <div
        role="meter"
        aria-label={queue ? 'Picks reached today' : 'People done today'}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
        aria-valuetext={word}
        className="h-1 overflow-hidden rounded-full bg-muted"
      >
        <div className="h-full rounded-full bg-ok transition-[width] duration-150" style={{ width: total > 0 ? `${Math.round((done / total) * 100)}%` : '0%' }} />
      </div>
    </div>
  );
}

/**
 * The day rail (design 16.1, `.rail-*`): one node per row in plan order (a pick, or a person on a plan with person
 * rows), joined by a line, ending in a finish node. The rows stay where the API put them (picks with a new opening
 * first, each with its "New opening, 6 hours ago" tag); a logged row is relabelled in place, never moved.
 * `aria-current="step"` marks the first row still to do, or the finish node once the day is done. The
 * selection itself belongs to the view (`rowProps` from `useListNav`).
 */
export function DayRail({
  rows,
  rowProps,
  tomorrow,
  allDone,
  asOf,
}: {
  rows: PlanRow[];
  rowProps: (id: Id) => RowProps;
  tomorrow: PlanView['tomorrow'];
  allDone: boolean;
  asOf: IsoTime;
}) {
  const firstTodo = allDone ? null : (rows.find((r) => r.state === 'todo')?.entryId ?? null);
  const queue = rows.length > 0 && rows.every(companyRow);
  return (
    <div className="grid">
      {rows.length > 0 ? <Progress done={rows.filter((r) => r.state === 'done').length} total={rows.length} queue={queue} /> : null}
      <div role="listbox" aria-label={queue ? "Today's picks" : "Today's people"} className="grid">
        {rows.map((row) => (
          <RailRow key={row.entryId} row={row} props={rowProps(row.entryId)} current={row.entryId === firstTodo} asOf={asOf} />
        ))}
      </div>
      <div
        data-finish
        data-done={allDone}
        aria-current={allDone ? 'step' : undefined}
        className={cn('mt-1 grid grid-cols-[32px_minmax(0,1fr)] items-start gap-x-3 rounded-lg px-2.5 py-2.5', allDone && 'bg-card shadow-sm ring-1 ring-border')}
      >
        <span
          aria-hidden
          className={cn(
            'grid size-8 place-items-center rounded-lg border-[1.5px] border-dashed border-muted-foreground text-muted-foreground',
            allDone && 'border-solid border-foreground bg-foreground text-background',
          )}
        >
          <Flag className="size-3.5" strokeWidth={2.5} />
        </span>
        <span className="grid min-w-0 gap-0.5">
          <b className="truncate text-sm leading-5 font-medium">Done for today</b>
          <span className="truncate text-[13px] leading-5 text-muted-foreground">{tomorrow.text}</span>
        </span>
      </div>
    </div>
  );
}
