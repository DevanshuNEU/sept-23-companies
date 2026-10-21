import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  ApiError,
  queryKeys,
  useClient,
  type Id,
  type OutcomeKind,
  type OutcomePromptView,
  type PlanRow,
  type PlanView,
  type PromptTouch,
  type ReplyKind,
  type Revision,
  type ShellSummary,
  type StepKind,
  type WinsView,
  type WriteOptions,
} from '@/api';
import { replyKindStatus } from '@/lib/status';
import { newestRevision, noteRevision } from '@/pending/revisions';
import { usePendingControls, usePendingWrite, type Patch } from '@/pending/pending';
import { withStepLogged } from './plan-steps';

type Reply = Exclude<ReplyKind, 'unsorted' | 'auto_reply'>;

/** Said no and asked to stop add a no-go, so they commit at once with no Undo (phase 1 lead decision 2). */
const AT_ONCE = new Set<Reply>(['said_no', 'asked_to_stop']);

const ADD = (company: string) => `To change it, open ${company}'s page.`;
/** A press that does nothing says why, once. */
const refuse = (text: string): false => {
  toast(text);
  return false;
};
const sentence = (text: string) => (/[.!?]$/.test(text.trim()) ? text.trim() : `${text.trim()}.`);

/* ------------------------------------------------------------------ revisions */

/**
 * Skip, notes and outcomes are state-changing writes, so each carries the revision the last write on the same
 * thing returned. The cached plan only catches up after its refetch, so each commit records the revision it
 * gets back (`noteRevision`) and the next write takes the newest of that, the cache and the row on screen.
 *
 * Writes for one thing never overlap: each waits for the one before it, so it can use that one's result. A
 * keepalive flush (the tab is closing) cannot wait or read, so it sends at once with the newest revision known.
 * Step logs are append-only and the API accepts them at any revision, so a flush that sends two of them lands
 * both in whatever order they arrive. A skip or note save flushed beside another write on the same row can be
 * rejected as stale, and says so.
 */
type Lane = { tail: Promise<unknown> };
const lanes = new WeakMap<QueryClient, Map<string, Lane>>();

function laneOf(queryClient: QueryClient, key: string): Lane {
  const map = lanes.get(queryClient) ?? new Map<string, Lane>();
  lanes.set(queryClient, map);
  const lane = map.get(key) ?? { tail: Promise.resolve() };
  map.set(key, lane);
  return lane;
}

function sendVersioned<T extends { revision: Revision }>(
  queryClient: QueryClient,
  key: string,
  seen: Revision,
  cached: () => Revision | undefined,
  o: WriteOptions,
  call: (revision: Revision) => Promise<T>,
): Promise<T> {
  const lane = laneOf(queryClient, key);
  const newest = () => newestRevision(queryClient, key, cached(), seen) ?? seen;
  const run = async (revision: Revision): Promise<T> => {
    const result = await call(revision);
    noteRevision(queryClient, key, result.revision);
    return result;
  };
  const result = o.keepalive ? run(newest()) : lane.tail.then(() => run(newest()));
  lane.tail = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

let saves = 0;
/** The newest save per note, per client, so the view can tell a note whose save has not landed yet. */
const lastSave = new WeakMap<QueryClient, Map<Id, string>>();

const cachedPlan = (queryClient: QueryClient) => queryClient.getQueryData<PlanView>(queryKeys.plan);
const cachedRow = (queryClient: QueryClient, entryId: Id) => cachedPlan(queryClient)?.rows.find((r) => r.entryId === entryId);

/* ------------------------------------------------------------------ patches */

const shellPatch = (change: (s: ShellSummary) => ShellSummary = (s) => s): Patch<ShellSummary> => ({ key: queryKeys.shell, apply: change });

function planRowPatch(entryId: Id, change: (row: PlanRow) => PlanRow): Patch<PlanView> {
  return {
    key: queryKeys.plan,
    apply: (plan) => ({ ...plan, rows: plan.rows.map((r) => (r.entryId === entryId ? change(r) : r)) }),
  };
}

/** Nothing is laid over the view: the key is named so the view refetches once the commit has landed. */
const refetchOnly = <T,>(key: readonly unknown[]): Patch<T> => ({ key, apply: (data) => data });

/** Whether logging this step is the one that finishes the row. */
const finishes = (row: PlanRow, kind: StepKind) => row.state === 'todo' && row.steps.every((s) => s.kind === kind || s.at !== null);

const stepLabel = (row: PlanRow, kind: StepKind): string =>
  kind === 'request'
    ? `Logged: request sent to ${row.person?.person.name ?? row.company.name}.`
    : `Logged: applied for ${row.role?.title ?? 'the role'} at ${row.company.name}.`;

function outcomeLabel(t: PromptTouch, kind: OutcomeKind, replyKind: Reply | undefined): string {
  const name = t.person.name;
  switch (kind) {
    case 'accepted':
      return `Logged: ${name} accepted.`;
    case 'replied':
      return replyKind ? `Logged: reply from ${name} (${replyKindStatus[replyKind].label}).` : `Logged: reply from ${name}.`;
    case 'referred':
      return `Logged: ${name} referred you.`;
    case 'interview':
      return `Logged: interview at ${t.company.name}.`;
    case 'offer':
      return `Logged: offer from ${t.company.name}.`;
    case 'quiet':
      return `Logged: nothing yet from ${name}.`;
  }
}

/**
 * The plan's actions, each one a pending write with its patches, so the row, the done count and the sidebar move
 * at once and Undo (or `z`) takes the write back inside the 8 second window.
 *
 * The UI never decides what counts as a win or which recognition to show: an answer lays nothing over the wins
 * view. It names the wins and shell keys so both refetch the moment the commit lands, and an undone answer never
 * appears.
 */
export function usePlanActions() {
  const client = useClient();
  const write = usePendingWrite();
  const { isPending } = usePendingControls();
  const queryClient = useQueryClient();

  /** True when the log was queued; a refused or repeated press returns false (and says why, unless it repeats). */
  const logStep = (row: PlanRow, kind: StepKind): boolean => {
    const { entryId } = row;
    const id = `log:${entryId}:${kind === 'request' ? 'request' : 'apply'}`;
    if (isPending(id)) return false;
    const company = row.company.name;
    // The sender and the reply reader log email, so no key logs anything on an email row.
    if (row.channel === 'email') return refuse('Review this email in the Outbox.');
    // The row on screen against the newest poll: a key that lands before the view drew a changed row is refused.
    const latest = cachedRow(queryClient, entryId);
    let call: (revision: Revision, o: WriteOptions) => Promise<PlanRow>;
    const key = `plan:${entryId}:${kind}`;
    if (kind === 'request') {
      if (!row.person) return refuse(`Find someone at ${company} first.`);
      const personId = row.person.person.id;
      if (latest && latest.person?.person.id !== personId) {
        const now = latest.person ? ` to ${latest.person.person.name}` : '';
        return refuse(`Not logged. The person on ${company}'s row changed${now}. Check the row, then press R again.`);
      }
      call = (revision, o) => client.logRequest(entryId, { personId, idempotencyKey: key }, revision, o);
    } else {
      if (!row.role || !row.steps.some((s) => s.kind === 'apply')) return refuse('No role to apply for.');
      const postingId = row.role.postingId;
      if (latest && latest.role?.postingId !== postingId) {
        const now = latest.role ? ` to ${latest.role.title}` : '';
        return refuse(`Not logged. The role on ${company}'s row changed${now}. Check the row, then press P again.`);
      }
      call = (revision, o) => client.logApplied(entryId, { postingId, idempotencyKey: key }, revision, o);
    }
    if (row.state === 'skipped') return refuse(`${company} is carried to tomorrow.`);
    if (row.state === 'done' || row.steps.find((s) => s.kind === kind)?.at) return refuse('Already logged.');

    const label = stepLabel(row, kind);
    const delta = finishes(row, kind) ? 1 : 0;
    write({
      id,
      label,
      afterCommit: `${label} ${ADD(company)}`,
      patches: [
        { key: queryKeys.plan, apply: (p: PlanView) => withStepLogged(p, entryId, kind, p.asOf) },
        shellPatch((s) => ({ ...s, plan: { ...s.plan, done: s.plan.done + delta } })),
      ],
      commit: (o) =>
        sendVersioned(queryClient, `plan:${entryId}`, row.revision, () => cachedRow(queryClient, entryId)?.revision, o, (revision) =>
          call(revision, o).catch((err: unknown) => {
            // The row changed inside the window (another person or role, carried elsewhere): say what did not happen
            // and what to do, not only the server's reason.
            if (err instanceof ApiError && err.code === 'not_allowed') {
              throw new ApiError('not_allowed', `Not logged. ${sentence(err.message)} Check ${company}'s row, then log it again.`);
            }
            throw err;
          }),
        ),
    });
    return true;
  };

  const logRequest = (row: PlanRow) => logStep(row, 'request');
  const logApplied = (row: PlanRow) => logStep(row, 'apply');

  /** True when the carry was queued. */
  const skip = (row: PlanRow): boolean => {
    const { entryId } = row;
    const id = `skip:${entryId}`;
    if (isPending(id)) return false;
    const company = row.company.name;
    if (row.state === 'done') return refuse(`${company} is already done.`);
    if (row.state === 'skipped') return refuse(`${company} is already carried to tomorrow.`);
    const label = `Carried to tomorrow: ${company}.`;
    write({
      id,
      label,
      afterCommit: `${label} ${ADD(company)}`,
      patches: [planRowPatch(entryId, (r) => (r.state === 'todo' ? { ...r, state: 'skipped' } : r)), shellPatch()],
      commit: (o) =>
        sendVersioned(queryClient, `plan:${entryId}`, row.revision, () => cachedRow(queryClient, entryId)?.revision, o, (revision) =>
          client.skipRow(entryId, revision, o),
        ),
    });
    return true;
  };

  const noteOf = (queryClient: QueryClient, row: PlanRow) => cachedRow(queryClient, row.entryId)?.note?.revision;

  const saveNote = (row: PlanRow, text: string, reasonLine: string) => {
    const note = row.note;
    if (!note) return;
    const label = `Saved the note for ${row.person?.person.name ?? row.company.name}.`;
    // One id per save: the store ignores a write whose id is still active, and a second save must not be dropped. The
    // note's lane keeps the saves in order, and each sends the full text and reason line, so the later one supersedes.
    const id = `note:${note.noteId}:${++saves}`;
    const map = lastSave.get(queryClient) ?? new Map<Id, string>();
    map.set(note.noteId, id);
    lastSave.set(queryClient, map);
    write({
      id,
      label,
      afterCommit: label,
      patches: [
        planRowPatch(row.entryId, (r) => {
          if (!r.note) return r;
          const reasonState = reasonLine === r.note.reasonLine ? r.note.reasonState : 'written';
          return { ...r, note: { ...r.note, text, chars: text.length, reasonLine, reasonState, needsYourLine: reasonState === 'proposed' } };
        }),
      ],
      commit: (o) =>
        sendVersioned(queryClient, `note:${note.noteId}`, note.revision, () => noteOf(queryClient, row), o, (revision) =>
          client.saveNote(note.noteId, { text, reasonLine, revision }, o),
        ),
    });
  };

  const confirmReason = (row: PlanRow) => {
    const note = row.note;
    if (!note) return;
    const label = 'Confirmed your line.';
    write({
      id: `confirm:${note.noteId}`,
      label,
      afterCommit: label,
      patches: [
        planRowPatch(row.entryId, (r) => (r.note ? { ...r, note: { ...r.note, reasonState: 'confirmed', needsYourLine: false } } : r)),
      ],
      commit: (o) =>
        sendVersioned(queryClient, `note:${note.noteId}`, note.revision, () => noteOf(queryClient, row), o, (revision) =>
          client.confirmReason(note.noteId, revision, o),
        ),
    });
  };

  const answer = (t: PromptTouch, kind: OutcomeKind, replyKind?: Reply) => {
    const label = outcomeLabel(t, kind, replyKind);
    const cachedTouch = () => queryClient.getQueryData<OutcomePromptView>(queryKeys.outcomePrompt)?.touches.find((x) => x.touchId === t.touchId)?.revision;
    write({
      id: `answer:${t.touchId}`,
      label,
      afterCommit: `${label} ${ADD(t.company.name)}`,
      immediate: kind === 'replied' && replyKind !== undefined && AT_ONCE.has(replyKind),
      patches: [
        {
          key: queryKeys.outcomePrompt,
          apply: (v: OutcomePromptView) => ({ ...v, touches: v.touches.filter((x) => x.touchId !== t.touchId) }),
        },
        refetchOnly<WinsView>(queryKeys.wins),
        refetchOnly<PlanView>(queryKeys.plan),
        shellPatch(),
      ],
      commit: (o) =>
        sendVersioned(queryClient, `touch:${t.touchId}`, t.revision, cachedTouch, o, (revision) =>
          (replyKind ? client.logOutcome(t.touchId, kind, revision, o, replyKind) : client.logOutcome(t.touchId, kind, revision, o)),
        ),
    });
  };

  const seeWins = (ids: Id[]) => {
    if (ids.length === 0) return;
    const named = new Set(ids);
    const unseenNow = () => queryClient.getQueryData<WinsView>(queryKeys.wins)?.wins.filter((w) => named.has(w.winId) && w.unseen).length ?? ids.length;
    const count = unseenNow();
    write({
      id: 'wins:seen',
      label: 'Marked as seen.',
      afterCommit: 'Marked as seen.',
      immediate: true,
      silent: true,
      patches: [
        {
          key: queryKeys.wins,
          apply: (v: WinsView) => {
            const wins = v.wins.map((w) => (named.has(w.winId) ? { ...w, unseen: false } : w));
            return { ...v, wins, unseen: wins.filter((w) => w.unseen).length };
          },
        },
        shellPatch((s) => ({ ...s, winsUnseen: Math.max(0, s.winsUnseen - count) })),
      ],
      commit: (o) => client.markWinsSeen(ids, o),
    });
  };

  /** True while the newest save of this note waits in its window or for its commit: its checks are unknown. */
  const noteSaving = (noteId: Id): boolean => {
    const id = lastSave.get(queryClient)?.get(noteId);
    return id !== undefined && isPending(id);
  };
  /** True while Confirm line for this note waits in its window or for its commit. */
  const confirming = (noteId: Id): boolean => isPending(`confirm:${noteId}`);

  return { logRequest, logApplied, skip, saveNote, confirmReason, answer, seeWins, noteSaving, confirming };
}
