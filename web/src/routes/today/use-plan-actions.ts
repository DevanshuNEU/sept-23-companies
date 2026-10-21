import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
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

/* ------------------------------------------------------------------ revisions */

/**
 * Every logged step bumps the row's revision, so a write must carry the revision the last one returned. The
 * cached plan only catches up after its refetch, so each commit records the revision it got back (`noteRevision`)
 * and the next write for the same thing takes the newest of that, the cache and the row on screen.
 *
 * Writes for one thing never overlap: each waits for the one before it, so it can use that one's result. A
 * keepalive flush (the tab is closing) cannot wait or read, so it sends at once and counts the writes still in
 * flight for that thing, each of which will bump the revision by one.
 */
type Lane = { tail: Promise<unknown>; inflight: number };
const lanes = new WeakMap<QueryClient, Map<string, Lane>>();

function laneOf(queryClient: QueryClient, key: string): Lane {
  const map = lanes.get(queryClient) ?? new Map<string, Lane>();
  lanes.set(queryClient, map);
  const lane = map.get(key) ?? { tail: Promise.resolve(), inflight: 0 };
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
  const ahead = lane.inflight;
  lane.inflight += 1;
  const result = o.keepalive ? run(newest() + ahead) : lane.tail.then(() => run(newest()));
  const settled = result.then(
    () => undefined,
    () => undefined,
  );
  lane.tail = settled;
  void settled.then(() => {
    lane.inflight -= 1;
  });
  return result;
}

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

  const logStep = (row: PlanRow, kind: StepKind) => {
    const { entryId } = row;
    const id = `log:${entryId}:${kind === 'request' ? 'request' : 'apply'}`;
    if (isPending(id)) return;
    const company = row.company.name;
    if (kind === 'request') {
      if (row.channel === 'email') return void toast('Review this email in the Outbox.');
      if (!row.person) return void toast(`Find someone at ${company} first.`);
    } else if (!row.role || !row.steps.some((s) => s.kind === 'apply')) {
      return void toast('No role to apply for.');
    }
    if (row.state === 'skipped') return void toast(`${company} is carried to tomorrow.`);
    if (row.state === 'done' || row.steps.find((s) => s.kind === kind)?.at) return void toast('Already logged.');

    const key = `plan:${entryId}:${kind}`;
    const label = stepLabel(row, kind);
    const delta = finishes(row, kind) ? 1 : 0;
    const personId = row.person?.person.id;
    const postingId = row.role?.postingId;
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
          kind === 'request' && personId !== undefined
            ? client.logRequest(entryId, { personId, idempotencyKey: key }, revision, o)
            : client.logApplied(entryId, { postingId: postingId as Id, idempotencyKey: key }, revision, o),
        ),
    });
  };

  const logRequest = (row: PlanRow) => logStep(row, 'request');
  const logApplied = (row: PlanRow) => logStep(row, 'apply');

  const skip = (row: PlanRow) => {
    const { entryId } = row;
    const id = `skip:${entryId}`;
    if (isPending(id)) return;
    const company = row.company.name;
    if (row.state === 'done') return void toast(`${company} is already done.`);
    if (row.state === 'skipped') return void toast(`${company} is already carried to tomorrow.`);
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
  };

  const noteOf = (queryClient: QueryClient, row: PlanRow) => cachedRow(queryClient, row.entryId)?.note?.revision;

  const saveNote = (row: PlanRow, text: string, reasonLine: string) => {
    const note = row.note;
    if (!note) return;
    const label = `Saved the note for ${row.person?.person.name ?? row.company.name}.`;
    write({
      id: `note:${note.noteId}`,
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
    const label = `Confirmed the reason line for ${row.person?.person.name ?? row.company.name}.`;
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

  return { logRequest, logApplied, skip, saveNote, confirmReason, answer, seeWins };
}
