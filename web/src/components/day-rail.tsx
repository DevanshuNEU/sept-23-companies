import { Check, Flag } from 'lucide-react';
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
  if (row.state === 'skipped') return 'carried';
  if (row.steps.some((s) => s.at !== null)) return 'logged';
  return selected ? 'current' : 'todo';
}

function title(row: PlanRow): string {
  if (row.channel === 'email') return row.company.name;
  return row.person?.person.name ?? 'Find someone';
}

function sub(row: PlanRow): string {
  if (row.channel !== 'email') return row.company.name;
  return row.state === 'done' ? 'By email, reviewed' : 'By email, in the Outbox';
}

const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

function NodeMark({ node, selected }: { node: Node; selected: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        'relative z-[1] grid size-5 place-items-center rounded-full border-[1.5px] border-muted-foreground bg-background text-background',
        selected && 'border-2 border-foreground',
        node === 'done' && 'border-foreground bg-foreground',
        node === 'logged' && 'border-info-dot bg-info-bg text-info',
        node === 'carried' && 'bg-[linear-gradient(90deg,var(--muted-foreground)_50%,var(--background)_50%)]',
      )}
    >
      {node === 'done' ? <Check className="size-3" strokeWidth={3} /> : null}
      {node === 'logged' ? <span className="size-1.5 rounded-full bg-info-dot" /> : null}
    </span>
  );
}

const option = cn(
  'relative grid cursor-default grid-cols-[20px_minmax(0,1fr)_auto] items-start gap-x-3 rounded-md px-3 py-2.5 outline-none',
  'hover:bg-muted/55 aria-selected:bg-muted aria-selected:hover:bg-muted focus-visible:ring-[3px] focus-visible:ring-ring/50',
  // The line from under this node to the next one; darker once the row is done, so the distance travelled shows.
  "before:absolute before:top-8 before:-bottom-2.5 before:left-[21.5px] before:w-px before:bg-border before:content-['']",
  'data-[node=done]:before:bg-muted-foreground',
);

function RailRow({ row, props, current, asOf }: { row: PlanRow; props: RowProps; current: boolean; asOf: IsoTime }) {
  const { ref, onClick, ...aria } = props;
  const node = nodeOf(row, aria['aria-selected']);
  const word = WORD[node];
  const carried = row.carriedFrom !== null && row.state === 'todo' ? `Carried over from ${formatCarriedDay(row.carriedFrom, asOf)}` : null;
  const ask = row.state === 'todo' && row.note?.needsYourLine === true ? 'Your line to confirm' : null;
  const where = row.channel === 'email' ? `${title(row)} by email` : `${title(row)} at ${row.company.name}`;
  const label = [`${row.position}. ${where}`, word, ...[carried, ask].flatMap((p) => (p ? [lowerFirst(p)] : []))].join(', ');

  return (
    <div
      role="option"
      {...aria}
      aria-label={label}
      aria-current={current ? 'step' : undefined}
      data-entry={row.entryId}
      data-node={node}
      tabIndex={-1}
      ref={ref}
      onClick={onClick}
      className={option}
    >
      <NodeMark node={node} selected={aria['aria-selected']} />
      <span className="grid min-w-0 gap-px">
        <b className="truncate font-medium">
          <span className="mr-1.5 font-normal text-muted-foreground tabular-nums">{row.position}</span>
          {title(row)}
        </b>
        <span className="truncate text-[13px] text-muted-foreground">{sub(row)}</span>
        {carried ? <span className="truncate text-xs leading-4 text-muted-foreground">{carried}</span> : null}
        {ask ? <span className="truncate text-xs leading-4 text-muted-foreground">{ask}</span> : null}
      </span>
      <span
        className={cn(
          'text-xs leading-5 whitespace-nowrap text-muted-foreground',
          node === 'logged' && 'text-info',
          node === 'done' && 'text-foreground',
        )}
      >
        {word}
      </span>
    </div>
  );
}

/**
 * The day rail (design 16.1, `.rail-*`): one node per person in plan order, joined by a line, ending in a
 * finish node. The rows stay where the API put them; a logged row is relabelled in place, never moved.
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
  return (
    <div className="grid">
      <div role="listbox" aria-label="Today's people" className="grid">
        {rows.map((row) => (
          <RailRow key={row.entryId} row={row} props={rowProps(row.entryId)} current={row.entryId === firstTodo} asOf={asOf} />
        ))}
      </div>
      <div
        data-finish
        data-done={allDone}
        aria-current={allDone ? 'step' : undefined}
        className={cn(
          'relative grid grid-cols-[20px_minmax(0,1fr)_auto] items-start gap-x-3 rounded-md px-3 py-2.5',
          allDone && 'bg-muted',
        )}
      >
        <span
          aria-hidden
          className={cn(
            'relative z-[1] grid size-5 place-items-center rounded-full border-[1.5px] border-dashed border-muted-foreground bg-background text-muted-foreground',
            allDone && 'border-solid border-foreground bg-foreground text-background',
          )}
        >
          <Flag className="size-3" strokeWidth={3} />
        </span>
        <span className="grid min-w-0 gap-px">
          <b className="truncate font-medium">Done for today</b>
          <span className="truncate text-[13px] text-muted-foreground">{tomorrow.text}</span>
        </span>
        <span />
      </div>
    </div>
  );
}
