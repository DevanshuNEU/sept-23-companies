import type { CloseReason, CompanyView } from '@/api';
import { companyStatus, type Status } from '@/lib/status';

type Company = CompanyView['company'];

/** The words of a close reason, as the dialog lists them. */
export const CLOSE_WORD: Record<CloseReason, string> = {
  referral: 'referral',
  interview: 'interview',
  no_sponsorship: 'no sponsorship',
  no_referrals: 'no referrals',
  already_hired: 'already hired',
  not_now: 'not now',
  other: 'other',
};

const isClosed = (c: Company) => c.status === 'closed';

/** What he can do here now. Pause and resume are separate: a held company can be paused, and resumed to release its emails. */
export function companyActions(c: Company) {
  const open = c.status === 'working';
  return {
    pause: open && c.pausedAt === null,
    resume: open && (c.pausedAt !== null || c.held || c.heldByTouchId !== null),
    close: c.status === 'working' || c.status === 'backlog' || c.status === 'candidate',
    reopen: isClosed(c),
  };
}

/**
 * The badges of the header, in the spec's words. Closed wins over everything. Otherwise a reply hold and a pause
 * both show, so a held company he pauses reads "Held: reply" and "Paused".
 */
export function headerStatuses(c: Company): Status[] {
  if (isClosed(c)) {
    const reason = c.closed?.reason;
    if (reason === 'interview') return [{ label: 'Done', tone: 'ok' }, { label: 'Interviewing', tone: 'ok' }];
    if (reason === 'referral') return [{ label: 'Done', tone: 'ok' }, { label: 'Referral', tone: 'ok' }];
    if (reason === 'no_sponsorship') return [{ label: 'No-go', tone: 'bad' }];
    return [{ label: reason ? `Closed: ${CLOSE_WORD[reason]}` : 'Closed', tone: 'idle' }];
  }
  const out: Status[] = [];
  if (c.held || c.heldByTouchId !== null) out.push(companyStatus({ ...c, pausedAt: null }));
  if (c.pausedAt !== null) out.push(companyStatus({ ...c, heldByTouchId: null, held: false, heldByReply: null }));
  if (out.length === 0) out.push(companyStatus(c));
  if (c.day !== null && c.status === 'working') out.push({ label: `Day ${c.day} of 3`, tone: 'idle' });
  return out;
}
