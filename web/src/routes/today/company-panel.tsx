import { useState, type FormEvent, type ReactNode } from 'react';
import { BookmarkCheck, Briefcase, Search, Terminal } from 'lucide-react';
import type { IsoTime, PlanRow } from '@/api';
import { StepMarks } from '@/components/step-marks';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { safeHref } from '@/lib/safe-href';
import { cn } from '@/lib/utils';
import { searchFieldId, peopleFieldId } from './plan-steps';
import { MAIN, CarriedOver, ClosedLine, KeyHint, LogButton, PanelHead, QuietActions, type PanelActions } from './plan-panel';

/** One line of the company's facts: a quiet label, then the API's words. */
function Fact({ label, children, muted = false }: { label: string; children: ReactNode; muted?: boolean }) {
  return (
    <div className="grid grid-cols-[132px_minmax(0,1fr)] items-baseline gap-x-4 py-1.5 text-[13px] max-[700px]:grid-cols-1">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={cn('min-w-0', muted && 'text-muted-foreground')}>{children}</dd>
    </div>
  );
}

/** "In flight" and "Asked before" in a calm grey, a reply in the info hue; never red. */
function Standing({ row }: { row: PlanRow }) {
  const s = row.queue?.standing;
  if (!s) return null;
  return <span className={cn(s.status === 'replied' && 'text-info')}>{s.text}</span>;
}

/**
 * A company on today's queue (company first, #9 2026-10-26): Knock picked it; he finds the people on LinkedIn and
 * writes his own notes. The panel shows what the row knows, each line as the API words it (size and stage, the role and
 * its age, recent hiring, sponsorship, why it ranks, where it stands, the last touch, the email hint, the people on
 * record), then the actions: Open LinkedIn search (S, the filled button), Careers page (O), Copy for terminal (C),
 * Log applied (A) when there is a role, the Log people box (L) for when the terminal cannot log them, Save this
 * search, and the quiet Carry (X) and Not a fit (N). The row is done once a request or an email at the company is
 * logged on or after the list date, from the terminal or here; a log from the terminal shows within seconds.
 */
export function CompanyPanel({
  row,
  asOf,
  actions,
  headingId,
}: {
  row: PlanRow;
  asOf: IsoTime;
  actions: Pick<PanelActions, 'onCarry' | 'onNotFit' | 'onLog' | 'onLogPeople' | 'onSaveSearch' | 'onCopyLine'>;
  headingId?: string;
}) {
  const [lines, setLines] = useState('');
  const [url, setUrl] = useState('');
  const q = row.queue;
  const search = safeHref(q?.peopleSearchUrl ?? row.findSomeone?.searchUrl);
  const careers = safeHref(q?.careersUrl);
  const roleHref = safeHref(row.role?.url);
  const todo = row.state === 'todo';
  const open = row.state !== 'skipped';
  const hasApply = row.steps.some((s) => s.kind === 'apply');
  const room = q ? q.standing.room : 4;
  const peopleId = peopleFieldId(headingId);
  const searchId = searchFieldId(headingId);
  const logPeople = (e: { preventDefault: () => void }) => {
    e.preventDefault();
    if (actions.onLogPeople(lines)) setLines('');
  };
  const saveSearch = (e: FormEvent) => {
    e.preventDefault();
    if (actions.onSaveSearch(url)) setUrl('');
  };

  return (
    <div className="grid max-w-[720px] gap-4">
      <PanelHead
        row={row}
        title={row.company.name}
        headingId={headingId}
        meta={
          <>
            <span>A company to reach. You find the people.</span>
            <CarriedOver row={row} asOf={asOf} />
          </>
        }
      />

      {q ? (
        <dl aria-label={`About ${row.company.name}`} className="grid border-y py-1">
          <Fact label="Size and stage" muted={!q.sizeLine}>
            {q.sizeLine ?? 'Not known yet'}
          </Fact>
          <Fact label="Role" muted={!q.roleLine}>
            {q.roleLine ? (
              roleHref ? (
                <a
                  href={roleHref}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rounded-sm underline decoration-foreground/30 underline-offset-3 outline-none hover:decoration-foreground focus-visible:ring-[3px] focus-visible:ring-ring/70"
                >
                  {q.roleLine}
                </a>
              ) : (
                q.roleLine
              )
            ) : (
              'No open role at your level'
            )}
          </Fact>
          <Fact label="Hiring now" muted={q.hiring === 0}>
            {q.hiringLine}
          </Fact>
          <Fact label="Sponsorship">{q.sponsorshipLine}</Fact>
          <Fact label="Why it ranks">
            <ul className="grid gap-0.5">
              {q.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </Fact>
          <Fact label="Where it stands">
            <Standing row={row} />
          </Fact>
          {q.lastTouch ? <Fact label="Last touch">{q.lastTouch}</Fact> : null}
          {q.emailHint ? <Fact label="Email">{q.emailHint}</Fact> : null}
          <Fact label="People" muted={q.peopleOnRecord === 0}>
            {q.peopleLine}
          </Fact>
        </dl>
      ) : (
        <p className="max-w-[62ch] text-base leading-6">{row.reason}</p>
      )}

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
          {careers ? (
            <Button asChild variant="outline" size="sm" className={MAIN}>
              <a href={careers} target="_blank" rel="noopener noreferrer" aria-keyshortcuts="o">
                <Briefcase aria-hidden />
                Careers page
                <KeyHint k="O" />
              </a>
            </Button>
          ) : null}
          {q ? (
            <Button type="button" variant="outline" size="sm" className={MAIN} aria-keyshortcuts="c" onClick={actions.onCopyLine}>
              <Terminal aria-hidden />
              Copy for terminal
              <KeyHint k="C" />
            </Button>
          ) : null}
          {hasApply ? <LogButton row={row} kind="apply" filled={false} onLog={actions.onLog} /> : null}
        </div>
      ) : null}

      {open && (todo || room > 0) ? (
        <form aria-label="Log people you asked" onSubmit={logPeople} className="grid gap-2 rounded-lg border bg-subtle px-4 py-3">
          <label htmlFor={peopleId} className="flex flex-wrap items-baseline gap-x-2 text-[13px] font-medium">
            Log people you asked
            <KeyHint k="L" />
            <span className="font-normal text-muted-foreground">For when your terminal cannot log them. One per line, up to 10.</span>
          </label>
          <Textarea
            id={peopleId}
            aria-keyshortcuts="l"
            value={lines}
            onChange={(e) => setLines(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) logPeople(e);
            }}
            placeholder={'Name | Title | the note you sent\nJane Doe | Senior Software Engineer | Hi Jane, ...'}
            maxLength={8000}
            rows={3}
            spellCheck={false}
            className="min-h-20 font-mono text-[13px] md:text-[13px]"
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" variant="outline" size="sm" className={MAIN} aria-keyshortcuts="mod+enter">
              Log people
            </Button>
            <span className="text-xs text-muted-foreground">
              {q && q.standing.room < 4 && q.standing.status === 'in_flight'
                ? `Room for ${q.standing.room} more in this wave.`
                : `The row is done once a request at ${row.company.name} is logged.`}
            </span>
          </div>
        </form>
      ) : null}

      {q && open ? (
        <form aria-label="Save this search" onSubmit={saveSearch} className="flex flex-wrap items-center gap-2">
          <label htmlFor={searchId} className="text-[13px] text-muted-foreground">
            {q.searchSaved ? (
              <span className="inline-flex items-center gap-1.5 text-foreground">
                <BookmarkCheck aria-hidden className="size-3.5" />
                Your saved search opens with S.
              </span>
            ) : (
              'Save this search'
            )}
          </label>
          <Input
            id={searchId}
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="Paste the LinkedIn people search address"
            maxLength={2100}
            autoComplete="off"
            spellCheck={false}
            className="h-8 w-80 max-w-full text-[13px] md:text-[13px]"
          />
          <Button type="submit" variant="ghost" size="sm" className="h-8 px-2.5 text-xs">
            {q.searchSaved ? 'Save a new one' : 'Save'}
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
