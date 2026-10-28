import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  ApiError,
  queryKeys,
  usePageClient,
  type Id,
  type OutcomeKind,
  type OutcomePromptView,
  type BenchView,
  type BrowseCompany,
  type PassReason,
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
import { benchToPick, isProvisional } from './picks';

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
 * Every write on a row, a note or a touch carries the revision the last write on the same thing returned: the API
 * checks it on each one (step logs too) and moves it on by exactly one. The cached plan only catches up after its
 * refetch, so each commit records the revision it gets back (`noteRevision`) and the next write takes the newest of
 * that, the cache and the row on screen.
 *
 * Writes for one thing never overlap: each waits for the one before it, so it can use that one's result. A
 * keepalive flush (the tab is closing) cannot wait or read, so it sends at once with the newest revision known plus
 * one for each write of the lane already sent and not yet answered: the revision each one before it will leave. Sent
 * in queue order, they land in that order; one that arrives ahead of the write before it is refused as stale, and
 * the page is gone by then.
 */
type Lane = { tail: Promise<unknown>; started: number };
const lanes = new WeakMap<QueryClient, Map<string, Lane>>();

function laneOf(queryClient: QueryClient, key: string): Lane {
  const map = lanes.get(queryClient) ?? new Map<string, Lane>();
  lanes.set(queryClient, map);
  const lane = map.get(key) ?? { tail: Promise.resolve(), started: 0 };
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
  const newest = () => (newestRevision(queryClient, key, cached(), seen) ?? seen) + lane.started;
  const run = async (revision: Revision): Promise<T> => {
    lane.started += 1;
    try {
      const result = await call(revision);
      noteRevision(queryClient, key, result.revision);
      return result;
    } finally {
      lane.started -= 1;
    }
  };
  const result = o.keepalive ? run(newest()) : lane.tail.then(() => run(newest()));
  lane.tail = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

let saves = 0;
let pastes = 0;
/** The newest save per note, per client, so the view can tell a note whose save has not landed yet. */
const lastSave = new WeakMap<QueryClient, Map<Id, string>>();

const passId = (row: PlanRow) => `pass:${row.entryId}`;
/** The pending write of a swap, named by the company coming in (its provisional row has the id `-companyId`). */
const swapInId = (companyId: Id) => `swap-in:${companyId}`;
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

const stepLabel = (row: PlanRow, kind: StepKind, asked?: string): string =>
  kind === 'request'
    ? `Logged: request sent to ${row.person?.person.name ?? asked ?? row.company.name}.`
    : `Logged: applied for ${row.role?.title ?? 'the role'} at ${row.company.name}.`;

function outcomeLabel(t: PromptTouch, kind: OutcomeKind, replyKind: Reply | undefined): string {
  // An application has no person, so its outcome names the company.
  const name = t.person?.name ?? t.company.name;
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
 * appears. `page` names the view whose client the writes go through: Today by default, and the win log passes `wins`.
 */
export function usePlanActions(page: Parameters<typeof usePageClient>[0] = 'plan') {
  const client = usePageClient(page);
  const write = usePendingWrite();
  const { isPending } = usePendingControls();
  const queryClient = useQueryClient();

  /**
   * Why a press on this row must do nothing, or null. The row is a company still coming in from the bench (its Not now
   * waits in its window), he said Not now to it (still on its way), or the newest plan no longer has the row.
   */
  const goneOrNoGo = (row: PlanRow, latest: PlanRow | undefined, what: 'Not logged' | 'Not carried'): string | null => {
    const company = row.company.name;
    if (isProvisional(row)) {
      const saved = isPending(swapInId(-row.entryId)) ? 'your swap' : 'your Not now';
      return `${what}. ${company} joins today's list once ${saved} is saved. Press Z to undo it.`;
    }
    if (isPending(passId(row))) return `${what}. You said Not now to ${company}.`;
    if (cachedPlan(queryClient) && !latest) return `${what}. ${company}'s row is no longer on today's plan.`;
    return null;
  };

  /**
   * True when the log was queued; a refused or repeated press returns false (and says why, unless it repeats). A
   * company row (nobody on it: company first) logs a request for `asked`, the name of the person he asked, and logs
   * applied like any row with a role.
   */
  const logStep = (row: PlanRow, kind: StepKind, asked?: string): boolean => {
    const { entryId } = row;
    const id = `log:${entryId}:${kind === 'request' ? 'request' : 'apply'}`;
    if (isPending(id)) return false;
    const company = row.company.name;
    // The sender and the reply reader log email, so no key logs anything on an email row.
    if (row.channel === 'email') return refuse('Review this email in the Outbox.');
    const name = asked?.split(/\s+/).filter(Boolean).join(' ') ?? '';
    // The row on screen against the newest poll: a key that lands before the view drew a changed row is refused.
    const latest = cachedRow(queryClient, entryId);
    const gone = goneOrNoGo(row, latest, 'Not logged');
    if (gone) return refuse(gone);
    let call: (revision: Revision, o: WriteOptions) => Promise<PlanRow>;
    const key = `plan:${entryId}:${kind}`;
    if (kind === 'request' && !row.person) {
      // A company row: the request names the person he asked, typed on the row.
      if (row.state === 'todo' && !row.steps.find((s) => s.kind === 'request')?.at && !name) {
        return refuse(`Not logged. Type the name of the person you asked at ${company}, then log it.`);
      }
      if (latest && latest.person) return refuse(`Not logged. ${company}'s row now names ${latest.person.person.name}. Check the row, then press R again.`);
      call = (revision, o) => client.logRequest(entryId, { personName: name, idempotencyKey: key }, revision, o);
    } else if (kind === 'request' && row.person) {
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
        return refuse(`Not logged. The role on ${company}'s row changed${now}. Check the row, then press A again.`);
      }
      call = (revision, o) => client.logApplied(entryId, { postingId, idempotencyKey: key }, revision, o);
    }
    if (row.state === 'skipped') return refuse(`${company} is carried to tomorrow.`);
    if (row.state === 'done' || row.steps.find((s) => s.kind === kind)?.at) return refuse('Already logged.');

    const label = stepLabel(row, kind, name || undefined);
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

  const logRequest = (row: PlanRow, asked?: string) => logStep(row, 'request', asked);
  const logApplied = (row: PlanRow) => logStep(row, 'apply');

  /**
   * Company first (#9, P2): the people he asked at a company row, pasted as `Name | Title | note` lines, for when his
   * terminal cannot log them. The API reads the lines; the row's request step shows as logged at once and Undo takes
   * the paste back inside the window. Each paste has its own key, so a retry writes once. A note check the API
   * reports (his note already went out, so it is logged as sent) is said once the log lands. True when queued.
   */
  const logPeople = (row: PlanRow, text: string): boolean => {
    const { entryId } = row;
    const company = row.company.name;
    if (row.channel === 'email' || row.person) return refuse(`Not logged. ${company}'s row is not a company row.`);
    const lines = text.split('\n').filter((l) => l.trim());
    if (lines.length === 0) return refuse('Not logged. Paste one line per person: Name | Title | note.');
    if (lines.length > 10) return refuse(`Not logged. Paste at most 10 people at a time; this has ${lines.length}.`);
    const gone = goneOrNoGo(row, cachedRow(queryClient, entryId), 'Not logged');
    if (gone) return refuse(gone);
    if (row.state === 'skipped') return refuse(`${company} is carried to tomorrow.`);
    const key = `plan:${entryId}:people:${++pastes}-${Date.now().toString(36)}`;
    const id = `people:${entryId}:${pastes}`;
    const label = `Logged: ${lines.length === 1 ? '1 person' : `${lines.length} people`} at ${company}.`;
    const delta = finishes(row, 'request') ? 1 : 0;
    write({
      id,
      label,
      afterCommit: `${label} ${ADD(company)}`,
      patches: [
        { key: queryKeys.plan, apply: (p: PlanView) => withStepLogged(p, entryId, 'request', p.asOf) },
        shellPatch((s) => ({ ...s, plan: { ...s.plan, done: s.plan.done + delta } })),
      ],
      commit: (o) =>
        sendVersioned(queryClient, `plan:${entryId}`, row.revision, () => cachedRow(queryClient, entryId)?.revision, o, (revision) =>
          client.logPeople(entryId, { lines: text, idempotencyKey: key }, revision, o).then(
            (res) => {
              const warned = res.people.filter((p) => p.warnings.length > 0);
              if (warned.length > 0) {
                toast.warning(`Logged as sent. A note check fired for ${warned.map((p) => p.name).join(', ')}: ${warned[0]?.warnings[0] ?? ''}`);
              }
              return res.row;
            },
            (err: unknown) => {
              if (err instanceof ApiError && err.code === 'not_allowed') {
                throw new ApiError('not_allowed', `Not logged. ${sentence(err.message)} Nothing was written for ${company}.`);
              }
              throw err;
            },
          ),
        ),
    });
    return true;
  };

  /** His LinkedIn people search for a company row: S opens it from now on. True when the save was queued. */
  const saveSearch = (row: PlanRow, url: string): boolean => {
    const clean = url.trim();
    const company = row.company.name;
    if (!/^https:\/\/(www\.)?linkedin\.com\/search\/results\/people\/?/.test(clean)) {
      return refuse('Not saved. Paste the address of a LinkedIn people search; it starts with https://www.linkedin.com/search/results/people/.');
    }
    if (row.queue?.searchSaved && row.queue.peopleSearchUrl === clean) return refuse(`That search is already saved for ${company}.`);
    const label = `Saved the search for ${company}. S opens it.`;
    write({
      id: `search:${row.entryId}`,
      label,
      afterCommit: label,
      immediate: true,
      patches: [
        planRowPatch(row.entryId, (r) => ({
          ...r,
          queue: r.queue ? { ...r.queue, peopleSearchUrl: clean, searchSaved: true } : r.queue,
          findSomeone: r.findSomeone ? { ...r.findSomeone, searchUrl: clean } : r.findSomeone,
        })),
      ],
      commit: (o) => client.saveSearch(row.entryId, clean, o),
    });
    return true;
  };

  /** True when the carry was queued. */
  const skip = (row: PlanRow): boolean => {
    const { entryId } = row;
    const id = `skip:${entryId}`;
    if (isPending(id)) return false;
    const company = row.company.name;
    const gone = goneOrNoGo(row, cachedRow(queryClient, entryId), 'Not carried');
    if (gone) return refuse(gone);
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
          // His save makes the note his, and his note is his reason (#9, 2026-10-26): nothing asks for the line.
          return { ...r, note: { ...r.note, text, chars: text.length, reasonLine, reasonState, savedBy: 'devanshu', needsYourLine: false } };
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

  /**
   * Marks wins seen. By default at once and without a word (opening the win log does it for every new win). With
   * `undo` it is a write like the others: the toast says `Marked seen.` with Undo, and the wins come back if he takes
   * it back inside the window (the W key and the Mark seen button on Today).
   */
  const seeWins = (ids: Id[], opts: { undo?: boolean } = {}) => {
    if (ids.length === 0) return;
    const named = new Set(ids);
    const unseenNow = () => queryClient.getQueryData<WinsView>(queryKeys.wins)?.wins.filter((w) => named.has(w.winId) && w.unseen).length ?? ids.length;
    const count = unseenNow();
    write({
      id: 'wins:seen',
      label: opts.undo ? 'Marked seen.' : 'Marked as seen.',
      afterCommit: opts.undo ? 'Marked seen.' : 'Marked as seen.',
      immediate: !opts.undo,
      silent: !opts.undo,
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

  /**
   * Why Not now must not start on this row, or null: a row he already reached (a request logged, on screen or still in
   * its window) stays on the list as done. An application never stops it (his ruling, 2026-10-26).
   */
  const passRefusal = (row: PlanRow): string | null => {
    if (isProvisional(row)) return `${row.company.name} is still coming in. Press Z to undo ${isPending(swapInId(-row.entryId)) ? 'the swap' : 'the Not now'} before it.`;
    if (row.steps.some((s) => s.kind === 'request' && s.at !== null)) return `You already reached ${row.company.name} today, so it stays on the list.`;
    return null;
  };

  /**
   * Not now (top picks, 2026-10-26): a cooldown, never a no-go. The pick leaves the rail and the bench's first company
   * slides into its place at once, with Undo for the window; nothing reaches the API until the window ends, so Undo
   * puts the day back exactly. The API picks the company that comes in from the same bench; if that turned out to be
   * another one, the toast says so once it lands. True when queued.
   */
  const passPick = (row: PlanRow, reason: PassReason | null, note?: string): boolean => {
    const id = passId(row);
    if (isPending(id)) return false;
    const blocked = passRefusal(row);
    if (blocked) return refuse(blocked);
    const latest = cachedRow(queryClient, row.entryId);
    if (cachedPlan(queryClient) && !latest) return refuse(`${row.company.name}'s row is no longer on today's plan.`);
    const bench = queryClient.getQueryData<BenchView>(queryKeys.bench);
    const taken = new Set((cachedPlan(queryClient)?.rows ?? []).map((r) => r.company.id));
    const next = bench?.companies.find((b) => !taken.has(b.company.id)) ?? null;
    const company = row.company.name;
    const label = next ? `Passed. Added ${next.company.name}.` : `Passed on ${company}.`;
    const dataError = reason === 'wrong_company' || reason === 'wrong_location';
    write({
      id,
      label,
      afterCommit: dataError ? `${label} Your flag is in Needs you.` : `${label} It comes back in 14 days, or sooner with a new opening.`,
      patches: [
        {
          key: queryKeys.plan,
          apply: (p: PlanView) => {
            const at = p.rows.findIndex((r) => r.entryId === row.entryId);
            if (at < 0) return p;
            const rows = [...p.rows];
            rows.splice(at, 1, ...(next ? [benchToPick(next, row.position)] : []));
            const numbered = rows.map((r, i) => (r.position === i + 1 ? r : { ...r, position: i + 1 }));
            return { ...p, rows: numbered, total: numbered.length, done: numbered.filter((r) => r.state === 'done').length, passed: p.passed + 1 };
          },
        },
        {
          key: queryKeys.bench,
          apply: (b: BenchView) => ({ ...b, companies: next ? b.companies.filter((c) => c.company.id !== next.company.id) : b.companies }),
        },
        shellPatch(),
      ],
      commit: (o) =>
        sendVersioned(queryClient, `plan:${row.entryId}`, row.revision, () => cachedRow(queryClient, row.entryId)?.revision, o, (revision) =>
          client.passPick(row.entryId, { reason, ...(note ? { note } : {}), ...(row.queue?.opening ? { postingId: row.queue.opening.postingId } : {}), idempotencyKey: `plan:${row.entryId}:pass` }, revision, o).then(
            (res) => {
              // The API chose from the same bench; when another company came in, say which.
              if ((res.added?.company.id ?? null) !== (next?.company.id ?? null)) toast(res.text);
              return { ...res, revision: revision + 1 };
            },
            (err: unknown) => {
              if (err instanceof ApiError && err.code === 'not_allowed') throw new ApiError('not_allowed', `Not passed. ${sentence(err.message)}`);
              throw err;
            },
          ),
        ),
    });
    return true;
  };

  /**
   * Why a swap must not start: the pick is still coming in, was reached, done or carried, or the company is held back
   * or already on the list. A company he said Not now to is not blocked here: the API says so with its dates.
   */
  const swapRefusal = (row: PlanRow, b: BrowseCompany): string | null => {
    if (isProvisional(row)) return `${row.company.name} is still coming in. Press Z to undo it before swapping it.`;
    if (row.state !== 'todo' || row.steps.some((s) => s.at !== null)) return `${row.company.name} is already worked or carried, so it stays on the list.`;
    if (b.blocked) return sentence(b.blocked);
    if (cachedPlan(queryClient)?.rows.some((r) => r.company.id === b.company.id)) return `${b.company.name} is already on today's list.`;
    return null;
  };

  /**
   * Swap (Browse companies): the chosen company takes the pick's slot and the pick goes back to More options. It is not
   * a Not now: no cooldown, no pass counted, and it uses no room. The rail shows the change at once; nothing reaches
   * the API until the window ends, so Undo puts the day back exactly. True when queued.
   */
  const swapPick = (row: PlanRow, b: BrowseCompany): boolean => {
    const id = swapInId(b.company.id);
    if (isPending(id) || isPending(passId(row))) return false;
    const refused = swapRefusal(row, b);
    if (refused) return refuse(`Not swapped. ${refused}`);
    const latest = cachedRow(queryClient, row.entryId);
    if (cachedPlan(queryClient) && !latest) return refuse(`Not swapped. ${row.company.name}'s row is no longer on today's plan.`);
    const label = `Swapped in ${b.company.name} for ${row.company.name}.`;
    write({
      id,
      label,
      afterCommit: `${label} ${row.company.name} is back on More options.`,
      patches: [
        {
          key: queryKeys.plan,
          apply: (p: PlanView) => {
            const at = p.rows.findIndex((r) => r.entryId === row.entryId);
            if (at < 0) return p;
            const rows = [...p.rows];
            rows.splice(at, 1, benchToPick(b, row.position));
            return { ...p, rows };
          },
        },
        {
          key: queryKeys.bench,
          apply: (v: BenchView) => ({ ...v, companies: v.companies.filter((c) => c.company.id !== b.company.id) }),
        },
        shellPatch(),
      ],
      commit: (o) =>
        sendVersioned(queryClient, `plan:${row.entryId}`, row.revision, () => cachedRow(queryClient, row.entryId)?.revision, o, (revision) =>
          client.swapPick(row.entryId, { companyId: b.company.id, idempotencyKey: `pick-swap:${row.entryId}:${b.company.id}` }, revision, o).then(
            (res) => {
              // The browse list names the pick that went back: read it again.
              void queryClient.invalidateQueries({ queryKey: queryKeys.browseAll });
              return { ...res, revision: revision + 1 };
            },
            (err: unknown) => {
              if (err instanceof ApiError && err.code === 'not_allowed') throw new ApiError('not_allowed', `Not swapped. ${sentence(err.message)}`);
              throw err;
            },
          ),
        ),
    });
    return true;
  };

  /** Add to today from the bench: the company joins after the last pick. True when queued. */
  const addFromBench = (companyId: Id, name: string): boolean => {
    const id = `bench-add:${companyId}`;
    if (isPending(id)) return false;
    const bench = queryClient.getQueryData<BenchView>(queryKeys.bench);
    const b = bench?.companies.find((c) => c.company.id === companyId);
    if (!b) return refuse(`Not added. ${name} is no longer on the bench.`);
    if (bench && bench.room <= 0) return refuse(`Not added. ${sentence(bench.roomText)}`);
    if (cachedPlan(queryClient)?.rows.some((r) => r.company.id === companyId)) return refuse(`${name} is already on today's list.`);
    const label = `Added ${name} to today.`;
    write({
      id,
      label,
      afterCommit: label,
      patches: [
        {
          key: queryKeys.plan,
          apply: (p: PlanView) => {
            const rows = [...p.rows, benchToPick(b, p.rows.length + 1)];
            return { ...p, rows, total: rows.length };
          },
        },
        {
          key: queryKeys.bench,
          apply: (v: BenchView) => ({ ...v, companies: v.companies.filter((c) => c.company.id !== companyId), room: Math.max(0, v.room - 1) }),
        },
        shellPatch((s) => ({ ...s, plan: { ...s.plan, total: s.plan.total + 1 } })),
      ],
      commit: (o) =>
        client.addFromBench(companyId, { idempotencyKey: `bench:${companyId}:add` }, o).catch((err: unknown) => {
          if (err instanceof ApiError && err.code === 'not_allowed') throw new ApiError('not_allowed', `Not added. ${sentence(err.message)}`);
          throw err;
        }),
    });
    return true;
  };

  /** True while the newest save of this note waits in its window or for its commit: its checks are unknown. */
  const noteSaving = (noteId: Id): boolean => {
    const id = lastSave.get(queryClient)?.get(noteId);
    return id !== undefined && isPending(id);
  };
  /** True while Confirm line for this note waits in its window or for its commit. */
  const confirming = (noteId: Id): boolean => isPending(`confirm:${noteId}`);

  return {
    logRequest, logApplied, logPeople, saveSearch, skip, passPick, passRefusal, addFromBench, swapPick, swapRefusal, saveNote, confirmReason, answer, seeWins, noteSaving, confirming,
  };
}
