import type { CompanyView, LevelTier, PostingRow } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { formatDay } from '@/lib/format';
import type { Tone } from '@/lib/status';

const LEVEL: Record<LevelTier, string> = {
  new_grad: 'New grad',
  early_career: 'Early career',
  swe1: 'Engineer I',
  swe2: 'Engineer II',
  open: 'Any level',
  not_stated: 'Level not stated',
  senior: 'Senior',
};

const STANCE: Record<PostingRow['sponsorship'], { label: string; tone: Tone }> = {
  yes: { label: 'Sponsors', tone: 'ok' },
  no: { label: 'Does not sponsor', tone: 'bad' },
  opt_only: { label: 'OPT only', tone: 'warn' },
  unclear: { label: 'Unclear', tone: 'warn' },
  silent: { label: 'Not stated', tone: 'idle' },
};

function State({ p }: { p: PostingRow }) {
  if (p.state === 'open') return <Badge variant="ok">Open</Badge>;
  if (p.state === 'closed') {
    return (
      <span className="grid gap-0.5">
        <Badge variant="idle">{p.closedAt ? `Closed ${formatDay(p.closedAt)}` : 'Closed'}</Badge>
        {p.closeReason ? <span className="text-xs text-muted-foreground">{p.closeReason}</span> : null}
      </span>
    );
  }
  return (
    <span className="grid gap-0.5">
      <Badge variant="idle">{p.state === 'hidden' ? 'Hidden' : 'Out of lane'}</Badge>
      {p.hiddenRule ? <span className="text-xs text-muted-foreground">{p.hiddenRule}</span> : null}
    </span>
  );
}

/** Every posting the company ever had, open and closed. Nothing is deleted. */
export function Roles({ view }: { view: CompanyView }) {
  if (view.postings.length === 0) return <p className="px-3 py-6 text-sm text-muted-foreground">No postings seen for this company yet.</p>;
  return (
    <div className="grid gap-2">
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Posting</TableHead>
              <TableHead>Level</TableHead>
              <TableHead>Where</TableHead>
              <TableHead>Sponsorship</TableHead>
              <TableHead>First seen</TableHead>
              <TableHead>Last seen</TableHead>
              <TableHead>State</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {view.postings.map((p) => (
              <TableRow key={p.id}>
                <TableCell className="font-medium">
                  <a href={p.url} target="_blank" rel="noreferrer" className="underline decoration-border underline-offset-3 hover:decoration-current">
                    {p.title}
                  </a>
                </TableCell>
                <TableCell>
                  <Badge variant="outline">{LEVEL[p.levelTier]}</Badge>
                </TableCell>
                <TableCell>{p.location}</TableCell>
                <TableCell>
                  <Badge variant={STANCE[p.sponsorship].tone}>{STANCE[p.sponsorship].label}</Badge>
                </TableCell>
                <TableCell className="tabular-nums">{formatDay(p.firstSeenAt)}</TableCell>
                <TableCell className="tabular-nums">{formatDay(p.lastSeenAt)}</TableCell>
                <TableCell>
                  <State p={p} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <p className="text-xs text-muted-foreground">
        {`Decided by rules version ${view.rulesetVersion}. The app shows stored decisions and never classifies anything itself.`}
      </p>
    </div>
  );
}
