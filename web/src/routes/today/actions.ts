import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import {
  queryKeys,
  usePageClient,
  type DailyEntry,
  type Id,
  type RemoveReason,
  type Revision,
  type ShellSummary,
  type TodayView,
  type WriteOptions,
} from '@/api';
import { usePendingControls, usePendingWrite, type Patch } from '@/pending/pending';

type State = DailyEntry['state'];
/** What the server last said about one company's row: its revision and state. */
type Known = { revision: Revision; state: State };

export const findEntry = (t: TodayView | undefined, id: Id) => (t ? [...t.proposed, ...t.backlog].find((e) => e.company.id === id) : undefined);

/** An optimistic state change on one row, in place. Picked moves by one only when the row's state really changes. */
function statePatch(id: Id, state: State): Patch<TodayView> {
  return {
    key: queryKeys.today,
    apply: (t) => {
      const before = findEntry(t, id);
      if (!before || before.state === state) return t;
      const delta = (state === 'picked' ? 1 : 0) - (before.state === 'picked' ? 1 : 0);
      const set = (list: DailyEntry[]) => list.map((e) => (e.company.id === id ? { ...e, state } : e));
      return { ...t, picked: t.picked + delta, proposed: set(t.proposed), backlog: set(t.backlog) };
    },
  };
}

function shellPatch(change: (s: ShellSummary) => ShellSummary): Patch<ShellSummary> {
  return { key: queryKeys.shell, apply: change };
}

const pickedDelta = (wasPicked: boolean) => (wasPicked ? 1 : 0);

/**
 * The row as the last commit left it, per client. A commit returns the new revision at once, but the cached
 * Today view only catches up after its refetch, so a second write for the same company reads from here first.
 */
const committed = new WeakMap<QueryClient, Map<Id, Known>>();

function noteCommitted(queryClient: QueryClient, result: DailyEntry) {
  const map = committed.get(queryClient) ?? new Map<Id, Known>();
  map.set(result.company.id, { revision: result.revision, state: result.state });
  committed.set(queryClient, map);
}

/**
 * What the server holds for this company: the newer of the cached row (the cache is never overlaid) and the
 * last commit's result. `seen` is the row on screen, used only when the cache has no row for it.
 */
function knownOf(queryClient: QueryClient, seen: DailyEntry): Known {
  const id = seen.company.id;
  const cached = findEntry(queryClient.getQueryData<TodayView>(queryKeys.today), id) ?? seen;
  const last = committed.get(queryClient)?.get(id);
  return last && last.revision >= cached.revision ? last : { revision: cached.revision, state: cached.state };
}

const WRITES = ['pick', 'unpick', 'backlog'] as const;

/**
 * Pick, unpick, backlog and remove, each with its patches on Today and the sidebar counts.
 *
 * Writes for one company never wait side by side in the undo window: a new action first drops every pending
 * write for that company, then sends something only if the server's state differs from what he now wants.
 * So pick then unpick inside 8 s sends nothing, and the writes that do commit read their revision from the
 * last commit's result.
 */
export function useTodayActions() {
  const client = usePageClient('today');
  const write = usePendingWrite();
  const { cancel } = usePendingControls();
  const queryClient = useQueryClient();

  /** Drops this company's pending pick, unpick and backlog (not a remove: that one is already on its way). */
  const dropPending = (id: Id) => {
    for (const kind of WRITES) cancel(`${kind}:${id}`);
  };
  /** Runs a commit with the revision known when it starts, and records the revision it returns for the next write. */
  const send = (e: DailyEntry, call: (revision: Revision, o: WriteOptions) => Promise<DailyEntry>) => async (o: WriteOptions) => {
    const result = await call(knownOf(queryClient, e).revision, o);
    noteCommitted(queryClient, result);
    return result;
  };

  const pick = (e: DailyEntry) => {
    const { id, name } = e.company;
    dropPending(id);
    const server = knownOf(queryClient, e);
    if (server.state === 'picked') return;
    write({
      id: `pick:${id}`,
      label: `Picked ${name}`,
      afterCommit: `Picked ${name}. Press X to unpick.`,
      patches: [statePatch(id, 'picked'), shellPatch((s) => ({ ...s, pickedToday: s.pickedToday + 1 }))],
      commit: send(e, (r, o) => client.pick(id, r, o)),
    });
  };

  const unpick = (e: DailyEntry) => {
    const { id, name } = e.company;
    dropPending(id);
    const server = knownOf(queryClient, e);
    if (server.state !== 'picked') return;
    write({
      id: `unpick:${id}`,
      label: `Unpicked ${name}`,
      afterCommit: `Unpicked ${name}. Press X to pick it again.`,
      patches: [statePatch(id, 'proposed'), shellPatch((s) => ({ ...s, pickedToday: s.pickedToday - 1 }))],
      commit: send(e, (r, o) => client.unpick(id, r, o)),
    });
  };

  const backlog = (e: DailyEntry) => {
    const { id, name } = e.company;
    dropPending(id);
    const server = knownOf(queryClient, e);
    if (server.state === 'backlog') return;
    const wasPicked = server.state === 'picked';
    write({
      id: `backlog:${id}`,
      label: `Moved ${name} to the backlog`,
      afterCommit: `Moved ${name} to the backlog.`,
      patches: [statePatch(id, 'backlog'), ...(wasPicked ? [shellPatch((s) => ({ ...s, pickedToday: s.pickedToday - 1 }))] : [])],
      commit: send(e, (r, o) => client.backlog(id, r, o)),
    });
  };

  const remove = (e: DailyEntry, reason: RemoveReason, note: string) => {
    const { id, name } = e.company;
    dropPending(id);
    const wasPicked = knownOf(queryClient, e).state === 'picked';
    const off = (list: DailyEntry[]) => list.filter((x) => x.company.id !== id);
    write({
      id: `remove:${id}`,
      label: `Removed ${name}. Find it on the No-go list.`,
      afterCommit: `Removed ${name}. Find it on the No-go list.`,
      immediate: true,
      patches: [
        {
          key: queryKeys.today,
          apply: (t: TodayView) =>
            findEntry(t, id) ? { ...t, picked: t.picked - pickedDelta(wasPicked), proposed: off(t.proposed), backlog: off(t.backlog) } : t,
        },
        shellPatch((s) => ({ ...s, noGoCount: s.noGoCount + 1, pickedToday: s.pickedToday - pickedDelta(wasPicked) })),
      ],
      commit: (o) => client.removeCompany(id, reason, note, knownOf(queryClient, e).revision, o),
    });
  };

  return { pick, unpick, backlog, remove };
}
