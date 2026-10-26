import { useState, type FormEvent, type ReactNode } from 'react';
import { Search } from 'lucide-react';
import type { IsoTime, PlanRow } from '@/api';
import { StepMarks } from '@/components/step-marks';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { safeHref } from '@/lib/safe-href';
import { askedFieldId } from './plan-steps';
import { MAIN, CarriedOver, ClosedLine, CompanyLink, KeyHint, LogButton, PanelHead, PanelReason, PanelRole, QuietActions, type PanelActions } from './plan-panel';

function Step({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <li className="grid gap-0.5 px-4 pb-3 text-[13px] first:pt-3.5">
      <span className="text-xs text-muted-foreground">{`${n}. ${title}`}</span>
      {children}
    </li>
  );
}

/**
 * A company row (company first, #9): a company to reach, with nobody named. He finds the people on LinkedIn and writes
 * his own notes, so the row shows the reason and the role, three steps (search, reach out, log it here), Open LinkedIn
 * search (S) as the filled button, a field for the name of the person he asked with Log request sent (R, then Enter),
 * Log applied (P) when the row has a role, the step marks and the quiet actions. No note: the app never writes one.
 */
export function FindSomeonePanel({
  row,
  asOf,
  actions,
  headingId,
}: {
  row: PlanRow;
  asOf: IsoTime;
  actions: Pick<PanelActions, 'onCarry' | 'onNotFit' | 'onLog' | 'onLogAsked'>;
  headingId?: string;
}) {
  const [asked, setAsked] = useState('');
  const search = safeHref(row.findSomeone?.searchUrl);
  const todo = row.state === 'todo';
  const hasApply = row.steps.some((s) => s.kind === 'apply');
  const requested = row.steps.some((s) => s.kind === 'request' && s.at !== null);
  const fieldId = askedFieldId(headingId);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    actions.onLogAsked(asked);
  };
  return (
    <div className="grid max-w-[720px] gap-4">
      <PanelHead
        row={row}
        title={`Find someone at ${row.company.name}`}
        headingId={headingId}
        meta={
          <>
            <span>
              <CompanyLink row={row} />
              {' is a company to reach. You find the people.'}
            </span>
            <CarriedOver row={row} asOf={asOf} />
          </>
        }
      />
      <PanelReason row={row} />
      <PanelRole row={row} />

      <ol aria-label="How to reach this company" className="grid rounded-lg border bg-subtle">
        <Step n={1} title="Search">
          <span>Open the LinkedIn search and pick the people you want to reach.</span>
        </Step>
        <Step n={2} title="Reach out">
          <span>Send your requests on LinkedIn, with your own note.</span>
        </Step>
        <Step n={3} title="Log it here">
          <span>Type the name of someone you asked and log the request. The row is done once a request at {row.company.name} is logged.</span>
        </Step>
      </ol>

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
          {hasApply ? <LogButton row={row} kind="apply" filled={false} onLog={actions.onLog} /> : null}
        </div>
      ) : null}

      {todo && !requested ? (
        <form aria-label="Log a request" onSubmit={submit} className="flex flex-wrap items-center gap-2">
          <label htmlFor={fieldId} className="text-[13px] text-muted-foreground">
            Who did you ask?
          </label>
          <Input
            id={fieldId}
            value={asked}
            onChange={(e) => setAsked(e.target.value)}
            placeholder="Their name"
            maxLength={200}
            autoComplete="off"
            className="h-8 w-56 text-[13px] md:text-[13px]"
          />
          <Button type="submit" variant="outline" size="sm" className={MAIN} aria-keyshortcuts="r">
            Log request sent
            <KeyHint k="R" />
          </Button>
        </form>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <StepMarks steps={row.steps} role={hasApply} />
        <span className="flex-1" />
        {todo ? <QuietActions actions={actions} /> : null}
      </div>
      <ClosedLine row={row} />
    </div>
  );
}
