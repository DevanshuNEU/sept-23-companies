import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { toast } from 'sonner';
import { HelpCircle, Layers, Terminal } from 'lucide-react';
import {
  pollMs,
  queryKeys,
  usePageClient,
  type BenchView,
  type Id,
  type PassReason,
  type PlanRow,
  type PlanView,
  type StepKind,
} from '@/api';
import { DayRail } from '@/components/day-rail';
import { QueryState } from '@/components/state-views';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { isMac, useHotkeys, type Binding } from '@/keyboard/hotkeys';
import { useListNav, type RowProps } from '@/keyboard/list-nav';
import { formatLongDate } from '@/lib/format';
import { cn } from '@/lib/utils';
import { openExternal, safeHref } from '@/lib/safe-href';
import { useViewQuery } from '@/pending/pending';
import { BenchDrawer } from './bench-drawer';
import { CompanyPanel } from './company-panel';
import { EmailRowPanel } from './email-row-panel';
import { FinishPanel } from './finish-panel';
import { InFlightList } from './in-flight-list';
import type { NoteEdit } from './note-editor';
import { KeyHint, PersonPanel, type PanelActions } from './plan-panel';
import { PlanEmpty, PlanJobBanner, PlanNotReady, PlanSkeleton } from './plan-states';
import { OutcomePrompt } from './outcome-prompt';
import { PassChips } from './pass-chips';
import { isCompanyRow, nextTodoAfter, peopleFieldId } from './plan-steps';
import { usePlanActions } from './use-plan-actions';
import { WatchList } from './watch-list';
import { WinStrip } from './win-strip';

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

async function copy(text: string, done: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast(done);
  } catch {
    toast.error('Copy failed. Nothing is on the clipboard. Try again, or select the text and copy it.');
  }
}

/** The row's name as the rail and the live region say it. */
function nameOf(row: PlanRow): string {
  if (row.channel === 'email') return `${row.company.name} by email`;
  if (isCompanyRow(row)) return row.company.name;
  return `${row.person?.person.name ?? 'Find someone'} at ${row.company.name}`;
}

/** A queue of companies (company first, #9): every row is a company row, so the page counts companies. */
const isQueue = (plan: PlanView) => plan.rows.length > 0 && plan.rows.every(isCompanyRow);

/** Whether logging this step is the one that finishes the row, so the selection moves on. */
const finishes = (row: PlanRow, kind: StepKind) =>
  row.state === 'todo' && row.steps.some((s) => s.kind === kind && s.at === null) && row.steps.every((s) => s.kind === kind || s.at !== null);

/** The rows as they are once this one is no longer to do, for `nextTodoAfter`. */
const without = (rows: PlanRow[], entryId: Id): PlanRow[] => rows.map((r) => (r.entryId === entryId ? { ...r, state: 'done' } : r));

/**
 * Below the plan, quiet (design 16.1): the 7-day question, then the watch list. Each part draws nothing when it has nothing to say, so the
 * rule above them only shows with something under it.
 */
function BelowPlan({ className, plan }: { className?: string; plan?: PlanView }) {
  return (
    <div className={cn('mt-8 grid max-w-[720px] gap-6 empty:hidden', className)}>
      {plan ? <InFlightList items={plan.inFlight} /> : null}
      <OutcomePrompt />
      <WatchList />
    </div>
  );
}

function PlanHead({ plan }: { plan: PlanView }) {
  const { used, limit, hardUsed, hardLimit } = plan.linkedinWeek;
  // Once nothing is left to do, the head says what carried over, as the finish line does (design 16.2).
  const finished = plan.rows.length > 0 && plan.rows.every((r) => r.state !== 'todo');
  const carried = finished ? plan.rows.filter((r) => r.state === 'skipped').length : 0;
  return (
    <div className="flex flex-none items-end gap-4 border-b px-6 pt-5 pb-4">
      <div>
        <h2 className="text-xl leading-7 font-semibold tracking-[-0.01em]">{formatLongDate(plan.date)}</h2>
        {plan.notReady ? null : (
          <p className="mt-0.5 text-[13px] text-muted-foreground tabular-nums">
            {isQueue(plan)
              ? `${plural(plan.total, 'pick', 'picks')}, ${plan.done} reached${carried > 0 ? `, ${carried} carried over` : ''}${plan.passed > 0 ? `, ${plan.passed} passed` : ''}`
              : `${plural(plan.total, 'person', 'people')}, ${plan.done} done${carried > 0 ? `, ${carried} carried over` : ''}`}
          </p>
        )}
      </div>
      <div className="ml-auto flex items-center gap-2 text-[13px]">
        <p data-quota="week" className="flex items-baseline gap-1.5 tabular-nums">
          <span className="text-muted-foreground">Requests this week</span> <b className="font-semibold">{used}</b>{' '}
          <span className="text-muted-foreground">{`of ${limit}`}</span>
        </p>
        <Tooltip>
          <TooltipTrigger asChild>
            <button type="button" aria-label="About this limit" className="rounded-sm text-muted-foreground outline-none focus-visible:ring-[3px] focus-visible:ring-ring/70">
              <HelpCircle aria-hidden className="size-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent className="max-w-64">
            {`The plan uses ${limit} of LinkedIn's ${hardLimit} a week. ${hardUsed} of ${hardLimit} are used in the last 7 days, counting requests sent outside the plan.`}
          </TooltipContent>
        </Tooltip>
      </div>
    </div>
  );
}

/**
 * The plan with people on it: the day rail on the left, the selected person's work on the right (design 16.1).
 *
 * The first row to do is selected on arrival and pinned: it is read once, when the plan first shows, so a row
 * that finishes or a poll that reorders never moves the selection. Once he moves, the selection lives in the URL.
 * Every key acts on the row he moved to last (`useHotkeys` with the selection), and follows the same rules as its
 * button.
 */
function PlanWork({ plan, bench }: { plan: PlanView; bench: BenchView | undefined }) {
  const navigate = useNavigate();
  const actions = usePlanActions();
  const [pinned] = useState(() => plan.rows.find((r) => r.state === 'todo')?.entryId);
  const [edits, setEdits] = useState<ReadonlyMap<Id, NoteEdit>>(new Map());
  // Not now's reason chips are open on this row; the bench drawer (B) is open.
  const [passing, setPassing] = useState<Id | null>(null);
  const [benchOpen, setBenchOpen] = useState(false);
  const showBench = useCallback(() => setBenchOpen(true), []);
  useNewOpeningToast(bench, showBench);
  // The panel's heading takes focus after a save or a confirm, so focus never drops to the page.
  const headingId = useId();
  const noteFieldId = `${headingId}-note`;

  const rows = plan.rows;
  const ids = rows.map((r) => r.entryId);
  const byId = new Map(rows.map((r) => [r.entryId, r]));
  const { selected, select, rowProps } = useListNav<Id>({
    ids,
    initial: pinned,
    onOpen: (id) => {
      const row = byId.get(id);
      if (row) enter(row);
    },
    announce: (id) => {
      const row = byId.get(id);
      return row ? nameOf(row) : '';
    },
  });
  const allDone = rows.every((r) => r.state !== 'todo');

  // The finish line takes the work panel the moment no row is to do (design 16.2) and stays until he picks a
  // person on the rail. `id` is the row he was on, so a click on it still counts as picking. Arriving on a plan
  // that is already finished with a person in the URL shows that person, as the link asked.
  const [arrivedDone] = useState(allDone);
  const [fin, setFin] = useState<{ id: Id | null; browsing: boolean } | null>(null);
  if (!allDone && fin !== null) setFin(null);
  else if (allDone && fin === null) setFin({ id: selected, browsing: arrivedDone && selected !== null });
  else if (fin !== null && !fin.browsing && selected !== fin.id) setFin({ ...fin, browsing: true });
  const showFinish = allDone && fin !== null && !fin.browsing;
  const current = showFinish || selected === null ? null : (byId.get(selected) ?? null);

  const focusPanel = () => requestAnimationFrame(() => document.getElementById(headingId)?.focus());
  const moveOn = (row: PlanRow) => {
    const next = nextTodoAfter(without(rows, row.entryId), row.entryId);
    if (next !== null) select(next);
  };
  const setEdit = (noteId: Id, edit: NoteEdit | null) =>
    setEdits((m) => {
      const next = new Map(m);
      if (edit) next.set(noteId, edit);
      else next.delete(noteId);
      return next;
    });

  /* ------------------------------------------------------------------ what each key and button does */

  /** A company row's Log people box: L (and R) put the cursor there; Cmd+Enter or the button logs the paste. */
  const focusPeople = (row: PlanRow) => {
    const field = document.getElementById(peopleFieldId(headingId));
    if (field) return void requestAnimationFrame(() => field.focus());
    toast(row.state === 'skipped' ? `${row.company.name} is carried to tomorrow.` : `Nothing to log at ${row.company.name}: its wave is full.`);
  };
  const log = (row: PlanRow, kind: StepKind) => {
    if (kind === 'request' && isCompanyRow(row)) return focusPeople(row);
    const done = finishes(row, kind);
    const queued = kind === 'request' ? actions.logRequest(row) : actions.logApplied(row);
    if (queued && done) moveOn(row);
  };
  const logPeople = (row: PlanRow, lines: string): boolean => {
    const done = finishes(row, 'request');
    const queued = actions.logPeople(row, lines);
    if (queued && done) moveOn(row);
    return queued;
  };
  const carry = (row: PlanRow) => {
    if (actions.skip(row)) moveOn(row);
  };
  /**
   * Not now (P, or N; top picks, his ruling of 2026-10-26): never a remove and never a no-go. It opens the reason chips
   * when the row may pass; else it says why, once. A pick passes with the chip he presses (or none), and the bench's
   * best company slides into its place.
   */
  const askNotNow = (row: PlanRow) => {
    const blocked = actions.passRefusal(row);
    if (blocked) return void toast(blocked);
    setPassing((p) => (p === row.entryId ? null : row.entryId));
  };
  const pass = (row: PlanRow, reason: PassReason | null) => {
    setPassing(null);
    if (actions.passPick(row, reason)) moveOn(row);
  };
  /** An open edit that differs from the saved note: Copy would copy the saved text, so it waits for Save. */
  const unsavedEdit = (row: PlanRow): boolean => {
    const draft = row.note ? edits.get(row.note.noteId) : undefined;
    return draft !== undefined && row.note !== null && (draft.text !== row.note.text || draft.line !== row.note.reasonLine);
  };
  /** Why this note cannot be copied right now, in the words the toast says, or null. C, the Copy button and Enter all obey it. */
  const copyRefusal = (row: PlanRow, note: NonNullable<PlanRow['note']>): string | null => {
    if (actions.noteSaving(note.noteId)) return 'Not copied. The note is saving, and the checks run again first.';
    if (unsavedEdit(row)) return `Not copied. Save your edit first (${isMac() ? 'Cmd+S' : 'Ctrl+S'}), then copy.`;
    if (note.checks.length === 0) return 'Not copied. The checks have not run yet. Try again in a moment.';
    const blocks = note.checks.filter((c) => c.severity === 'block').length;
    if (blocks > 0) return `Not copied. ${plural(blocks, 'check blocks', 'checks block')} this note. Fix it, then copy.`;
    return null;
  };
  /** What the toast says after a copy: plain, or a warning when a fact in the note has no safe link. */
  const copiedWords = (note: NonNullable<PlanRow['note']>): string => {
    const unlinked = note.facts.filter((f) => f.inNote && !safeHref(f.source.url)).length;
    return unlinked === 0
      ? 'Copied the note.'
      : unlinked === 1
        ? 'Copied. One fact has no link: check it before you send.'
        : `Copied. ${unlinked} facts have no link: check them before you send.`;
  };
  /** A company row: the one line his terminal skills read (`Knock: <Company> (company <id>), role: <title>`). */
  const copyLine = (row: PlanRow) => {
    if (row.queue) return void copy(row.queue.terminalLine, 'Copied the line for your terminal.');
    if (row.findSomeone) return void copy(row.findSomeone.handoff, 'Copied the handoff.');
  };
  const copyNote = (row: PlanRow) => {
    if (row.queue || row.findSomeone) return copyLine(row);
    const note = row.note;
    if (!note) return void toast(row.channel === 'email' ? 'No note on this row. Review the email in the Outbox.' : 'No note on this row yet.');
    const refusal = copyRefusal(row, note);
    if (refusal) return void toast(refusal);
    void copy(note.text, copiedWords(note));
  };
  /**
   * Enter on a person to do: open the profile, then copy the note, ready to paste. The window opens first and inside
   * the key press (or the click), so a popup blocker allows it. A note C would refuse is not copied, and then the
   * profile is not opened either, so nothing stale is left on the clipboard to paste. A row with no note still opens the
   * profile. Logging the request stays his own R.
   */
  const copyAndOpen = (row: PlanRow) => {
    const profile = row.person?.person.linkedinUrl;
    const note = row.note;
    if (!note) {
      if (openExternal(profile)) toast('Opened the profile. No note on this row yet.');
      return;
    }
    const refusal = copyRefusal(row, note);
    if (refusal) return void toast(refusal);
    if (!openExternal(profile)) return void copy(note.text, `No profile link for ${row.person?.person.name ?? 'this person'}. ${copiedWords(note)}`);
    void copy(note.text, `Opened the profile. ${copiedWords(note)}`);
  };
  /** Enter: copy and open for a LinkedIn person to do who has a profile link; for any other row, its company page. */
  function enter(row: PlanRow) {
    const toDo = row.state === 'todo' && row.channel === 'linkedin_request' && row.person !== null;
    if (toDo && safeHref(row.person?.person.linkedinUrl)) copyAndOpen(row);
    else navigate(`/companies/${row.company.id}`);
  }
  const copyAllHandoffs = () => {
    const queueLines = rows.flatMap((r) => (r.state === 'todo' && r.queue ? [r.queue.terminalLine] : []));
    if (queueLines.length > 0) return void copy(queueLines.join('\n'), `Copied ${plural(queueLines.length, 'line', 'lines')} for your terminal.`);
    const handoffs = rows.flatMap((r) => (r.state === 'todo' && r.findSomeone ? [r.findSomeone.handoff] : []));
    if (handoffs.length === 0) return void toast('Nothing copied. No company on today\'s list is still to do.');
    void copy(handoffs.join('\n\n'), `Copied ${plural(handoffs.length, 'handoff', 'handoffs')}.`);
  };
  const edit = (row: PlanRow) => {
    const note = row.note;
    if (!note) return void toast(isCompanyRow(row) ? 'No note on a company row. You write your own notes.' : 'No note on this row.');
    if (!edits.has(note.noteId)) setEdit(note.noteId, { text: note.text, line: note.reasonLine });
    // Focus goes into the editor only when he starts an edit, never when he comes back to a row with a kept one.
    requestAnimationFrame(() => document.getElementById(noteFieldId)?.focus());
  };
  const save = (row: PlanRow) => {
    const note = row.note;
    const draft = note ? edits.get(note.noteId) : undefined;
    if (!note || !draft) return;
    setEdit(note.noteId, null);
    if (draft.text !== note.text || draft.line !== note.reasonLine) actions.saveNote(row, draft.text, draft.line);
    focusPanel();
  };
  const cancelEdit = (row: PlanRow) => {
    if (row.note) setEdit(row.note.noteId, null);
    focusPanel();
  };
  const confirm = (row: PlanRow) => {
    const note = row.note;
    if (!note) return void toast('No line to confirm on this row.');
    if (actions.noteSaving(note.noteId)) return void toast('Not confirmed. The note is saving. Press Y again once it is saved.');
    if (actions.confirming(note.noteId)) return;
    if (note.reasonState !== 'proposed') return void toast('The line is already yours.');
    // A note he saved is his reason (#9, 2026-10-26): Claude's proposed line under it is not his to confirm.
    if (!note.needsYourLine) return void toast('Nothing to confirm. You wrote this note, so it needs no line.');
    actions.confirmReason(row);
    focusPanel();
  };
  const open = (url: string | null | undefined, none: string) => {
    if (!openExternal(url)) toast(none);
  };
  const panelActions = (row: PlanRow): PanelActions => ({
    onLog: (kind) => log(row, kind),
    onLogPeople: (lines) => logPeople(row, lines),
    onSaveSearch: (url) => actions.saveSearch(row, url),
    onCopyLine: () => copyLine(row),
    onCarry: () => carry(row),
    onNotNow: () => askNotNow(row),
    onCopy: () => copyNote(row),
    onCopyOpen: () => copyAndOpen(row),
    onEdit: () => edit(row),
    onConfirm: () => confirm(row),
    onEditChange: (e) => {
      if (row.note) setEdit(row.note.noteId, e);
    },
    onSave: () => save(row),
    onCancelEdit: () => cancelEdit(row),
  });

  /* ------------------------------------------------------------------ keys */

  const group = 'Today';
  const on = (run: (row: PlanRow) => void) => () => {
    if (current) run(current);
  };
  const has = current !== null;
  const editing = current?.note ? edits.has(current.note.noteId) : false;
  const company = current !== null && isCompanyRow(current);
  const person = current?.person ?? null;
  const onRow: Binding[] = [
    // L: the profile on a person row; the Log people box on a company row (company first, #9).
    {
      keys: 'l', label: company ? 'Log people you asked' : 'Open profile', group, enabled: has,
      run: on((r) => (isCompanyRow(r) ? focusPeople(r) : open(r.person?.person.linkedinUrl, r.person ? `No profile link for ${r.person.person.name}.` : 'Nobody on this row.'))),
    },
    {
      keys: 'o', label: company ? 'Open the careers page' : 'Open role', group, enabled: has,
      run: on((r) => (r.queue ? open(r.queue.careersUrl, `No careers page known for ${r.company.name}.`) : open(r.role?.url, 'No role on this row.'))),
    },
    { keys: 's', label: 'Open LinkedIn search', group, enabled: has, run: on((r) => open(r.queue?.peopleSearchUrl ?? r.findSomeone?.searchUrl, 'This row already has someone. Press L to open their profile.')) },
    { keys: 'c', label: company ? 'Copy the line for your terminal' : 'Copy the note', group, enabled: has, run: on(copyNote) },
    { keys: 'a', label: 'Log applied', group, enabled: has, run: on((r) => log(r, 'apply')) },
    // The note keys and R act on a person row only; a company row has no note, and its people are logged with L.
    { keys: 'e', label: 'Edit the note', group, enabled: has && person !== null, run: on(edit) },
    { keys: 'y', label: 'Confirm your line', group, enabled: has && person !== null, run: on(confirm) },
    { keys: 'r', label: company ? 'Log people you asked' : 'Log request sent', group, enabled: has, run: on((r) => log(r, 'request')) },
    { keys: 'x', label: 'Carry to tomorrow', group, enabled: has, run: on(carry) },
    // Only a row to do has a Not now button, so only a row to do answers p and n (one action since 2026-10-26).
    { keys: 'p', label: 'Not now', group, enabled: current?.state === 'todo' && passing === null, run: on(askNotNow) },
    { keys: 'n', label: 'Not now', group, enabled: current?.state === 'todo' && passing === null, run: on(askNotNow) },
    { keys: 'mod+s', label: 'Save the note', group, enabled: editing, run: on(save) },
  ];
  // These act on the selected person, so a key right after j, k or a click waits for that move to render.
  useHotkeys(onRow, { selection: selected });
  useHotkeys([
    { keys: 'shift+c', label: isQueue(plan) ? 'Copy every company line for the terminal' : 'Copy every find-someone handoff', group, run: copyAllHandoffs },
    { keys: 'b', label: 'More options (the bench)', group, inDialogs: true, run: () => setBenchOpen((o) => !o) },
  ]);

  /* ------------------------------------------------------------------ layout */

  let panel = (
    <p className="max-w-[62ch] text-sm text-muted-foreground">
      {allDone
        ? `Nothing is left to do on the plan. Pick ${isQueue(plan) ? 'a company' : 'anyone'} on the left to see what you did.`
        : `Pick ${isQueue(plan) ? 'a company' : 'a person'} on the left to see the work.`}
    </p>
  );
  if (current) {
    const a = panelActions(current);
    const note = current.note;
    const state = {
      edit: note ? (edits.get(note.noteId) ?? null) : null,
      saving: note ? actions.noteSaving(note.noteId) : false,
      unsaved: unsavedEdit(current),
      fieldId: noteFieldId,
    };
    panel =
      current.channel === 'email' ? (
        <EmailRowPanel key={current.entryId} row={current} asOf={plan.asOf} actions={a} headingId={headingId} />
      ) : current.person === null ? (
        <CompanyPanel
          key={current.entryId}
          row={current}
          asOf={plan.asOf}
          actions={{ ...a, onPass: (reason) => pass(current, reason), onCancelPass: () => setPassing(null) }}
          headingId={headingId}
          passing={passing === current.entryId}
        />
      ) : (
        <PersonPanel key={current.entryId} row={current} asOf={plan.asOf} actions={a} note={state} headingId={headingId} />
      );
  }

  // With the finish line showing, no row is selected: the finish node is the one marked, as in the mockup.
  const railProps = (id: Id): RowProps => {
    const props = rowProps(id);
    return {
      ...props,
      'aria-selected': showFinish ? false : props['aria-selected'],
      'data-active': showFinish ? false : props['data-active'],
      onClick: () => {
        setFin((f) => (f === null ? f : { ...f, browsing: true }));
        props.onClick();
      },
    };
  };

  return (
    <div className="grid min-h-0 flex-1 grid-cols-[320px_minmax(0,1fr)] max-[1100px]:grid-cols-1 max-[1100px]:overflow-y-auto">
      <div className="min-h-0 overflow-y-auto border-r px-3 pt-4 pb-6 max-[1100px]:overflow-visible max-[1100px]:border-r-0 max-[1100px]:border-b">
        <DayRail rows={rows} rowProps={railProps} tomorrow={plan.tomorrow} allDone={allDone} asOf={plan.asOf} />
        <div className="mx-3 mt-6 grid justify-items-start gap-2">
          <Button type="button" variant="ghost" size="sm" className="h-7 gap-1.5 px-2.5 text-xs" aria-keyshortcuts="shift+c" onClick={copyAllHandoffs}>
            <Terminal aria-hidden />
            {isQueue(plan) ? 'Copy every company line' : 'Copy every find-someone handoff'}
            <span aria-hidden className="inline-flex gap-0.5">
              <KeyHint k="⇧" />
              <KeyHint k="C" />
            </span>
          </Button>
          <Button type="button" variant="ghost" size="sm" className="h-7 gap-1.5 px-2.5 text-xs" aria-keyshortcuts="b" onClick={() => setBenchOpen(true)}>
            <Layers aria-hidden />
            {`More options${bench && bench.companies.length > 0 ? ` (${bench.companies.length})` : ''}`}
            <KeyHint k="B" />
          </Button>
          <Link
            to="/proposals"
            className="ml-2.5 rounded-sm text-xs text-muted-foreground underline decoration-foreground/30 underline-offset-3 transition-colors outline-none hover:decoration-foreground focus-visible:ring-[3px] focus-visible:ring-ring/70"
          >
            All proposed companies
          </Link>
        </div>
      </div>
      <section
        aria-label={showFinish ? 'Finish' : current && isCompanyRow(current) ? 'Selected company' : 'Selected person'}
        className="min-w-0 overflow-y-auto px-8 pt-5 pb-12 max-[1100px]:overflow-visible"
      >
        {showFinish ? <FinishPanel plan={plan} /> : panel}
        {current && current.person !== null && passing === current.entryId ? (
          <div className="mt-4 max-w-[720px]">
            <PassChips company={current.company.name} onPass={(reason) => pass(current, reason)} onCancel={() => setPassing(null)} />
          </div>
        ) : null}
        <BelowPlan plan={plan} />
      </section>

      <BenchDrawer
        open={benchOpen}
        onOpenChange={setBenchOpen}
        bench={bench}
        onAdd={(b) => {
          if (actions.addFromBench(b.company.id, b.company.name)) setBenchOpen(false);
        }}
      />
    </div>
  );
}

function PlanContent({ plan, bench }: { plan: PlanView; bench: BenchView | undefined }) {
  const failed = plan.listJob && (plan.listJob.status === 'failed' || plan.listJob.late);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <PlanHead plan={plan} />
      {failed ? (
        <div className="flex-none px-6 pt-4">
          <PlanJobBanner job={plan.listJob} />
        </div>
      ) : null}
      {plan.notReady || plan.rows.length === 0 ? (
        <div className="min-h-0 flex-1 overflow-y-auto">
          {plan.notReady ? <PlanNotReady plan={plan} /> : <PlanEmpty />}
          <BelowPlan className="mx-auto px-8 pb-12" plan={plan} />
        </div>
      ) : (
        <PlanWork plan={plan} bench={bench} />
      )}
    </div>
  );
}

/**
 * Today as a queue of companies (company first, #9 2026-10-26; design 16.1 for the layout): about 8 companies in
 * order, each with what Knock knows and where it stands, ending in a finish line, with the companies in flight under
 * it. He finds the people on LinkedIn and logs them from his terminal (or the row's Log people box). A plan built with
 * person rows (the switch back on) still renders them.
 * Every reason, state, count and not-ready line comes from the client; the page lays them out. The phase-1 picks
 * and backlog live at /proposals.
 */
/**
 * A toast when a new early career opening reaches the bench mid-day: "New opening at Retell AI". The first read seeds
 * what he has seen, so opening the page says nothing; the pulse refetches the bench whenever anything is written.
 */
function useNewOpeningToast(bench: BenchView | undefined, show: () => void) {
  const seen = useRef<Set<Id> | null>(null);
  useEffect(() => {
    if (!bench) return;
    const fresh = bench.companies.filter((b) => b.fresh && b.earlyFit);
    if (seen.current === null) {
      seen.current = new Set(fresh.map((b) => b.company.id));
      return;
    }
    for (const b of fresh) {
      if (seen.current.has(b.company.id)) continue;
      seen.current.add(b.company.id);
      toast(`New opening at ${b.company.name}`, { description: b.queue.whyToday, action: { label: 'Show', onClick: show } });
    }
  }, [bench, show]);
}

export default function TodayPage() {
  const client = usePageClient('plan');
  const plan = useViewQuery(queryKeys.plan, () => client.plan(), { pollMs: pollMs.default });
  const bench = useViewQuery(queryKeys.bench, () => client.bench(), { pollMs: pollMs.default });
  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* The view's name for the outline; the visible heading is the date. */}
      <h1 className="sr-only">Today</h1>
      <WinStrip />
      <div className="min-h-0 flex-1">
        <QueryState query={plan} skeleton={<PlanSkeleton />} empty={null}>
          {(p) => <PlanContent plan={p} bench={bench.data} />}
        </QueryState>
      </div>
    </div>
  );
}
