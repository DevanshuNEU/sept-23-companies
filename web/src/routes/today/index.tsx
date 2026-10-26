import { useId, useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { toast } from 'sonner';
import { HelpCircle, Terminal } from 'lucide-react';
import {
  pollMs,
  queryKeys,
  usePageClient,
  type Id,
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
import { RemoveDialog } from '@/routes/proposals/remove-dialog';
import { EmailRowPanel } from './email-row-panel';
import { FinishPanel } from './finish-panel';
import { FindSomeonePanel } from './find-someone-panel';
import type { NoteEdit } from './note-editor';
import { KeyHint, PersonPanel, type PanelActions } from './plan-panel';
import { PlanEmpty, PlanJobBanner, PlanNotReady, PlanSkeleton } from './plan-states';
import { OutcomePrompt } from './outcome-prompt';
import { nextTodoAfter } from './plan-steps';
import { usePlanActions } from './use-plan-actions';
import { WatchList } from './watch-list';

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
  return `${row.person?.person.name ?? 'Find someone'} at ${row.company.name}`;
}

/** Whether logging this step is the one that finishes the row, so the selection moves on. */
const finishes = (row: PlanRow, kind: StepKind) =>
  row.state === 'todo' && row.steps.some((s) => s.kind === kind && s.at === null) && row.steps.every((s) => s.kind === kind || s.at !== null);

/** The rows as they are once this one is no longer to do, for `nextTodoAfter`. */
const without = (rows: PlanRow[], entryId: Id): PlanRow[] => rows.map((r) => (r.entryId === entryId ? { ...r, state: 'done' } : r));

/**
 * Below the plan, quiet (design 16.1): the 7-day question, then the watch list. Each part draws nothing when it has nothing to say, so the
 * rule above them only shows with something under it.
 */
function BelowPlan({ className }: { className?: string }) {
  return (
    <div className={cn('mt-8 grid max-w-[720px] empty:hidden', className)}>
      <OutcomePrompt />
      <WatchList />
    </div>
  );
}

function PlanHead({ plan }: { plan: PlanView }) {
  const { used, limit, hardUsed, hardLimit } = plan.linkedinMonth;
  // Once nothing is left to do, the head says what carried over, as the finish line does (design 16.2).
  const finished = plan.rows.length > 0 && plan.rows.every((r) => r.state !== 'todo');
  const carried = finished ? plan.rows.filter((r) => r.state === 'skipped').length : 0;
  return (
    <div className="flex flex-none items-end gap-4 border-b px-6 pt-5 pb-4">
      <div>
        <h2 className="text-xl leading-7 font-semibold tracking-[-0.01em]">{formatLongDate(plan.date)}</h2>
        {plan.notReady ? null : (
          <p className="mt-0.5 text-[13px] text-muted-foreground tabular-nums">{`${plural(plan.total, 'person', 'people')}, ${plan.done} done${carried > 0 ? `, ${carried} carried over` : ''}`}</p>
        )}
      </div>
      <div className="ml-auto flex items-center gap-2 text-[13px]">
        <p data-quota="month" className="flex items-baseline gap-1.5 tabular-nums">
          <span className="text-muted-foreground">Requests this month</span> <b className="font-semibold">{used}</b>{' '}
          <span className="text-muted-foreground">{`of ${limit}`}</span>
        </p>
        <Tooltip>
          <TooltipTrigger asChild>
            <button type="button" aria-label="About this limit" className="rounded-sm text-muted-foreground outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">
              <HelpCircle aria-hidden className="size-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent className="max-w-64">
            {`The plan uses ${limit} of LinkedIn's ${hardLimit} a month. ${hardUsed} of ${hardLimit} are used, counting requests sent outside the plan.`}
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
function PlanWork({ plan }: { plan: PlanView }) {
  const navigate = useNavigate();
  const actions = usePlanActions();
  const [pinned] = useState(() => plan.rows.find((r) => r.state === 'todo')?.entryId);
  const [edits, setEdits] = useState<ReadonlyMap<Id, NoteEdit>>(new Map());
  const [removing, setRemoving] = useState<PlanRow | null>(null);
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
      if (row) navigate(`/companies/${row.company.id}`);
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

  const log = (row: PlanRow, kind: StepKind) => {
    const done = finishes(row, kind);
    const queued = kind === 'request' ? actions.logRequest(row) : actions.logApplied(row);
    if (queued && done) moveOn(row);
  };
  const carry = (row: PlanRow) => {
    if (actions.skip(row)) moveOn(row);
  };
  /** Not a fit opens its dialog only when the API could remove the company; else it says why, once. */
  const askNotAFit = (row: PlanRow) => {
    const blocked = actions.notAFitRefusal(row);
    if (blocked) return void toast(blocked);
    setRemoving(row);
  };
  /** An open edit that differs from the saved note: Copy would copy the saved text, so it waits for Save. */
  const unsavedEdit = (row: PlanRow): boolean => {
    const draft = row.note ? edits.get(row.note.noteId) : undefined;
    return draft !== undefined && row.note !== null && (draft.text !== row.note.text || draft.line !== row.note.reasonLine);
  };
  const copyNote = (row: PlanRow) => {
    if (row.findSomeone) return void copy(row.findSomeone.handoff, 'Copied the handoff for Claude.');
    const note = row.note;
    if (!note) return void toast(row.channel === 'email' ? 'No note on this row. Review the email in the Outbox.' : 'No note on this row yet.');
    if (actions.noteSaving(note.noteId)) return void toast('Not copied. The note is saving, and the checks run again first.');
    if (unsavedEdit(row)) return void toast(`Not copied. Save your edit first (${isMac() ? 'Cmd+S' : 'Ctrl+S'}), then copy.`);
    if (note.checks.length === 0) return void toast('Not copied. The checks have not run yet. Try again in a moment.');
    const blocks = note.checks.filter((c) => c.severity === 'block').length;
    if (blocks > 0) return void toast(`Not copied. ${plural(blocks, 'check blocks', 'checks block')} this note. Fix it, then copy.`);
    const unlinked = note.facts.filter((f) => f.inNote && !safeHref(f.source.url)).length;
    const done =
      unlinked === 0
        ? 'Copied the note.'
        : unlinked === 1
          ? 'Copied. One fact has no link: check it before you send.'
          : `Copied. ${unlinked} facts have no link: check them before you send.`;
    void copy(note.text, done);
  };
  const copyAllHandoffs = () => {
    const handoffs = rows.flatMap((r) => (r.state === 'todo' && r.findSomeone ? [r.findSomeone.handoff] : []));
    if (handoffs.length === 0) return void toast('Nothing copied. No row today needs someone found.');
    void copy(handoffs.join('\n\n'), `Copied ${plural(handoffs.length, 'handoff', 'handoffs')} for Claude.`);
  };
  const edit = (row: PlanRow) => {
    const note = row.note;
    if (!note) return void toast(row.findSomeone ? 'No note yet. Claude writes it once someone is found.' : 'No note on this row.');
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
    actions.confirmReason(row);
    focusPanel();
  };
  const open = (url: string | null | undefined, none: string) => {
    if (!openExternal(url)) toast(none);
  };
  const panelActions = (row: PlanRow): PanelActions => ({
    onLog: (kind) => log(row, kind),
    onCarry: () => carry(row),
    onNotFit: () => askNotAFit(row),
    onCopy: () => copyNote(row),
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
  const onRow: Binding[] = [
    { keys: 'l', label: 'Open profile', group, enabled: has, run: on((r) => open(r.person?.person.linkedinUrl, r.person ? `No profile link for ${r.person.person.name}.` : 'Nobody on this row yet. Press S to search.')) },
    { keys: 'o', label: 'Open role', group, enabled: has, run: on((r) => open(r.role?.url, 'No role on this row.')) },
    { keys: 'a', label: 'Open the apply page', group, enabled: has, run: on((r) => open(r.applyUrl, 'No apply page on this row.')) },
    { keys: 's', label: 'Open LinkedIn search', group, enabled: has, run: on((r) => open(r.findSomeone?.searchUrl, 'This row already has someone. Press L to open their profile.')) },
    { keys: 'c', label: 'Copy the note or handoff', group, enabled: has, run: on(copyNote) },
    { keys: 'e', label: 'Edit the note', group, enabled: has, run: on(edit) },
    { keys: 'y', label: 'Confirm your line', group, enabled: has, run: on(confirm) },
    { keys: 'r', label: 'Log request sent', group, enabled: has, run: on((r) => log(r, 'request')) },
    { keys: 'p', label: 'Log applied', group, enabled: has, run: on((r) => log(r, 'apply')) },
    { keys: 'x', label: 'Carry to tomorrow', group, enabled: has, run: on(carry) },
    // Only a row to do has a Not a fit button, so only a row to do answers n.
    { keys: 'n', label: 'Not a fit', group, enabled: current?.state === 'todo', run: on(askNotAFit) },
    { keys: 'mod+s', label: 'Save the note', group, enabled: editing, run: on(save) },
  ];
  // These act on the selected person, so a key right after j, k or a click waits for that move to render.
  useHotkeys(onRow, { selection: selected });
  useHotkeys([{ keys: 'shift+c', label: 'Copy every find-someone handoff', group, run: copyAllHandoffs }]);

  /* ------------------------------------------------------------------ layout */

  let panel = (
    <p className="max-w-[62ch] text-sm text-muted-foreground">
      {allDone ? 'Nothing is left to do on the plan. Pick anyone on the left to see what you did.' : 'Pick a person on the left to see their work.'}
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
        <FindSomeonePanel key={current.entryId} row={current} asOf={plan.asOf} actions={a} headingId={headingId} />
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
            Copy every find-someone handoff
            <span aria-hidden className="inline-flex gap-0.5">
              <KeyHint k="⇧" />
              <KeyHint k="C" />
            </span>
          </Button>
          <Link
            to="/proposals"
            className="ml-2.5 rounded-sm text-xs text-muted-foreground underline decoration-border underline-offset-3 outline-none hover:decoration-current focus-visible:ring-[3px] focus-visible:ring-ring/50"
          >
            All proposed companies
          </Link>
        </div>
      </div>
      <section aria-label={showFinish ? 'Finish' : 'Selected person'} className="min-w-0 overflow-y-auto px-8 pt-5 pb-12 max-[1100px]:overflow-visible">
        {showFinish ? <FinishPanel plan={plan} /> : panel}
        <BelowPlan />
      </section>

      <RemoveDialog
        name={removing?.company.name ?? ''}
        open={removing !== null}
        onOpenChange={(o) => {
          if (!o) setRemoving(null);
        }}
        onRemove={(reason, note) => {
          if (removing && actions.notAFit(removing, reason, note)) moveOn(removing);
        }}
      />
    </div>
  );
}

function PlanContent({ plan }: { plan: PlanView }) {
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
          <BelowPlan className="mx-auto px-8 pb-12" />
        </div>
      ) : (
        <PlanWork plan={plan} />
      )}
    </div>
  );
}

/**
 * Today as a plan of people (design 16.1): about 7 people in order, one next step each, ending in a finish line.
 * Every reason, state, count and not-ready line comes from the client; the page lays them out. The phase-1 picks
 * and backlog live at /proposals.
 */
export default function TodayPage() {
  const client = usePageClient('plan');
  const plan = useViewQuery(queryKeys.plan, () => client.plan(), { pollMs: pollMs.default });
  return (
    <div className="h-full min-h-0">
      {/* The view's name for the outline; the visible heading is the date. */}
      <h1 className="sr-only">Today</h1>
      <QueryState query={plan} skeleton={<PlanSkeleton />} empty={null}>
        {(p) => <PlanContent plan={p} />}
      </QueryState>
    </div>
  );
}
