import { createContext, useContext } from 'react';
import { flexRender, getCoreRowModel, useReactTable, type ColumnDef, type Row } from '@tanstack/react-table';
import { Archive, Trash2 } from 'lucide-react';
import type { DailyEntry, IsoTime, LevelTier, RoleToName } from '@/api';
import { ConfidenceMeter } from '@/components/confidence-meter';
import { StatusBadge } from '@/components/status-badge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import type { RowProps } from '@/keyboard/list-nav';
import { formatAge, formatDueDay } from '@/lib/format';
import { sponsorshipStatus } from '@/lib/status';
import { cn } from '@/lib/utils';
import { groupInOrder } from './grouping';

const LEVEL: Record<LevelTier, string | null> = {
  new_grad: 'New grad',
  early_career: 'Early career',
  swe1: 'SWE I',
  swe2: 'SWE II',
  open: 'Open level',
  not_stated: null,
  senior: 'Senior',
};

const GROUP_LABEL: Record<DailyEntry['listGroup'], string> = {
  level_role: 'Role at your level: new grad, early career, SWE I, SWE II',
  intern_junior: 'Intern or junior work you can do on OPT',
  reason_no_role: 'No role yet, a reason to reach out',
  any_us_role: 'Open US engineering role, level not named',
};

const HEAD = 'h-9 bg-subtle px-3 text-[13px] text-muted-foreground';
const CELL = 'h-9 px-3 py-1.5 text-[13px] whitespace-normal';
/**
 * Row actions float over the row's right edge, so they take no column width (the 1280 px layout has 610 px for this table).
 * From 1400 px the Sponsorship cell reserves room for them; below that the badge is hidden while they show.
 */
const FLOAT = 'absolute top-1/2 right-2 z-10 -translate-y-1/2';
const SUB = 'text-xs text-muted-foreground';

/** "New grad", "Starts 2027" and "2 days ago, asks 2 years", from the stored role. */
export function RoleMeta({ role, asOf }: { role: RoleToName; asOf: IsoTime }) {
  const level = LEVEL[role.levelTier];
  const years = role.yearsAsked === null ? '' : `, asks ${role.yearsAsked} ${role.yearsAsked === 1 ? 'year' : 'years'}`;
  return (
    <span className={cn(SUB, 'flex flex-wrap items-center gap-x-1.5')}>
      {level ? <Badge variant="idle">{level}</Badge> : null}
      {role.start2027 ? <Badge variant="warn">Starts 2027</Badge> : null}
      {`${formatAge(role.firstSeenAt, asOf)}${years}`}
    </span>
  );
}

/** A badge with a hover and focus tooltip; focusable so the reason is reachable by keyboard. */
function TipBadge({ label, tip }: { label: string; tip: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span tabIndex={0} className="inline-flex rounded-md outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">
          <Badge variant="warn">{label}</Badge>
        </span>
      </TooltipTrigger>
      <TooltipContent>{tip}</TooltipContent>
    </Tooltip>
  );
}

/** The number with its confidence bars, or an amber Guess or Unknown badge. */
function SizeCell({ entry }: { entry: DailyEntry }) {
  const size = entry.size;
  if (!size) return <TipBadge label="Unknown" tip="No headcount source yet." />;
  if (size.confidence === 'guess') {
    return (
      <span className="inline-flex items-center gap-1.5">
        <span className="tabular-nums">{`about ${size.value}`}</span>
        <TipBadge label="Guess" tip={`A guess from ${size.sourceRef}. Shown, not hidden.`} />
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 tabular-nums">
      {size.value}
      <ConfidenceMeter confidence={size.confidence} />
    </span>
  );
}

export type ProposalActions = {
  onToggle: (e: DailyEntry) => void;
  onBacklog: (e: DailyEntry) => void;
  onRemove: (e: DailyEntry) => void;
};

/** At capacity an unpicked box is aria-disabled (still focusable, so the tooltip can say why). */
function PickBox({ entry, blockedText, onToggle }: { entry: DailyEntry; blockedText: string | null; onToggle: (e: DailyEntry) => void }) {
  const picked = entry.state === 'picked';
  const blocked = !picked && blockedText !== null;
  const box = (
    <Checkbox
      checked={picked}
      aria-label={`Pick ${entry.company.name}`}
      aria-disabled={blocked ? true : undefined}
      onCheckedChange={() => onToggle(entry)}
      className="aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
    />
  );
  if (!blocked) return box;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{box}</TooltipTrigger>
      <TooltipContent>{blockedText}</TooltipContent>
    </Tooltip>
  );
}

function RowActions({ entry, list, actions }: { entry: DailyEntry; list: 'proposed' | 'backlog'; actions: ProposalActions }) {
  // Moved to the backlog inside the undo window: relabelled in place until the list refreshes.
  if (list === 'proposed' && entry.state === 'backlog') return <Badge variant="idle" className={FLOAT}>Backlog</Badge>;
  const name = entry.company.name;
  return (
    <div className={cn(FLOAT, 'flex gap-1 bg-muted opacity-0 group-hover/row:opacity-100 group-focus-within/row:opacity-100 min-[1400px]:group-data-[active=true]/row:opacity-100')}>
      {list === 'proposed' ? (
        <Button variant="ghost" size="icon-sm" className="size-7" aria-label={`Move ${name} to the backlog`} onClick={() => actions.onBacklog(entry)}>
          <Archive aria-hidden />
        </Button>
      ) : null}
      <Button variant="ghost" size="icon-sm" className="size-7" aria-label={`Remove ${name}`} onClick={() => actions.onRemove(entry)}>
        <Trash2 aria-hidden />
      </Button>
    </div>
  );
}

type TableContext = {
  list: 'proposed' | 'backlog';
  asOf: IsoTime;
  blockedText: string | null;
  actions: ProposalActions;
};

// The cells read what they need from here, so the column definitions stay the same object across renders
// (a new cell function each render would remount every cell, and with it every open tooltip).
const Ctx = createContext<TableContext | null>(null);
function useTableContext(): TableContext {
  const v = useContext(Ctx);
  if (!v) throw new Error('cell outside ProposalsTable');
  return v;
}

function PickCell({ row }: { row: Row<DailyEntry> }) {
  const { blockedText, actions } = useTableContext();
  return <PickBox entry={row.original} blockedText={blockedText} onToggle={actions.onToggle} />;
}

function CompanyCell({ row: { original: e } }: { row: Row<DailyEntry> }) {
  const { list } = useTableContext();
  return (
    <>
      <div className="font-medium whitespace-nowrap">{e.company.name}</div>
      <div className={SUB}>{list === 'backlog' ? `${e.company.hq}. First proposed ${formatDueDay(e.firstProposedOn)}` : e.company.hq}</div>
    </>
  );
}

function RoleCell({ row: { original: e } }: { row: Row<DailyEntry> }) {
  const { asOf } = useTableContext();
  if (!e.role) return <span className={SUB}>No role to name</span>;
  return (
    <>
      <div>{e.role.title}</div>
      <RoleMeta role={e.role} asOf={asOf} />
    </>
  );
}

function ActionsCell({ row }: { row: Row<DailyEntry> }) {
  const { list, actions } = useTableContext();
  return <RowActions entry={row.original} list={list} actions={actions} />;
}

const COLUMNS: ColumnDef<DailyEntry>[] = [
  { id: 'pick', header: () => <span className="sr-only">Pick</span>, cell: PickCell },
  { id: 'company', header: 'Company', cell: CompanyCell },
  { id: 'role', header: 'Role to name', cell: RoleCell },
  { id: 'size', header: 'Size', cell: ({ row }) => <SizeCell entry={row.original} /> },
  {
    id: 'sponsorship',
    header: 'Sponsorship',
    // Under 1400 px the row actions float over this cell, so the badge steps aside while they show.
    cell: ({ row }) => (
      <span className="inline-flex max-[1399px]:group-hover/row:invisible max-[1399px]:group-focus-within/row:invisible">
        <StatusBadge status={sponsorshipStatus[row.original.sponsorship]} dot />
      </span>
    ),
  },
  { id: 'actions', header: () => <span className="sr-only">Actions</span>, cell: ActionsCell },
];

const getRowId = (e: DailyEntry) => String(e.company.id);

/**
 * The Proposed or Backlog list: group rows in the order the API returns them, then one row per company.
 * Every value shown is stored or derived by the client; the table only lays it out.
 */
export function ProposalsTable({
  entries,
  list,
  asOf,
  blockedText,
  rowProps,
  actions,
}: {
  entries: DailyEntry[];
  list: 'proposed' | 'backlog';
  asOf: IsoTime;
  /** The capacity reason while every pick is used, else null. */
  blockedText: string | null;
  rowProps: (id: number) => RowProps;
  actions: ProposalActions;
}) {
  // TanStack Table returns fresh functions each render; nothing here memoizes on them.
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({ data: entries, columns: COLUMNS, getCoreRowModel: getCoreRowModel(), getRowId });

  const groups = groupInOrder(table.getRowModel().rows, (row) => row.original.listGroup);

  return (
    <Ctx.Provider value={{ list, asOf, blockedText, actions }}>
      <div className="overflow-hidden rounded-md border bg-card">
        <Table aria-label={list === 'proposed' ? 'Proposed companies' : 'Backlog'}>
          <TableHeader>
            {table.getHeaderGroups().map((hg) => (
              <TableRow key={hg.id} className="hover:bg-transparent">
                {hg.headers.map((h) => (
                  <TableHead key={h.id} className={cn(HEAD, (h.id === 'pick' || h.id === 'actions') && 'w-px', h.id === 'actions' && 'px-0')}>
                    {flexRender(h.column.columnDef.header, h.getContext())}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {groups.map(({ group, items: rows }) => [
              <TableRow key={`group-${group}`} className="hover:bg-transparent">
                <TableCell colSpan={COLUMNS.length} className="h-8 bg-subtle px-3 text-xs font-medium text-muted-foreground">
                  {GROUP_LABEL[group]} <span className="ml-1 tabular-nums">{rows.length}</span>
                </TableCell>
              </TableRow>,
              ...rows.map((row) => (
                <TableRow
                  key={row.id}
                  {...rowProps(row.original.company.id)}
                  className="group/row cursor-default data-[active=true]:bg-muted data-[active=true]:shadow-[inset_2px_0_0_var(--foreground)]"
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id} className={cn(CELL, cell.column.id === 'pick' && 'w-px pr-0', cell.column.id === 'sponsorship' && 'min-[1400px]:pr-20', cell.column.id === 'actions' && 'relative w-0 p-0')}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              )),
            ])}
          </TableBody>
        </Table>
      </div>
    </Ctx.Provider>
  );
}
