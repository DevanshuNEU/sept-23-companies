import { Link } from 'react-router';
import type { StillEmailing } from '@/api';
import { StatusBadge } from '@/components/status-badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import type { Status } from '@/lib/status';

// The words of design 5.1 for each stored state.
const STATE: Record<StillEmailing['state'], Status> = {
  emailing: { label: 'Emailing', tone: 'info' },
  held: { label: 'Held: reply', tone: 'ok' },
  paused: { label: 'Paused', tone: 'idle', icon: 'pause' },
  waiting: { label: 'Waiting', tone: 'idle' },
};

const HEAD = 'h-9 bg-subtle px-3 text-[13px] text-muted-foreground';
const CELL = 'h-9 px-3 py-1.5 text-[13px]';

/** Companies picked on earlier days that are not closed: day, emailed against budget, the next step in words. */
export function StillEmailingTable({ rows }: { rows: StillEmailing[] }) {
  return (
    <div className="overflow-hidden rounded-md border bg-card">
      <Table aria-label="Still emailing">
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className={HEAD}>Company</TableHead>
            <TableHead className={HEAD}>Day</TableHead>
            <TableHead className={HEAD}>Emailed</TableHead>
            <TableHead className={HEAD}>Next</TableHead>
            <TableHead className={`${HEAD} w-px text-right`}>State</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.company.id}>
              <TableCell className={`${CELL} font-medium`}>
                <Link to={`/companies/${r.company.id}`} className="outline-none hover:underline focus-visible:underline">
                  {r.company.name}
                </Link>
              </TableCell>
              <TableCell className={`${CELL} tabular-nums`}>{`Day ${r.day} of ${r.days}`}</TableCell>
              <TableCell className={`${CELL} tabular-nums`}>{`${r.emailed} of ${r.budget}`}</TableCell>
              <TableCell className={`${CELL} whitespace-normal`}>{r.nextStep.text}</TableCell>
              <TableCell className={`${CELL} text-right`}>
                <StatusBadge status={STATE[r.state]} dot />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
