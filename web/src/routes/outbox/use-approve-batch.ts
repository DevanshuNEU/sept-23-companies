import { useQueryClient } from '@tanstack/react-query';
import { ApiError, newRequestId, queryKeys, usePageClient, type OutboxDetail, type OutboxItem, type OutboxView, type ShellSummary } from '@/api';
import { usePendingWrite, type Patch, type PendingWrite } from '@/pending/pending';
import { noteRevision } from '@/pending/revisions';
import { approvedText, refusedSummary } from './approve-batch';
import { approveRows, fewerDrafts, patchDetailItem } from './outbox-patches';

/**
 * Approving a batch of drafts. Only here and in the Outbox detail is `client.approve` called, and only after his
 * click in the batch dialog. One undoable write holds the whole batch: Undo or Z inside the window drops all of it.
 * When the window ends each draft goes through the same approve route as a single one, at the revision the dialog
 * showed, so the server runs the #9 checks on every draft and refuses any that changed.
 */
export function useApproveBatch(): (drafts: OutboxItem[]) => void {
  const client = usePageClient('outbox');
  const queryClient = useQueryClient();
  const write = usePendingWrite();

  const build = (drafts: OutboxItem[]): PendingWrite => {
    const ids = new Set(drafts.map((d) => d.touchId));
    const text = approvedText(drafts.length);
    const approved = { state: 'approved', group: 'approved' } as const;
    const list: Patch<OutboxView> = { key: queryKeys.outbox, apply: (v) => approveRows(v, ids) };
    const shell: Patch<ShellSummary> = { key: queryKeys.shell, apply: (s) => fewerDrafts(s, drafts.length) };
    const details = drafts.map((d): Patch<OutboxDetail> => ({ key: queryKeys.outboxDetail(d.touchId), apply: (x) => patchDetailItem(x, approved) }));
    // The names a single approve uses, so Skip and Withdraw on one of these drafts take just it out of the batch.
    const member = (d: OutboxItem) => `approve:${d.messageId}`;

    return {
      id: `approve-batch:${drafts.map((d) => d.messageId).join(',')}`,
      label: text,
      afterCommit: text,
      patches: [list, shell, ...details],
      members: {
        ids: drafts.map(member),
        without: (id) => {
          const rest = drafts.filter((d) => member(d) !== id);
          return rest.length > 0 ? build(rest) : null;
        },
      },
      commit: async (o) => {
        const one = async (d: OutboxItem) => {
          const done = await client.approve(d.messageId, d.revision, { requestId: newRequestId(), keepalive: o.keepalive });
          noteRevision(queryClient, `message:${d.messageId}`, done.item.revision);
        };
        const refused: { name: string; message: string; code: ApiError['code'] }[] = [];
        const settle = async (d: OutboxItem) => {
          try {
            await one(d);
          } catch (err) {
            refused.push({
              name: d.person.name,
              message: err instanceof Error ? err.message : 'It did not go through.',
              code: err instanceof ApiError ? err.code : 'network',
            });
          }
        };
        // A closing page cannot wait on one request before it sends the next, so it starts them all.
        if (o.keepalive) await Promise.all(drafts.map(settle));
        else for (const d of drafts) await settle(d);
        const first = refused[0];
        if (first) throw new ApiError(first.code, refusedSummary(drafts.length - refused.length, drafts.length, refused));
      },
    };
  };

  return (drafts) => {
    if (drafts.length > 0) write(build(drafts));
  };
}
