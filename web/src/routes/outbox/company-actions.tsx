import { useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  queryKeys,
  useClient,
  type CloseReason,
  type CompanyRef,
  type CompanyView,
  type Id,
  type IsoDate,
  type IsoTime,
  type OutboxDetail,
  type OutboxItem,
  type OutboxView,
  type ShellSummary,
} from '@/api';
import { CloseCompanyDialog } from '@/components/close-company-dialog';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { formatDue } from '@/lib/format';
import { usePendingWrite, type Patch } from '@/pending/pending';
import { newestRevision, noteRevision, withRevision } from '@/pending/revisions';
import { closeCompanyRows, closedRow, fewerDrafts, isQueuedFor, patchCompany, patchDetailItem } from './outbox-patches';

const REASON_WORDS: Record<CloseReason, string> = {
  referral: 'referral',
  interview: 'interview',
  no_sponsorship: 'no sponsorship',
  no_referrals: 'no referrals',
  already_hired: 'already hired',
  not_now: 'not now',
  other: 'other',
};

/** What a held or paused email is, in words: "Lena Szabo (approved)", "Tom Becker (follow-up 1, due today)". */
export function heldLine(h: { personName: string; state: string; seq: number; dueOn: IsoDate | null }, asOf: IsoTime): string {
  const parts: string[] = [];
  if (h.seq > 0) parts.push(`follow-up ${h.seq}`);
  if (h.seq === 0 || h.state !== 'drafted') parts.push(h.state === 'drafted' ? 'draft' : h.state.replace(/_/g, ' '));
  if (h.dueOn) parts.push(formatDue(h.dueOn, asOf).replace(/^D/, 'd'));
  return `${h.personName} (${parts.join(', ')})`;
}

/** The pause, resume and close of one company, as the Outbox detail offers them. `dialogs` goes in the tree once. */
export function useCompanyActions({
  company,
  touchId,
  items,
  willResume,
}: {
  company: CompanyRef;
  touchId: Id;
  items: OutboxItem[];
  /** Lines for the resume dialog: what will send again. */
  willResume: string[];
}): { held: boolean; pause: () => void; askResume: () => void; askClose: () => void; dialogs: ReactNode } {
  const client = useClient();
  const queryClient = useQueryClient();
  const write = usePendingWrite();
  const [resuming, setResuming] = useState(false);
  const [closing, setClosing] = useState(false);

  // The Outbox rows do not carry the company's revision, so a write reads it when it commits. Every read is
  // noted: a close flushed as the tab closes cannot read, and sends with the newest one known.
  const revisionKey = `company:${company.id}`;
  const read = async () => {
    const { revision } = (await client.company(company.id)).company;
    noteRevision(queryClient, revisionKey, revision);
    return revision;
  };
  const known = () => newestRevision(queryClient, revisionKey, queryClient.getQueryData<CompanyView>(queryKeys.company(company.id))?.company.revision);
  /** Reads the revision ahead of a write that may have to go out without a read. A failure is fine: the write reads again. */
  const warm = () => void read().catch(() => undefined);
  /** A write moves the revision and returns nothing to say so: read the new one for the next write. */
  const thenWarm = <T,>(p: Promise<T>): Promise<T> =>
    p.then((r) => {
      warm();
      return r;
    });

  /** The company's new state, on every Outbox row of it and on the open detail. */
  const companyPatches = (change: (asOf: IsoTime) => Partial<CompanyRef>): Patch[] => {
    const list: Patch<OutboxView> = { key: queryKeys.outbox, apply: (v) => patchCompany(v, company.id, change(v.asOf)) };
    const detail: Patch<OutboxDetail> = {
      key: queryKeys.outboxDetail(touchId),
      apply: (d) => patchDetailItem(d, { company: { ...d.item.company, ...change(d.asOf) } }),
    };
    return [list, detail];
  };

  // Pausing and resuming commit at once: a pause that waited 8 seconds could let an email out.
  const pause = () =>
    write({
      id: `pause:${company.id}`,
      label: `Paused ${company.name}. Nothing goes to it until you resume.`,
      afterCommit: `Paused ${company.name}.`,
      immediate: true,
      patches: companyPatches((asOf) => ({ pausedAt: asOf })),
      commit: (o) => thenWarm(withRevision(o, known, read, (r) => client.pauseCompany(company.id, r, o))),
    });

  const resume = () =>
    write({
      id: `resume:${company.id}`,
      label: `Resumed ${company.name}.`,
      afterCommit: `Resumed ${company.name}.`,
      immediate: true,
      patches: companyPatches(() => ({ pausedAt: null, heldByTouchId: null, held: false, heldByReply: null })),
      // His pause first; a company held by a reply and not paused releases the hold.
      commit: (o) => thenWarm(withRevision(o, known, read, (r) => (company.pausedAt === null ? client.releaseHold(company.id, r, o) : client.resumeCompany(company.id, r, o)))),
    });

  const close = (reason: CloseReason, note: string) => {
    const summary = `Company closed: ${REASON_WORDS[reason]}`;
    const queuedDrafts = items.filter((i) => isQueuedFor(i, company.id) && i.state === 'drafted').length;
    const list: Patch<OutboxView> = { key: queryKeys.outbox, apply: (v) => closeCompanyRows(v, company.id, summary) };
    const detail: Patch<OutboxDetail> = {
      key: queryKeys.outboxDetail(touchId),
      apply: (d) => {
        const shut = { ...d.item.company, status: 'closed' as const };
        return patchDetailItem(d, isQueuedFor(d.item, company.id) ? { ...closedRow(d.item, summary), company: shut } : { company: shut });
      },
    };
    const shell: Patch<ShellSummary> = { key: queryKeys.shell, apply: (s) => fewerDrafts(s, queuedDrafts) };
    // Closing is an undoable 8-second write for every reason (design section 7, "Z for 8 seconds").
    warm();
    write({
      id: `close:${company.id}`,
      label: `Closed ${company.name}: ${REASON_WORDS[reason]}. Its queued emails stop.`,
      afterCommit: `Closed ${company.name}.`,
      patches: [list, detail, shell],
      commit: (o) => withRevision(o, known, read, (r) => client.closeCompany(company.id, reason, note, r, o)),
    });
  };

  const dialogs = (
    <>
      <CloseCompanyDialog company={company} open={closing} onOpenChange={setClosing} onClose={close} />
      <AlertDialog open={resuming} onOpenChange={setResuming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{`Resume ${company.name}?`}</AlertDialogTitle>
            <AlertDialogDescription>
              {willResume.length > 0 ? 'These go back to the sender\'s queue on their normal schedule:' : 'Nothing is queued here. New drafts will go out as usual.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {willResume.length > 0 ? (
            <ul className="grid gap-1 rounded-md border bg-subtle px-3 py-2 text-sm">
              {willResume.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          ) : null}
          <AlertDialogFooter>
            <AlertDialogCancel>{company.held || company.heldByTouchId !== null ? 'Keep on hold' : 'Keep paused'}</AlertDialogCancel>
            <AlertDialogAction onClick={resume}>{`Resume ${company.name}`}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );

  return {
    held: company.pausedAt !== null || company.held || company.heldByTouchId !== null,
    pause,
    askResume: () => setResuming(true),
    askClose: () => {
      warm();
      setClosing(true);
    },
    dialogs,
  };
}
