import { useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { queryKeys, useClient, type CloseReason, type CompanyView, type Revision, type ShellSummary, type WriteOptions } from '@/api';
import { usePendingWrite, type Patch } from '@/pending/pending';
import { newestRevision, noteRevision, withRevision } from '@/pending/revisions';
import { CLOSE_WORD } from './company-status';

type Company = CompanyView['company'];

/** The second sentence of the close toast: where the company went. */
const TAIL: Record<CloseReason, string> = {
  referral: 'It is on the Done list.',
  interview: 'It is on the Done list.',
  no_sponsorship: 'It is on the No-go list.',
  no_referrals: 'Nothing more sends there.',
  already_hired: 'Nothing more sends there.',
  not_now: 'Nothing more sends there.',
  other: 'Nothing more sends there.',
};

/**
 * The company page's writes. Close waits 8 seconds with Undo. Pause commits at once (it is the safety action: a
 * pause that waited could let an email out), and so do resume and reopen (they ask first in a dialog, so they need
 * no Undo). Each patches the company, and names the lists whose counts move so they refetch when the write settles.
 * Every write reads the company's revision when it commits, not when it was asked for: another write on the same
 * company may land inside the window and move it. A write flushed as the tab closes cannot read; it sends with the
 * newest revision known: on screen when it was asked for, in the cache, or from a read.
 */
export function useCompanyActions(view: CompanyView | undefined) {
  const client = useClient();
  const queryClient = useQueryClient();
  const write = usePendingWrite();

  const patchCompany = useCallback(
    (edit: (c: Company, asOf: string) => Company): Patch<CompanyView> => ({
      key: queryKeys.company(view?.company.id ?? 0),
      apply: (v) => ({ ...v, company: edit(v.company, v.asOf) }),
    }),
    [view?.company.id],
  );
  /** Sends `send` with the company's revision: read when it commits, or the newest known when the tab is closing. */
  const withCompanyRevision = useCallback(
    <T,>(o: WriteOptions, c: Company, send: (revision: Revision) => Promise<T>): Promise<T> => {
      const key = `company:${c.id}`;
      const known = () => newestRevision(queryClient, key, c.revision, queryClient.getQueryData<CompanyView>(queryKeys.company(c.id))?.company.revision);
      const read = async () => {
        const { revision } = (await client.company(c.id)).company;
        noteRevision(queryClient, key, revision);
        return revision;
      };
      return withRevision(o, known, read, send);
    },
    [client, queryClient],
  );
  const refetch = (...keys: (readonly unknown[])[]): Patch[] => keys.map((key) => ({ key, apply: (d: unknown) => d }));

  const pause = useCallback(() => {
    if (!view) return;
    const { id, name } = view.company;
    const c = view.company;
    write({
      id: `pause:${id}`,
      label: `Paused ${name}. Nothing more sends there until you resume.`,
      afterCommit: `Paused ${name}.`,
      immediate: true,
      patches: [patchCompany((c, asOf) => ({ ...c, pausedAt: asOf })), ...refetch(queryKeys.shell, queryKeys.outbox, queryKeys.today)],
      commit: (o) => withCompanyRevision(o, c, (r) => client.pauseCompany(id, r, o)),
    });
  }, [view, write, client, patchCompany, withCompanyRevision]);

  const resume = useCallback(() => {
    if (!view) return;
    const { id, name } = view.company;
    const c = view.company;
    write({
      id: `resume:c${id}`,
      label: `Resumed ${name}.`,
      afterCommit: `Resumed ${name}.`,
      immediate: true,
      patches: [patchCompany((c) => ({ ...c, pausedAt: null, heldByTouchId: null, held: false, heldByReply: null })), ...refetch(queryKeys.shell, queryKeys.outbox, queryKeys.today)],
      // His pause first; a company held by a reply and not paused releases the hold (Resume shows again for it).
      commit: (o) => withCompanyRevision(o, c, (r) => (c.pausedAt === null ? client.releaseHold(id, r, o) : client.resumeCompany(id, r, o))),
    });
  }, [view, write, client, patchCompany, withCompanyRevision]);

  const close = useCallback(
    (reason: CloseReason, note: string) => {
      if (!view) return;
      const { id, name } = view.company;
      const c = view.company;
      const shell: Patch<ShellSummary> = {
        key: queryKeys.shell,
        apply: (s) => ({
          ...s,
          doneCount: s.doneCount + (reason === 'interview' || reason === 'referral' ? 1 : 0),
          noGoCount: s.noGoCount + (reason === 'no_sponsorship' ? 1 : 0),
        }),
      };
      write({
        id: `close:c${id}`,
        label: `Closed ${name}: ${CLOSE_WORD[reason]}. ${TAIL[reason]}`,
        afterCommit: `Closed ${name}: ${CLOSE_WORD[reason]}. Reopen it from the company page.`,
        patches: [
          patchCompany((c, asOf) => ({ ...c, status: 'closed', closed: { reason, note, at: asOf } })),
          shell,
          ...refetch(queryKeys.outbox, queryKeys.today, queryKeys.noGo, queryKeys.done),
        ],
        commit: (o) => withCompanyRevision(o, c, (r) => client.closeCompany(id, reason, note, r, o)),
      });
    },
    [view, write, client, patchCompany, withCompanyRevision],
  );

  const reopen = useCallback(() => {
    if (!view) return;
    const { id, name } = view.company;
    const c = view.company;
    write({
      id: `reopen:c${id}`,
      label: `Reopened ${name}.`,
      afterCommit: `Reopened ${name}.`,
      immediate: true,
      patches: [patchCompany((c) => ({ ...c, status: 'working', closed: null })), ...refetch(queryKeys.shell, queryKeys.outbox, queryKeys.today, queryKeys.noGo, queryKeys.done)],
      commit: (o) => withCompanyRevision(o, c, (r) => client.reopenCompany(id, r, o)),
    });
  }, [view, write, client, patchCompany, withCompanyRevision]);

  const copyRole = useCallback(() => {
    const title = view?.role?.title;
    if (!title) return;
    const failed = () => toast.error('Could not copy. Select the title and copy it by hand.');
    try {
      void navigator.clipboard.writeText(title).then(() => toast(`Copied "${title}"`), failed);
    } catch {
      failed();
    }
  }, [view?.role?.title]);

  return { pause, resume, close, reopen, copyRole };
}
