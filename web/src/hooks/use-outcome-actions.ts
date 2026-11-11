import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import {
  queryKeys,
  usePageClient,
  type Id,
  type OutcomeKind,
  type OutcomePromptView,
  type PlanView,
  type PromptTouch,
  type ReplyKind,
  type Revision,
  type ShellSummary,
  type WinsView,
  type WriteOptions,
} from '@/api';
import { replyKindStatus } from '@/lib/status';
import { newestRevision, noteRevision } from '@/pending/revisions';
import { usePendingWrite, type Patch } from '@/pending/pending';

type Reply = Exclude<ReplyKind, 'unsorted' | 'auto_reply'>;

/** Said no and asked to stop add a no-go, so they commit at once with no Undo (phase 1 lead decision 2). */
const AT_ONCE = new Set<Reply>(['said_no', 'asked_to_stop']);

const ADD = (company: string) => `To change it, open ${company}'s page.`;

/* ------------------------------------------------------------------ revisions */

/**
 * Every answer on a touch carries the revision the last write on the same touch returned: the API checks it and
 * moves it on by exactly one. The cached prompt only catches up after its refetch, so each commit records the
 * revision it gets back (`noteRevision`) and the next write takes the newest of that, the cache and the touch on screen.
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

/* ------------------------------------------------------------------ patches */

const shellPatch = (change: (s: ShellSummary) => ShellSummary = (s) => s): Patch<ShellSummary> => ({ key: queryKeys.shell, apply: change });

/** Nothing is laid over the view: the key is named so the view refetches once the commit has landed. */
const refetchOnly = <T,>(key: readonly unknown[]): Patch<T> => ({ key, apply: (data) => data });

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
 * The answers to the 7-day question and marking wins seen (#33), each one a pending write with its patches, so the
 * prompt, the wins and the sidebar move at once and Undo (or `z`) takes the write back inside the 8 second window.
 *
 * The UI never decides what counts as a win or which recognition to show: an answer lays nothing over the wins
 * view. It names the wins and shell keys so both refetch the moment the commit lands, and an undone answer never
 * appears. `page` names the view whose client the writes go through: the plan's by default, and the win log passes `wins`.
 */
export function useOutcomeActions(page: Parameters<typeof usePageClient>[0] = 'plan') {
  const client = usePageClient(page);
  const write = usePendingWrite();
  const queryClient = useQueryClient();

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
   * it back inside the window (the W key and the Mark seen button on the win strip).
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

  return { answer, seeWins };
}
