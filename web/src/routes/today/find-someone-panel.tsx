import type { ReactNode } from 'react';
import { Search, Terminal } from 'lucide-react';
import type { IsoTime, PlanRow } from '@/api';
import { Button } from '@/components/ui/button';
import { safeHref } from '@/lib/safe-href';
import { MAIN, CarriedOver, ClosedLine, CompanyLink, KeyHint, PanelHead, PanelReason, PanelRole, QuietActions, type PanelActions } from './plan-panel';

function Step({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <li className="grid gap-0.5 px-4 pb-3 text-[13px] first:pt-3.5">
      <span className="text-xs text-muted-foreground">{`${n}. ${title}`}</span>
      {children}
    </li>
  );
}

/**
 * A row with nobody on it yet (design 16.1, "Find someone"): the reason and the role, then three steps on paper
 * (search, hand it to Claude, back here) with the handoff text shown, Open LinkedIn search (S) as the one filled
 * button and Copy handoff (C). No note and no logging key: there is nobody to log yet.
 */
export function FindSomeonePanel({
  row,
  asOf,
  actions,
  headingId,
}: {
  row: PlanRow;
  asOf: IsoTime;
  actions: Pick<PanelActions, 'onCopy' | 'onCarry' | 'onNotFit'>;
  headingId?: string;
}) {
  const find = row.findSomeone;
  const search = safeHref(find?.searchUrl);
  const todo = row.state === 'todo';
  return (
    <div className="grid max-w-[720px] gap-4">
      <PanelHead
        row={row}
        title={`Find someone at ${row.company.name}`}
        headingId={headingId}
        meta={
          <>
            <span>
              {'Nobody at '}
              <CompanyLink row={row} />
              {' is on your list yet'}
            </span>
            <CarriedOver row={row} asOf={asOf} />
          </>
        }
      />
      <PanelReason row={row} />
      <PanelRole row={row} />

      {find ? (
        <ol aria-label="How to find someone" className="grid rounded-lg border bg-subtle">
          <Step n={1} title="Search">
            <span>Open the LinkedIn search and pick one engineer on the team.</span>
          </Step>
          <Step n={2} title="Hand it to Claude">
            <span>Copy the handoff and paste it in the side terminal with their profile.</span>
            <pre className="mt-1.5 rounded-md bg-muted px-3 py-2.5 font-mono text-xs leading-[18px] whitespace-pre-wrap">{find.handoff}</pre>
          </Step>
          <Step n={3} title="Back here">
            <span>Claude adds them and writes the note. This row becomes theirs.</span>
          </Step>
        </ol>
      ) : null}

      {todo ? (
        <div className="flex flex-wrap items-center gap-2">
          {search ? (
            <Button asChild size="sm" className={MAIN}>
              <a href={search} target="_blank" rel="noopener noreferrer" aria-keyshortcuts="s">
                <Search aria-hidden />
                Open LinkedIn search
                <KeyHint k="S" filled />
              </a>
            </Button>
          ) : null}
          {find ? (
            <Button type="button" variant="outline" size="sm" className={MAIN} aria-keyshortcuts="c" onClick={actions.onCopy}>
              <Terminal aria-hidden />
              Copy handoff
              <KeyHint k="C" />
            </Button>
          ) : null}
          <span className="flex-1" />
          <QuietActions actions={actions} />
        </div>
      ) : null}
      <ClosedLine row={row} />
    </div>
  );
}
