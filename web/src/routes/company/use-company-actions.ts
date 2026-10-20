import { useCallback } from 'react';
import { toast } from 'sonner';
import { queryKeys, useClient, type CloseReason, type CompanyView, type ShellSummary } from '@/api';
import { usePendingWrite, type Patch } from '@/pending/pending';
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
 * company may land inside the window and move it.
 */
export function useCompanyActions(view: CompanyView | undefined) {
  const client = useClient();
  const write = usePendingWrite();

  const patchCompany = useCallback(
    (edit: (c: Company, asOf: string) => Company): Patch<CompanyView> => ({
      key: queryKeys.company(view?.company.id ?? 0),
      apply: (v) => ({ ...v, company: edit(v.company, v.asOf) }),
    }),
    [view?.company.id],
  );
  /** The revision now, read when a write commits. */
  const revisionOf = useCallback(async (id: number) => (await client.company(id)).company.revision, [client]);
  const refetch = (...keys: (readonly unknown[])[]): Patch[] => keys.map((key) => ({ key, apply: (d: unknown) => d }));

  const pause = useCallback(() => {
    if (!view) return;
    const { id, name } = view.company;
    write({
      id: `pause:${id}`,
      label: `Paused ${name}. Nothing more sends there until you resume.`,
      afterCommit: `Paused ${name}.`,
      immediate: true,
      patches: [patchCompany((c, asOf) => ({ ...c, pausedAt: asOf })), ...refetch(queryKeys.shell, queryKeys.outbox, queryKeys.today)],
      commit: async (o) => client.pauseCompany(id, await revisionOf(id), o),
    });
  }, [view, write, client, patchCompany, revisionOf]);

  const resume = useCallback(() => {
    if (!view) return;
    const { id, name } = view.company;
    write({
      id: `resume:c${id}`,
      label: `Resumed ${name}.`,
      afterCommit: `Resumed ${name}.`,
      immediate: true,
      patches: [patchCompany((c) => ({ ...c, pausedAt: null, heldByTouchId: null })), ...refetch(queryKeys.shell, queryKeys.outbox, queryKeys.today)],
      commit: async (o) => client.resumeCompany(id, await revisionOf(id), o),
    });
  }, [view, write, client, patchCompany, revisionOf]);

  const close = useCallback(
    (reason: CloseReason, note: string) => {
      if (!view) return;
      const { id, name } = view.company;
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
        commit: async (o) => client.closeCompany(id, reason, note, await revisionOf(id), o),
      });
    },
    [view, write, client, patchCompany, revisionOf],
  );

  const reopen = useCallback(() => {
    if (!view) return;
    const { id, name } = view.company;
    write({
      id: `reopen:c${id}`,
      label: `Reopened ${name}.`,
      afterCommit: `Reopened ${name}.`,
      immediate: true,
      patches: [patchCompany((c) => ({ ...c, status: 'working', closed: null })), ...refetch(queryKeys.shell, queryKeys.outbox, queryKeys.today, queryKeys.noGo, queryKeys.done)],
      commit: async (o) => client.reopenCompany(id, await revisionOf(id), o),
    });
  }, [view, write, client, patchCompany, revisionOf]);

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
