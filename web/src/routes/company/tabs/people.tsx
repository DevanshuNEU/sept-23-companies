import { Link } from 'react-router';
import type { CompanyView, RoleKind } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { formatDayTime } from '@/lib/format';

const KIND: Record<RoleKind, string> = {
  engineer: 'Engineer',
  manager: 'Manager',
  founder: 'Founder',
  recruiter: 'Recruiter',
  alum: 'Alum',
  other: 'Other',
};

/** Everyone known here, by email or on LinkedIn. A reply is the one state that waits on him, so it links to the Outbox. */
export function People({ view }: { view: CompanyView }) {
  if (view.people.length === 0) return <p className="px-3 py-6 text-sm text-muted-foreground">No people yet. Paste them in the side terminal.</p>;
  const touchOf = (personId: number) => view.touches.find((t) => t.person.id === personId);
  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Person</TableHead>
            <TableHead>Kind</TableHead>
            <TableHead>Channel</TableHead>
            <TableHead>State</TableHead>
            <TableHead>Last touch</TableHead>
            <TableHead className="w-px" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {view.people.map((p) => {
            const touch = p.channel === 'email' ? touchOf(p.person.id) : undefined;
            return (
              <TableRow key={`${p.channel}-${p.person.id}`}>
                <TableCell>
                  <div className="font-medium">{p.person.name}</div>
                  <div className="text-xs text-muted-foreground">{p.person.email ?? 'No email'}</div>
                </TableCell>
                <TableCell>
                  <Badge variant={p.person.roleKind === 'recruiter' ? 'warn' : 'outline'}>{KIND[p.person.roleKind]}</Badge>
                </TableCell>
                <TableCell>{p.channel === 'email' ? 'Email' : 'LinkedIn'}</TableCell>
                <TableCell>
                  <Badge variant={p.tone}>{p.statusLine}</Badge>
                </TableCell>
                <TableCell className="text-[13px] tabular-nums">{p.lastTouchAt ? formatDayTime(p.lastTouchAt) : ''}</TableCell>
                <TableCell>
                  {touch?.state === 'replied' ? (
                    <Button variant="ghost" size="sm" asChild>
                      <Link to={`/outbox/${touch.touchId}`}>Open in Outbox</Link>
                    </Button>
                  ) : p.channel === 'linkedin' ? (
                    <Button variant="ghost" size="sm" asChild>
                      <Link to="/linkedin">LinkedIn list</Link>
                    </Button>
                  ) : null}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
