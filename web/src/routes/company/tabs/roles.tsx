import { Fragment } from 'react';
import type { CompanyView, LevelTier, PostingFlag, PostingRow } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { formatDay } from '@/lib/format';
import type { Tone } from '@/lib/status';
import { safeHref } from '@/lib/safe-href';

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

const FLAG_FIELD: Record<string, string> = { level_tier: 'Level', level: 'Level', sponsorship: 'Sponsorship', location: 'Location' };
const words = (v: string) => v.replaceAll('_', ' ');

/** "Level" for `level_tier`, and any field the table does not know in plain words. */
function fieldLabel(field: string): string {
  const known = FLAG_FIELD[field];
  if (known) return known;
  const w = words(field);
  return w.charAt(0).toUpperCase() + w.slice(1);
}

/** What one side read: its value and the words it came from. A side with nothing says so, and an empty quote says "No quote." */
function Side({ who, value, quote }: { who: 'reader' | 'rules'; value: string | null; quote: string }) {
  return (
    <div className="grid content-start gap-1">
      <p className="text-[13px]">{`The ${who} ${who === 'reader' ? 'says' : 'say'} ${value === null ? 'nothing' : words(value)}`}</p>
      {quote === '' ? (
        <p className="text-xs text-muted-foreground">No quote.</p>
      ) : (
        <blockquote className="max-w-[62ch] border-l-2 pl-3 text-[13px] leading-[20px] text-muted-foreground">{`"${quote}"`}</blockquote>
      )}
    </div>
  );
}

/** The fields where the posting reader and the rules disagree, each with both readings and both quotes. */
function Flags({ posting }: { posting: PostingRow }) {
  return (
    <div className="grid gap-2">
      <p className="text-xs text-muted-foreground">The reader and the rules read this posting differently.</p>
      <ul aria-label={`Where the reader and the rules disagree for ${posting.title}`} className="grid gap-3">
        {posting.flags.map((f: PostingFlag) => (
          <li key={f.field} className="grid gap-1.5">
            <span className="text-sm font-medium">{fieldLabel(f.field)}</span>
            <div className="grid gap-3 md:grid-cols-2">
              <Side who="reader" value={f.readerValue} quote={f.readerQuote} />
              <Side who="rules" value={f.rulesValue} quote={f.rulesQuote} />
            </div>
          </li>
        ))}
      </ul>
    </div>
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
              <Fragment key={p.id}>
                <TableRow className={p.flags.length > 0 ? 'border-b-0' : undefined}>
                  <TableCell className="font-medium">
                    {safeHref(p.url) ? (
                      <a href={safeHref(p.url)} target="_blank" rel="noreferrer" className="underline decoration-border underline-offset-3 hover:decoration-current">
                        {p.title}
                      </a>
                    ) : (
                      p.title
                    )}
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
                {p.flags.length > 0 ? (
                  <TableRow className="bg-muted/30 hover:bg-muted/30">
                    <TableCell colSpan={7} className="whitespace-normal pt-1 pb-3">
                      <Flags posting={p} />
                    </TableCell>
                  </TableRow>
                ) : null}
              </Fragment>
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
