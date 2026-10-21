import type { ReactNode } from 'react';
import { Link } from 'react-router';
import { Copy, ExternalLink, MousePointerClick, Terminal } from 'lucide-react';
import type { Confidence, DailyEntry, Fact, IsoTime } from '@/api';
import { ConfidenceMeter } from '@/components/confidence-meter';
import { EmptyState } from '@/components/state-views';
import { StatusBadge } from '@/components/status-badge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Kbd } from '@/components/ui/kbd';
import { ageDays, formatDay, formatDueDay } from '@/lib/format';
import { sponsorshipStatus, type Status } from '@/lib/status';
import { safeHref } from '@/lib/safe-href';
import { RoleMeta } from './proposals-table';

const STATE: Record<DailyEntry['state'], Status> = {
  proposed: { label: 'Proposed', tone: 'idle' },
  picked: { label: 'Picked', tone: 'info' },
  backlog: { label: 'Backlog', tone: 'idle' },
  removed: { label: 'Removed', tone: 'idle' },
};
const CONFIDENCE_WORD: Record<Confidence, string> = { confirmed: 'high confidence', estimate: 'medium confidence', guess: 'a guess' };

const LABEL = 'text-[13px] font-medium';

/** "LinkedIn and the careers page, Mon Nov 2": where a fact came from and when. */
function sourceOf(f: Fact): string {
  return [f.sourceRef, f.observedOn ? formatDueDay(f.observedOn) : ''].filter(Boolean).join(', ');
}

function Ev({ k, children, src }: { k: string; children: ReactNode; src?: string }) {
  return (
    <div className="grid grid-cols-[84px_1fr] gap-x-2.5 gap-y-0.5 border-b px-3 py-2 text-[13px] last:border-b-0">
      <span className="text-muted-foreground">{k}</span>
      <span className="flex flex-wrap items-center gap-1.5">{children}</span>
      {src ? <span className="col-start-2 text-xs text-muted-foreground">{src}</span> : null}
    </div>
  );
}

function Evidence({ e }: { e: DailyEntry }) {
  const size = e.size;
  const sponsor = e.sponsorshipFact;
  return (
    <div className="grid overflow-hidden rounded-md border">
      <Ev k="Size" src={size ? sourceOf(size) : undefined}>
        {size ? (
          <>
            <span>{size.confidence === 'guess' ? `about ${size.value} people` : `${size.value} people`}</span>
            {size.confidence === 'guess' ? <Badge variant="warn">Guess</Badge> : <ConfidenceMeter confidence={size.confidence} />}
          </>
        ) : (
          <Badge variant="warn">Unknown</Badge>
        )}
      </Ev>
      {e.stage ? (
        <Ev k="Stage" src={`${e.stage.sourceRef}, ${CONFIDENCE_WORD[e.stage.confidence]}`}>
          {e.stage.value}
        </Ev>
      ) : null}
      <Ev k="Sponsorship" src={sponsor ? sourceOf(sponsor) : undefined}>
        {sponsor ? <span>{sponsor.value}</span> : <StatusBadge status={sponsorshipStatus[e.sponsorship]} dot />}
        {sponsor?.quote ? <span className="w-full text-xs text-muted-foreground">{`"${sponsor.quote}"`}</span> : null}
      </Ev>
      <Ev k="Email domain" src={e.domainFact?.observedOn ? `Checked ${formatDueDay(e.domainFact.observedOn)}` : undefined}>
        {e.company.domain ? <span>{e.company.domain}</span> : <Badge variant="warn">No domain</Badge>}
        {e.mailServerOk === true ? <Badge variant="ok">Mail server found</Badge> : null}
        {e.mailServerOk === false ? <Badge variant="bad">No mail server</Badge> : null}
      </Ev>
    </div>
  );
}

function People({ e, handoff, onCopyHandoff }: { e: DailyEntry; handoff: string; onCopyHandoff: () => void }) {
  const people = e.peopleCount === 1 ? '1 person added' : `${e.peopleCount} people added`;
  const drafts = e.draftCount === 1 ? '1 draft is in the Outbox' : `${e.draftCount} drafts are in the Outbox`;
  return (
    <div className="grid gap-2">
      <span className={LABEL}>People</span>
      <p className="text-sm text-muted-foreground">{e.peopleCount === 0 ? 'No people added yet.' : `${people}. ${drafts}.`}</p>
      <span className="text-xs text-muted-foreground">
        {`To add ${e.peopleCount === 0 ? 'people' : 'more'}, paste this in the side terminal with the people:`}
      </span>
      <pre className="rounded-sm bg-muted px-3 py-2.5 font-mono text-xs leading-[18px] whitespace-pre-wrap text-foreground">{handoff}</pre>
      <Button variant="outline" size="sm" className="justify-self-start" onClick={onCopyHandoff}>
        <Terminal aria-hidden />
        Copy for Claude
      </Button>
    </div>
  );
}

export type PreviewActions = {
  onToggle: () => void;
  onBacklog: () => void;
  onRemove: () => void;
  onCopyTitle: () => void;
  onCopyHandoff: () => void;
};

/** The active row in full (design 5.1): why it was proposed, the role to name, evidence, people, actions. */
export function PreviewPanel({
  entry: e,
  list,
  asOf,
  handoff,
  actions,
}: {
  entry: DailyEntry | null;
  list: 'proposed' | 'backlog';
  asOf: IsoTime;
  handoff: string;
  actions: PreviewActions;
}) {
  if (!e) {
    return (
      <aside aria-label="Preview" className="min-h-0 overflow-y-auto border-l">
        <EmptyState icon={MousePointerClick} title="No company selected">
          Press J to start at the top, or click a row.
        </EmptyState>
      </aside>
    );
  }
  const where = [e.company.domain, e.company.hq].filter(Boolean).join(', ');
  return (
    <aside aria-label={`${e.company.name} preview`} className="grid min-h-0 auto-rows-min content-start gap-4 overflow-y-auto border-l p-5">
      <div className="flex items-start gap-2">
        <div className="min-w-0 grow">
          <h2 className="text-base font-semibold">{e.company.name}</h2>
          <p className="text-sm text-muted-foreground">{where}</p>
        </div>
        <StatusBadge status={STATE[e.state]} dot />
      </div>
      <p className="text-sm">{e.why}</p>

      {e.role ? (
        <div className="grid gap-1.5 rounded-md border p-3">
          <span className="text-xs text-muted-foreground">Role to name</span>
          <span className="text-sm font-semibold">{e.role.title}</span>
          <RoleMeta role={e.role} asOf={asOf} />
          <span className="text-xs text-muted-foreground">{`${e.role.location}. Posted ${formatDay(e.role.firstSeenAt)}.`}</span>
          <div className="mt-1 flex gap-2">
            <Button variant="outline" size="sm" onClick={actions.onCopyTitle}>
              <Copy aria-hidden />
              Copy title
              <Kbd aria-hidden>C</Kbd>
            </Button>
            {safeHref(e.role.url) ? (
              <Button variant="ghost" size="sm" asChild>
                <a href={safeHref(e.role.url)} target="_blank" rel="noreferrer">
                  <ExternalLink aria-hidden />
                  Open posting
                  <Kbd aria-hidden>O</Kbd>
                </a>
              </Button>
            ) : null}
          </div>
        </div>
      ) : (
        <p className="rounded-md border p-3 text-sm text-muted-foreground">No role to name yet.</p>
      )}

      <div className="grid gap-2">
        <span className={LABEL}>Evidence</span>
        <Evidence e={e} />
      </div>

      {e.otherRoles.length > 0 ? (
        <div className="grid gap-2">
          <span className={LABEL}>
            Other open roles <span className="font-normal text-muted-foreground">{e.otherRoles.length}</span>
          </span>
          <ul className="grid gap-1 text-sm">
            {e.otherRoles.map((r) => {
              const days = ageDays(r.firstSeenAt, asOf);
              return (
                <li key={r.title} className="flex items-center gap-2">
                  <span className="grow">{r.title}</span>
                  <span className="text-xs text-muted-foreground tabular-nums">{days === 1 ? '1 day' : `${days} days`}</span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      <People e={e} handoff={handoff} onCopyHandoff={actions.onCopyHandoff} />

      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={actions.onToggle}>
          {e.state === 'picked' ? 'Unpick' : 'Pick'}
          <Kbd aria-hidden>X</Kbd>
        </Button>
        {list === 'proposed' && e.state !== 'backlog' ? (
          <Button variant="ghost" size="sm" onClick={actions.onBacklog}>
            Backlog
            <Kbd aria-hidden>B</Kbd>
          </Button>
        ) : null}
        <Button variant="ghost" size="sm" onClick={actions.onRemove}>
          Remove
          <Kbd aria-hidden>R</Kbd>
        </Button>
        <Button variant="ghost" size="sm" asChild>
          <Link to={`/companies/${e.company.id}`}>
            Company page
            <Kbd aria-hidden>Enter</Kbd>
          </Link>
        </Button>
      </div>
    </aside>
  );
}
