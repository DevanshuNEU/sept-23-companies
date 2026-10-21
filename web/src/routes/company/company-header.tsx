import type { ReactNode } from 'react';
import { Lock, MoreHorizontal, Pause } from 'lucide-react';
import type { CompanyView, Fact as FactRow } from '@/api';
import { ConfidenceMeter } from '@/components/confidence-meter';
import { StatusBadge } from '@/components/status-badge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Kbd } from '@/components/ui/kbd';
import { formatDayTime, formatDueDay } from '@/lib/format';
import { readResultStatus, sponsorshipStatus, type Tone } from '@/lib/status';
import { cn } from '@/lib/utils';
import { safeHref } from '@/lib/safe-href';
import { companyActions, headerStatuses } from './company-status';

const DOT: Record<Tone, string> = {
  ok: 'bg-ok-dot',
  info: 'bg-info-dot',
  warn: 'bg-warn-dot',
  bad: 'bg-bad-dot',
  idle: 'bg-idle-dot',
};

export type CompanyHandlers = { onPause: () => void; onResume: () => void; onClose: () => void; onReopen: () => void };

export function CompanyHeader({ view, handlers }: { view: CompanyView; handlers: CompanyHandlers }) {
  const c = view.company;
  const can = companyActions(c);
  const boardUrl = safeHref(view.board?.url);
  return (
    <header className="mb-4 grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-6 gap-y-3">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">{c.name}</h1>
          <div role="group" aria-label="Status" className="flex flex-wrap items-center gap-2">
            {headerStatuses(c).map((s) => (
              <StatusBadge key={s.label} status={s} />
            ))}
          </div>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">{view.summary}</p>
      </div>
      <div className="flex items-center gap-2">
        {can.resume ? (
          <Button variant="outline" onClick={handlers.onResume}>
            Resume
            <Kbd>R</Kbd>
          </Button>
        ) : null}
        {can.pause ? (
          <Button variant="outline" onClick={handlers.onPause}>
            <Pause aria-hidden />
            Pause
            <Kbd>P</Kbd>
          </Button>
        ) : null}
        {can.close ? (
          <Button onClick={handlers.onClose}>
            Close with a reason
            <Kbd>C</Kbd>
          </Button>
        ) : null}
        {can.reopen || boardUrl ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="More actions">
                <MoreHorizontal aria-hidden />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {can.reopen ? <DropdownMenuItem onSelect={handlers.onReopen}>Reopen</DropdownMenuItem> : null}
              {boardUrl ? (
                <DropdownMenuItem asChild>
                  <a href={boardUrl} target="_blank" rel="noreferrer">
                    Open careers page
                  </a>
                </DropdownMenuItem>
              ) : null}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </div>
    </header>
  );
}

function Cell({ label, sub, children }: { label: string; sub?: ReactNode; children: ReactNode }) {
  return (
    <div role="group" aria-label={label} className="grid min-w-0 gap-0.5 border-r px-3.5 py-2.5 last:border-r-0">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="flex items-center gap-1.5 text-sm font-medium">{children}</span>
      {sub ? <span className="truncate text-xs text-muted-foreground">{sub}</span> : null}
    </div>
  );
}

/** Where a fact came from, in a few words: "You, Fri Oct 30" or "New grad posting, Tue Oct 20". */
function source(f: FactRow | undefined): string | null {
  if (!f) return null;
  const who = f.sourceKind === 'hand' ? 'You' : f.sourceRef;
  const text = f.observedOn ? `${who}, ${formatDueDay(f.observedOn)}` : who;
  return text || null;
}

function mailServer(ok: boolean | null): string {
  return ok === null ? 'Mail server not checked' : ok ? 'Mail server found' : 'No mail server found';
}

/** Size, stage, sponsorship, email domain and job board, plus E-Verify when the company has that fact. */
export function FactsStrip({ view }: { view: CompanyView }) {
  const find = (kind: FactRow['fact']) => view.facts.find((f) => f.fact === kind);
  const size = find('size');
  const stage = find('stage');
  const sponsorship = find('sponsorship');
  const domain = find('domain');
  const everify = find('everify');
  const board = view.board;
  const read = board?.lastRead ? readResultStatus[board.lastRead] : null;
  const unknown = (text: string) => <span className="text-muted-foreground">{text}</span>;
  return (
    <section aria-label="Facts" className="mb-5 grid overflow-hidden rounded-md border sm:grid-cols-[repeat(auto-fit,minmax(11rem,1fr))]">
      <Cell label="Size" sub={source(size)}>
        {size ? (
          <>
            <span>{size.value}</span>
            <ConfidenceMeter confidence={size.confidence} />
          </>
        ) : (
          unknown('Unknown')
        )}
      </Cell>
      <Cell label="Stage" sub={source(stage)}>
        {stage ? stage.value : unknown('Not known')}
      </Cell>
      <Cell label="Sponsorship" sub={source(sponsorship)}>
        <StatusBadge status={sponsorshipStatus[view.sponsorshipShown]} />
      </Cell>
      <Cell label="Email domain" sub={view.company.domain ? mailServer(view.mailServerOk) : null}>
        {view.company.domain ? (
          <>
            <span className="truncate">{view.company.domain}</span>
            {domain?.locked ? <Lock role="img" aria-label="Locked" className="size-3.5 shrink-0 text-muted-foreground" /> : null}
          </>
        ) : (
          <Badge variant="warn">No domain</Badge>
        )}
      </Cell>
      <Cell label="Job board" sub={board?.lastReadAt ? `Read ${formatDayTime(board.lastReadAt)}` : board ? 'Never read' : null}>
        {board ? (
          <>
            {read ? <span aria-hidden className={cn('size-2 shrink-0 rounded-full', DOT[read.tone])} /> : null}
            <span>{read ? `${board.ats}, ${read.label}` : board.ats}</span>
          </>
        ) : (
          unknown('No board known')
        )}
      </Cell>
      {everify ? (
        <Cell label="E-Verify" sub={source(everify)}>
          {everify.value}
        </Cell>
      ) : null}
    </section>
  );
}
