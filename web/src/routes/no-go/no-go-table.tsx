import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from '@tanstack/react-table';
import { MoreHorizontal } from 'lucide-react';
import type { NoGoReason, NoGoRow, NoGoSource, RemovedRow, RemoveReason } from '@/api';
import { StatusBadge } from '@/components/status-badge';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import type { RowProps } from '@/keyboard/list-nav';
import { formatDay } from '@/lib/format';
import { noGoReasonStatus, type Status } from '@/lib/status';
import { cn } from '@/lib/utils';
import { safeHref } from '@/lib/safe-href';

export type CompanyEntry = NoGoRow | RemovedRow;

const HEAD = 'h-9 bg-subtle px-3 text-[13px] text-muted-foreground';
const CELL = 'h-9 px-3 py-1.5 text-[13px] whitespace-normal';
const SUB = 'text-xs whitespace-nowrap text-muted-foreground';
const QUOTE = 'border-l-2 pl-2.5 text-[13px] text-muted-foreground';

const REMOVED: Status = { label: 'Removed by you', tone: 'idle' };

// The four reasons of the Remove dialog on Today, in its words.
const REMOVE_REASON: Record<RemoveReason, string> = {
  not_a_fit: 'Not a fit for me',
  already_in_touch: 'Already applied or in touch another way',
  bigger_than_it_looks: 'Bigger than it looks',
  other: 'Other',
};

const SOURCE_WORD: Record<NoGoSource, string> = {
  devanshu: 'You',
  rules: 'Rules',
  reply: 'Reply',
  import: 'Imported',
  claude: 'Claude',
};

/** A stable id per row: no-go entries by their id, removed companies by the company id. */
// eslint-disable-next-line react-refresh/only-export-components
export const entryId = (row: CompanyEntry): string => (row.kind === 'no_go' ? `n${row.noGoId}` : `r${row.company.id}`);

// eslint-disable-next-line react-refresh/only-export-components
export const entryName = (row: CompanyEntry): string =>
  row.kind === 'removed' ? row.company.name : row.target.kind === 'company' ? row.target.company.name : row.target.person.name;

/** The company page a row points at; people carry only their company's name. */
// eslint-disable-next-line react-refresh/only-export-components
export const entryCompanyId = (row: CompanyEntry): number | null =>
  row.kind === 'removed' ? row.company.id : row.target.kind === 'company' ? row.target.company.id : null;

/** "Oct 14": the day an entry was added, in the app's zone. */
const shortDay = (at: string) => formatDay(at).replace(/^\S+ /, '');

/** The chip word for a count bucket. */
// eslint-disable-next-line react-refresh/only-export-components
export const reasonLabel = (reason: NoGoReason | 'removed'): string =>
  reason === 'removed' ? REMOVED.label : noGoReasonStatus[reason].label;

/** The badge: the stored reason in the status words; "other" says who put it there. */
function reasonOf(row: CompanyEntry): Status {
  if (row.kind === 'removed') return REMOVED;
  if (row.reason === 'other' && row.source === 'devanshu') {
    return { label: row.target.kind === 'company' ? 'Marked no-go by you' : 'Marked by you', tone: 'idle' };
  }
  if (row.reason === 'other' && row.source === 'claude') return { label: 'Flagged by Claude', tone: 'idle' };
  return noGoReasonStatus[row.reason];
}

function removeReasonLabel(reason: string): string {
  return Object.hasOwn(REMOVE_REASON, reason) ? REMOVE_REASON[reason as RemoveReason] : reason;
}

function SourceLink({ row }: { row: NoGoRow }) {
  const href = safeHref(row.sourceUrl);
  if (!href) return <span>{row.sourceRef}</span>;
  return (
    <a className="underline-offset-4 hover:underline" href={href} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
      {row.sourceRef}
    </a>
  );
}

function CompanyEvidence({ row }: { row: CompanyEntry }) {
  if (row.kind === 'removed') return <span>{row.note}</span>;
  if (row.quote) return <span className={QUOTE}>{`"${row.quote}"`}</span>;
  return <span>{row.note}</span>;
}

function CompanySource({ row }: { row: CompanyEntry }) {
  if (row.kind === 'removed') {
    return (
      <>
        <div>You, from Today</div>
        <div className={SUB}>{removeReasonLabel(row.removeReason)}</div>
      </>
    );
  }
  if (row.source === 'import') {
    return (
      <>
        <span className="rounded-sm bg-muted px-1.5 py-px text-[11px] font-medium whitespace-nowrap text-muted-foreground">Imported</span>
        <div className={SUB}>{row.sourceRef}</div>
      </>
    );
  }
  // A quote fills the evidence, so a note (who closed it) moves under the source.
  const who = row.quote && row.note ? row.note : SOURCE_WORD[row.source];
  const sub = who === row.sourceRef ? shortDay(row.addedAt) : `${who}, ${shortDay(row.addedAt)}`;
  return (
    <>
      <SourceLink row={row} />
      <div className={SUB}>{sub}</div>
    </>
  );
}

function PersonSaid({ row }: { row: NoGoRow }) {
  const quoted = row.quote ? `"${row.quote}"` : '';
  const text = [row.note, quoted].filter(Boolean).join(' ');
  return <span className={row.quote && !row.note ? QUOTE : undefined}>{text}</span>;
}

export type EntryActions = {
  onOpenSource: (row: CompanyEntry) => void;
  onOpenCompany: (companyId: number) => void;
  onTakeOff: (row: CompanyEntry) => void;
};

function RowMenu({ row, actions }: { row: CompanyEntry; actions: EntryActions }) {
  const companyId = row.kind === 'no_go' && row.target.kind === 'person' ? null : entryCompanyId(row);
  const hasSource = row.kind === 'no_go' && row.sourceUrl !== null;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" className="size-7" aria-label="More" onClick={(e) => e.stopPropagation()}>
          <MoreHorizontal aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {hasSource ? <DropdownMenuItem onSelect={() => actions.onOpenSource(row)}>Open the source</DropdownMenuItem> : null}
        {companyId !== null ? <DropdownMenuItem onSelect={() => actions.onOpenCompany(companyId)}>Open company page</DropdownMenuItem> : null}
        <DropdownMenuItem onSelect={() => actions.onTakeOff(row)}>{row.kind === 'removed' ? 'Bring back' : 'Take off the list'}</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

const added = (row: CompanyEntry) => (
  <span className="whitespace-nowrap text-muted-foreground tabular-nums">{shortDay(row.kind === 'removed' ? row.removedAt : row.addedAt)}</span>
);

function companyColumns(actions: EntryActions): ColumnDef<CompanyEntry>[] {
  return [
    { id: 'company', header: 'Company', cell: ({ row }) => <span className="font-medium whitespace-nowrap">{entryName(row.original)}</span> },
    { id: 'reason', header: 'Reason', cell: ({ row }) => <StatusBadge status={reasonOf(row.original)} dot /> },
    { id: 'evidence', header: 'Evidence', cell: ({ row }) => <CompanyEvidence row={row.original} /> },
    { id: 'source', header: 'Source', cell: ({ row }) => <CompanySource row={row.original} /> },
    { id: 'added', header: 'Added', cell: ({ row }) => added(row.original) },
    { id: 'actions', header: () => <span className="sr-only">Actions</span>, cell: ({ row }) => <RowMenu row={row.original} actions={actions} /> },
  ];
}

function personColumns(actions: EntryActions): ColumnDef<NoGoRow>[] {
  return [
    {
      id: 'person',
      header: 'Person',
      cell: ({ row: { original: r } }) =>
        r.target.kind === 'person' ? (
          <>
            <div className="font-medium whitespace-nowrap">{r.target.person.name}</div>
            <div className={SUB}>{`${r.target.person.title}, ${r.target.companyName}`}</div>
          </>
        ) : null,
    },
    { id: 'reason', header: 'Reason', cell: ({ row }) => <StatusBadge status={reasonOf(row.original)} dot /> },
    { id: 'said', header: 'What they said', cell: ({ row }) => <PersonSaid row={row.original} /> },
    { id: 'source', header: 'Source', cell: ({ row }) => <SourceLink row={row.original} /> },
    { id: 'added', header: 'Added', cell: ({ row }) => added(row.original) },
    { id: 'actions', header: () => <span className="sr-only">Actions</span>, cell: ({ row }) => <RowMenu row={row.original} actions={actions} /> },
  ];
}

function EntryTable<T extends CompanyEntry>({
  label,
  rows,
  columns,
  rowProps,
}: {
  label: string;
  rows: T[];
  columns: ColumnDef<T>[];
  rowProps: (id: string) => RowProps;
}) {
  // TanStack Table returns fresh functions each render; nothing here memoizes on them.
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({ data: rows, columns, getCoreRowModel: getCoreRowModel(), getRowId: entryId });
  return (
    <div className="overflow-hidden rounded-md border bg-card">
      <Table aria-label={label}>
        <TableHeader>
          {table.getHeaderGroups().map((hg) => (
            <TableRow key={hg.id} className="hover:bg-transparent">
              {hg.headers.map((h) => (
                <TableHead key={h.id} className={cn(HEAD, h.id === 'actions' && 'w-px')}>
                  {flexRender(h.column.columnDef.header, h.getContext())}
                </TableHead>
              ))}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.map((row) => (
            <TableRow
              key={row.id}
              {...rowProps(row.id)}
              className="group/row cursor-default data-[active=true]:bg-muted data-[active=true]:shadow-[inset_2px_0_0_var(--foreground)]"
            >
              {row.getVisibleCells().map((cell) => (
                <TableCell key={cell.id} className={cn(CELL, cell.column.id === 'actions' && 'w-px')}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

/** Companies kept out: no-go entries (liftable) and companies removed by you (restorable), as the API orders them. */
export function CompaniesTable({ rows, rowProps, actions }: { rows: CompanyEntry[]; rowProps: (id: string) => RowProps; actions: EntryActions }) {
  return <EntryTable label="No-go companies" rows={rows} columns={companyColumns(actions)} rowProps={rowProps} />;
}

/** People kept out, with what they said. */
export function PeopleTable({ rows, rowProps, actions }: { rows: NoGoRow[]; rowProps: (id: string) => RowProps; actions: EntryActions }) {
  return <EntryTable label="No-go people" rows={rows} columns={personColumns(actions)} rowProps={rowProps} />;
}
