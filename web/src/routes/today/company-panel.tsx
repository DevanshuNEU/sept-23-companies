import { useState, type FormEvent, type ReactNode } from 'react';
import { BookmarkCheck, ChevronRight, Search, Terminal } from 'lucide-react';
import type { IsoTime, PassReason, PlanRow } from '@/api';
import { FitChip } from '@/components/fit-chip';
import { Monogram } from '@/components/monogram';
import { StepMarks } from '@/components/step-marks';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { safeHref } from '@/lib/safe-href';
import { laneLabel } from '@/lib/status';
import { cn } from '@/lib/utils';
import { canLogPeople, searchFieldId, peopleFieldId } from './plan-steps';
import { MAIN, CarriedOver, ClosedLine, KeyHint, LogButton, QuietActions, type PanelActions } from './plan-panel';
import { PassChips } from './pass-chips';
import { isProvisional } from './picks';
import { ResearchLinks, WhyToday } from './why-today';

/** One line of the company's standing: a quiet label, then the API's words. */
function Fact({ label, children, muted = false }: { label: string; children: ReactNode; muted?: boolean }) {
  return (
    <div className="grid grid-cols-[104px_minmax(0,1fr)] items-baseline gap-x-4 py-1 text-[13px] leading-6 max-[700px]:grid-cols-1">
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
 * A small row that opens a box below it: a chevron, what it is, a hint in grey and its key. Closed until he opens it
 * (the key for it opens it too), so the page leads with the work and keeps these for when the terminal cannot do it.
 */
function Disclosure({
  title,
  hint,
  keyHint,
  open,
  onOpenChange,
  children,
}: {
  title: ReactNode;
  hint?: ReactNode;
  keyHint?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
}) {
  return (
    <Collapsible open={open} onOpenChange={onOpenChange}>
      <CollapsibleTrigger
        aria-keyshortcuts={keyHint?.toLowerCase()}
        className="flex w-full items-center gap-2 rounded-md px-1 py-2.5 text-left text-[13px] outline-none transition-colors duration-150 hover:bg-muted/50 focus-visible:ring-[3px] focus-visible:ring-ring/70"
      >
        <ChevronRight aria-hidden className={cn('size-4 flex-none text-muted-foreground transition-transform duration-150', open && 'rotate-90')} />
        <span className="font-medium">{title}</span>
        {hint ? <span className="min-w-0 flex-1 truncate text-muted-foreground">{hint}</span> : <span className="flex-1" />}
        {keyHint ? <KeyHint k={keyHint} /> : null}
      </CollapsibleTrigger>
      <CollapsibleContent className="px-1 pt-1 pb-3">{children}</CollapsibleContent>
    </Collapsible>
  );
}

/**
 * A pick on today's list (company first, #9; top picks, 2026-10-26; the Today redesign): Knock picked it; he finds the
 * people on LinkedIn and writes his own notes. The panel leads with the company (its name large, what it builds, the
 * level and fit chips), then why today (the opening and its age, the reasons as chips, the judge's one line), then one
 * clear action, Open LinkedIn search (S, filled), with Copy for terminal (C), Log applied (A) and the research links
 * (Careers O, Website, LinkedIn page) quieter beside it. Where it stands follows. "Log people you asked" (L) and "Save
 * this search" start closed behind small rows; L opens the first and puts the cursor in it. Carry (X) and Not now (P or
 * N, the reason chips) stay at the bottom. The row is done once a request or an email at the company is logged on or
 * after the list date, from the terminal or here; a log from the terminal shows within seconds.
 */
export function CompanyPanel({
  row,
  asOf,
  actions,
  headingId,
  passing = false,
  peopleOpen,
  onPeopleOpenChange,
}: {
  row: PlanRow;
  asOf: IsoTime;
  actions: Pick<PanelActions, 'onCarry' | 'onNotNow' | 'onLog' | 'onLogPeople' | 'onSaveSearch' | 'onCopyLine'> & {
    onPass?: (reason: PassReason | null) => void;
    onCancelPass?: () => void;
  };
  headingId?: string;
  /** The Not now chips are open on this row. */
  passing?: boolean;
  /** The Log people box is open. It starts closed; the view's L key opens it and puts the cursor in it. */
  peopleOpen: boolean;
  onPeopleOpenChange: (open: boolean) => void;
}) {
  const [lines, setLines] = useState('');
  const [url, setUrl] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const q = row.queue;
  const search = safeHref(q?.peopleSearchUrl ?? row.findSomeone?.searchUrl);
  const todo = row.state === 'todo' && !isProvisional(row);
  const open = row.state !== 'skipped' && !isProvisional(row);
  const hasApply = row.steps.some((s) => s.kind === 'apply');
  const canLog = canLogPeople(row);
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
  const sub = q?.profile.builds.text ?? null;

  return (
    <div className="grid gap-5">
      <header className="grid gap-3">
        <div className="flex items-start gap-4">
          <Monogram name={row.company.name} className="mt-0.5 size-11 rounded-xl text-lg" />
          <div className="grid min-w-0 gap-1">
            <h2 id={headingId} tabIndex={-1} className="text-[28px] leading-9 font-semibold tracking-[-0.025em] outline-none">
              {row.company.name}
            </h2>
            <p className={cn('line-clamp-2 text-sm leading-[1.6]', sub === null && 'text-muted-foreground')}>
              {isProvisional(row) ? 'Coming in from the bench once your Not now is saved.' : (sub ?? 'What they build is not known yet.')}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[13px]">
          <Badge variant="idle">{q?.opening?.level ?? laneLabel[row.lane]}</Badge>
          <FitChip fit={q?.judgeFit} className="h-[22px]" />
          <CarriedOver row={row} asOf={asOf} />
        </div>
      </header>

      {q ? <WhyToday q={q} /> : <p className="max-w-[62ch] text-base leading-6">{row.reason}</p>}

      <div className="grid gap-2.5">
        {todo ? (
          <div className="flex flex-wrap items-center gap-2">
            {search ? (
              <Button asChild size="sm" className="h-9 gap-2 px-4 text-sm">
                <a href={search} target="_blank" rel="noopener noreferrer" aria-keyshortcuts="s">
                  <Search aria-hidden />
                  Open LinkedIn search
                  <KeyHint k="S" filled />
                </a>
              </Button>
            ) : null}
            {q ? (
              <Button type="button" variant="secondary" size="sm" className={MAIN} aria-keyshortcuts="c" onClick={actions.onCopyLine}>
                <Terminal aria-hidden />
                Copy for terminal
                <KeyHint k="C" />
              </Button>
            ) : null}
            {hasApply ? <LogButton row={row} kind="apply" filled={false} quiet onLog={actions.onLog} /> : null}
          </div>
        ) : null}
        {q ? <ResearchLinks q={q} variant="secondary" /> : null}
      </div>

      {passing && actions.onPass && actions.onCancelPass ? (
        <PassChips company={row.company.name} onPass={actions.onPass} onCancel={actions.onCancelPass} />
      ) : null}

      {q ? (
        <dl aria-label={`About ${row.company.name}`} className="grid border-t pt-3">
          <Fact label="Where it stands">
            <Standing row={row} />
          </Fact>
          {q.emailHint ? <Fact label="Email">{q.emailHint}</Fact> : null}
        </dl>
      ) : null}

      {canLog || (q && open) ? (
        <div className="grid divide-y border-y">
          {canLog ? (
            <Disclosure
              title="Log people you asked"
              hint="For when your terminal cannot log them"
              keyHint="L"
              open={peopleOpen}
              onOpenChange={onPeopleOpenChange}
            >
              <form aria-label="Log people you asked" onSubmit={logPeople} className="grid gap-2 rounded-lg border bg-subtle px-4 py-3">
                <label htmlFor={peopleId} className="text-[13px] text-muted-foreground">
                  One per line, up to 10. Name | Title | the note you sent.
                </label>
                <Textarea
                  id={peopleId}
                  aria-label="Log people you asked"
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
            </Disclosure>
          ) : null}
          {q && open ? (
            <Disclosure
              title="Save this search"
              hint={
                q.searchSaved ? (
                  <span className="inline-flex items-center gap-1.5">
                    <BookmarkCheck aria-hidden className="size-3.5" />
                    Your saved search opens with S
                  </span>
                ) : (
                  'Paste a LinkedIn people search so S opens it'
                )
              }
              open={searchOpen}
              onOpenChange={setSearchOpen}
            >
              <form aria-label="Save this search" onSubmit={saveSearch} className="flex flex-wrap items-center gap-2">
                <Input
                  id={searchId}
                  aria-label="Save this search"
                  type="url"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="Paste the LinkedIn people search address"
                  maxLength={2100}
                  autoComplete="off"
                  spellCheck={false}
                  className="h-8 w-80 max-w-full text-[13px] md:text-[13px]"
                />
                <Button type="submit" variant="outline" size="sm" className="h-8 px-2.5 text-xs">
                  {q.searchSaved ? 'Save a new one' : 'Save'}
                </Button>
              </form>
            </Disclosure>
          ) : null}
        </div>
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
