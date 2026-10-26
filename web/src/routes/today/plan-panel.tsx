import type { ReactNode } from 'react';
import { Link } from 'react-router';
import { Check, ExternalLink, UserRound } from 'lucide-react';
import type { IsoTime, PlanRow, RoleKind, StepKind } from '@/api';
import { NoteBlock } from '@/components/note-block';
import { SourceAnchor, SourceLinks } from '@/components/source-links';
import { StepMarks } from '@/components/step-marks';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Kbd } from '@/components/ui/kbd';
import { formatCarriedDay, formatClockTime } from '@/lib/format';
import { safeHref } from '@/lib/safe-href';
import { laneLabel } from '@/lib/status';
import { cn } from '@/lib/utils';
import { NoteEditor, type NoteEdit } from './note-editor';
import { nextStep } from './plan-steps';

/** What the panel's buttons do. The view binds the same handlers to the keys, so a key and its button never differ. */
export type PanelActions = {
  onLog: (kind: StepKind) => void;
  /** A company row: the people he asked, pasted as `Name | Title | note` lines. True when the log was queued. */
  onLogPeople: (lines: string) => boolean;
  /** A company row: save his LinkedIn people search. True when the save was queued. */
  onSaveSearch: (url: string) => boolean;
  /** A company row: copy the one line for his terminal. */
  onCopyLine: () => void;
  onCarry: () => void;
  onNotFit: () => void;
  onCopy: () => void;
  /** Open the profile, then copy the note (Enter on a person). */
  onCopyOpen: () => void;
  onEdit: () => void;
  onConfirm: () => void;
  onEditChange: (edit: NoteEdit) => void;
  onSave: () => void;
  onCancelEdit: () => void;
};

/** The note editor's state for this row, and whether its newest save or Confirm line is still waiting. */
export type NoteState = { edit: NoteEdit | null; saving: boolean; unsaved: boolean; fieldId: string };

const KIND: Record<RoleKind, string> = {
  engineer: 'Engineer',
  manager: 'Manager',
  founder: 'Founder',
  recruiter: 'Recruiter',
  alum: 'Alum',
  other: 'Other',
};

const POSTED = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric' });
const companyPath = (row: PlanRow) => `/companies/${row.company.id}`;

/** The size of a row's main actions: the mockup's 32 px button with 13 px text. */
export const MAIN = 'gap-1.5 text-[13px]';

/** A key hint on a button. On the filled button it is the mockup's translucent chip in the button's own color. */
export function KeyHint({ k, filled = false }: { k: string; filled?: boolean }) {
  return (
    <Kbd aria-hidden className={cn(filled && 'bg-primary-foreground/15 text-primary-foreground')}>
      {k}
    </Kbd>
  );
}

/** The company's name as a link to its page. */
export function CompanyLink({ row, children }: { row: PlanRow; children?: ReactNode }) {
  return (
    <Link
      to={companyPath(row)}
      className="rounded-sm text-foreground underline decoration-foreground/30 underline-offset-3 transition-colors outline-none hover:decoration-foreground focus-visible:ring-[3px] focus-visible:ring-ring/70"
    >
      {children ?? row.company.name}
    </Link>
  );
}

/** The top of the panel: the name (the focus target after a save or the last step), what they are, the lane. */
export function PanelHead({
  row,
  title,
  meta,
  headingId,
}: {
  row: PlanRow;
  title: string;
  meta: ReactNode;
  headingId?: string;
}) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-4 gap-y-1">
      <div className="grid gap-1">
        <h2 id={headingId} tabIndex={-1} className="text-2xl leading-8 font-semibold tracking-[-0.015em] outline-none">
          {title}
        </h2>
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[13px] text-muted-foreground">{meta}</div>
      </div>
      <Badge variant="idle">{laneLabel[row.lane]}</Badge>
    </div>
  );
}

/** The one reason, the sentence he acts on, with where it comes from. */
export function PanelReason({ row }: { row: PlanRow }) {
  return (
    <div>
      <p className="max-w-[62ch] text-base leading-6">{row.reason}</p>
      <SourceLinks links={row.reasonSources} className="mt-1.5" />
    </div>
  );
}

/** "Carried over from Monday", grey, never red, while the row is still to do. */
export function CarriedOver({ row, asOf }: { row: PlanRow; asOf: IsoTime }) {
  if (row.carriedFrom === null || row.state !== 'todo') return null;
  return <span className="text-muted-foreground">{`Carried over from ${formatCarriedDay(row.carriedFrom, asOf)}`}</span>;
}

/** The role to name, with Open role (O) and the apply page. Nothing when the row has no role. */
export function PanelRole({ row }: { row: PlanRow }) {
  const role = row.role;
  if (!role) return null;
  const roleHref = safeHref(role.url);
  const applyHref = safeHref(row.applyUrl);
  const details = [role.location, `Posted ${POSTED.format(new Date(role.firstSeenAt))}`, role.yearsAsked ? `Asks ${role.yearsAsked} years` : null]
    .filter(Boolean)
    .map((p) => `${p}.`)
    .join(' ');
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-y py-2.5 text-[13px]">
      <span className="text-muted-foreground">Role</span>
      <span className="min-w-0 flex-1">
        <b className="font-medium">{role.title}</b> <span className="text-muted-foreground">{details}</span>
      </span>
      {roleHref ? (
        <Button asChild variant="outline" size="sm" className="h-7 gap-1.5 px-2.5 text-xs has-[>svg]:px-2">
          <a href={roleHref} target="_blank" rel="noopener noreferrer" aria-keyshortcuts="o">
            <ExternalLink aria-hidden />
            Open role
            <KeyHint k="O" />
          </a>
        </Button>
      ) : null}
      {applyHref ? (
        <Button asChild variant="outline" size="sm" className="h-7 gap-1.5 px-2.5 text-xs has-[>svg]:px-2">
          <a href={applyHref} target="_blank" rel="noopener noreferrer">
            <ExternalLink aria-hidden />
            Apply page
          </a>
        </Button>
      ) : null}
    </div>
  );
}

/** Carry to tomorrow (X) and Not a fit (N): quiet, at the end of the row's actions. */
export function QuietActions({ actions }: { actions: Pick<PanelActions, 'onCarry' | 'onNotFit'> }) {
  return (
    <>
      <Button type="button" variant="ghost" size="sm" className="h-7 gap-1.5 px-2.5 text-xs" aria-keyshortcuts="x" onClick={actions.onCarry}>
        Carry to tomorrow
        <KeyHint k="X" />
      </Button>
      <Button type="button" variant="ghost" size="sm" className="h-7 gap-1.5 px-2.5 text-xs" aria-keyshortcuts="n" onClick={actions.onNotFit}>
        Not a fit
        <KeyHint k="N" />
      </Button>
    </>
  );
}

/** Why a skipped row left the day: it is carried to tomorrow (a company removed as not a fit is off the plan). */
export function SkippedLine() {
  return <p className="text-[13px] text-muted-foreground">Carried to tomorrow. It is first on tomorrow&apos;s plan.</p>;
}

/** The calm last line of a row that is no longer to do. */
export function ClosedLine({ row }: { row: PlanRow }) {
  if (row.state === 'skipped') return <SkippedLine />;
  if (row.state !== 'done') return null;
  const times = row.steps.flatMap((s) => (s.at ? [s.at] : [])).sort();
  const last = times.at(-1);
  return (
    <p className="text-[13px] text-muted-foreground">
      {last ? `Done at ${formatClockTime(last)}. ` : 'Done. '}
      To change a log, open <CompanyLink row={row}>{`${row.company.name}'s page`}</CompanyLink>.
    </p>
  );
}

const stepAt = (row: PlanRow, kind: StepKind) => row.steps.find((s) => s.kind === kind)?.at ?? null;

/**
 * One log button. The row's next step is the only filled button, unless his line still waits for him: then
 * Confirm line in the note is the filled one (ruling 13). A logged step stays, quiet, with its time.
 */
export function LogButton({ row, kind, filled, onLog }: { row: PlanRow; kind: StepKind; filled: boolean; onLog: (kind: StepKind) => void }) {
  const at = stepAt(row, kind);
  const key = kind === 'request' ? 'R' : 'A';
  const label = at ? (kind === 'request' ? `Request logged ${formatClockTime(at)}` : `Applied ${formatClockTime(at)}`) : kind === 'request' ? 'Log request sent' : 'Log applied';
  return (
    <Button
      type="button"
      variant={at ? 'ghost' : filled ? 'default' : 'outline'}
      aria-keyshortcuts={key.toLowerCase()}
      onClick={() => onLog(kind)}
      size="sm"
      className={MAIN}
    >
      {at ? <Check aria-hidden /> : null}
      {label}
      <KeyHint k={key} filled={!at && filled} />
    </Button>
  );
}

/**
 * The work panel for a person on LinkedIn (design 16.1): who they are and where they were found, the one reason
 * and its sources, the role, the note on paper, then one filled button for the next step, the step marks and the
 * quiet actions. A done or carried row keeps its marks and says so in one calm line.
 */
export function PersonPanel({
  row,
  asOf,
  actions,
  note,
  headingId,
}: {
  row: PlanRow;
  asOf: IsoTime;
  actions: PanelActions;
  note: NoteState;
  headingId?: string;
}) {
  const person = row.person;
  if (!person) return null;
  const step = nextStep(row);
  const confirmFirst = row.state === 'todo' && row.note?.needsYourLine === true;
  const profile = safeHref(person.person.linkedinUrl);
  const hasApply = row.steps.some((s) => s.kind === 'apply');
  const todo = row.state === 'todo';

  return (
    <div className="grid max-w-[720px] gap-4">
      <PanelHead
        row={row}
        title={person.person.name}
        headingId={headingId}
        meta={
          <>
            <span>
              {`${person.person.title} at `}
              <CompanyLink row={row} />
            </span>
            <Badge variant="outline">{KIND[person.person.roleKind]}</Badge>
            {person.foundOn ? (
              <SourceAnchor link={{ label: `Found on ${person.foundOn.label}`, url: person.foundOn.url }} />
            ) : null}
            <CarriedOver row={row} asOf={asOf} />
          </>
        }
      />
      <PanelReason row={row} />
      <PanelRole row={row} />

      {row.note ? (
        <NoteBlock
          key={row.note.noteId}
          note={row.note}
          saving={note.saving}
          unsaved={note.unsaved}
          onCopy={actions.onCopy}
          onEdit={actions.onEdit}
          onConfirm={actions.onConfirm}
          editing={
            note.edit ? (
              <NoteEditor
                note={row.note}
                edit={note.edit}
                name={person.person.name}
                fieldId={note.fieldId}
                onChange={actions.onEditChange}
                onSave={actions.onSave}
                onCancel={actions.onCancelEdit}
              />
            ) : undefined
          }
        />
      ) : null}

      {todo ? (
        <div className="flex flex-wrap items-center gap-2">
          {profile ? (
            <Button asChild variant="outline" size="sm" className={MAIN}>
              <a href={profile} target="_blank" rel="noopener noreferrer" aria-keyshortcuts="l">
                <UserRound aria-hidden />
                Open profile
                <KeyHint k="L" />
              </a>
            </Button>
          ) : null}
          {profile && row.note ? (
            <Button type="button" variant="outline" size="sm" className={MAIN} aria-keyshortcuts="enter" onClick={actions.onCopyOpen}>
              <ExternalLink aria-hidden />
              Copy and open
              <KeyHint k="↵" />
            </Button>
          ) : null}
          <LogButton row={row} kind="request" filled={!confirmFirst && step === 'request'} onLog={actions.onLog} />
          {hasApply ? <LogButton row={row} kind="apply" filled={!confirmFirst && step === 'apply'} onLog={actions.onLog} /> : null}
        </div>
      ) : null}

      <div className={cn('flex flex-wrap items-center gap-2', todo && '-mt-1')}>
        <StepMarks steps={row.steps} role={hasApply} />
        <span className="flex-1" />
        {todo ? <QuietActions actions={actions} /> : null}
      </div>
      <ClosedLine row={row} />
    </div>
  );
}
