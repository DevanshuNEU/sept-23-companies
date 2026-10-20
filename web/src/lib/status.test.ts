import type { CompanyRef } from '@/api';
import {
  companyStatus,
  linkedinStepStatus,
  messageStatus,
  noGoReasonStatus,
  readResultStatus,
  replyKindStatus,
  reviewKindStatus,
  sponsorshipStatus,
  touchStatus,
} from '@/lib/status';

const company = (over: Partial<CompanyRef> = {}): CompanyRef => ({
  id: 1,
  name: 'Graphite',
  status: 'working',
  pausedAt: null,
  heldByTouchId: null,
  ...over,
});

describe('status vocabulary', () => {
  it('status words match the spec', () => {
    expect(messageStatus.send_unknown).toEqual({ label: 'Checking', tone: 'info', icon: 'clock' });
    expect(replyKindStatus.asked_to_stop).toMatchObject({ label: 'Asked to stop', tone: 'bad' });
    expect(messageStatus.approved).toMatchObject({ label: 'Approved', tone: 'info' });
    expect(messageStatus.failed).toMatchObject({ label: 'Failed', tone: 'bad' });
    expect(messageStatus.drafted).toMatchObject({ label: 'Draft', tone: 'idle' });
    expect(touchStatus.replied).toMatchObject({ label: 'Replied', tone: 'ok' });
    expect(touchStatus.bounced).toMatchObject({ label: 'Bounced', tone: 'bad' });
    expect(sponsorshipStatus.says_no).toMatchObject({ label: 'Does not sponsor', tone: 'bad' });
    expect(sponsorshipStatus.has_sponsored).toMatchObject({ label: 'Has sponsored', tone: 'ok' });
    expect(sponsorshipStatus.not_stated).toMatchObject({ label: 'Not stated', tone: 'idle' });
    expect(noGoReasonStatus.citizenship).toMatchObject({ label: 'Citizenship or clearance', tone: 'bad' });
    expect(noGoReasonStatus.over_300).toMatchObject({ label: 'Over 300 people', tone: 'idle' });
    expect(reviewKindStatus.size_guess).toMatchObject({ label: 'Guess', tone: 'warn' });
    expect(readResultStatus.ok).toMatchObject({ label: 'ok', tone: 'ok' });
    expect(readResultStatus.empty).toMatchObject({ label: 'Empty', tone: 'warn' });
    expect(readResultStatus.error).toMatchObject({ label: 'Error', tone: 'bad' });
    expect(linkedinStepStatus.replied).toMatchObject({ tone: 'ok' });
  });

  it('gives every status a label and a tone', () => {
    const maps = [
      messageStatus, touchStatus, replyKindStatus, sponsorshipStatus,
      reviewKindStatus, noGoReasonStatus, readResultStatus, linkedinStepStatus,
    ];
    for (const map of maps) {
      for (const s of Object.values(map)) {
        expect(s.label.length).toBeGreaterThan(0);
        expect(['ok', 'info', 'warn', 'bad', 'idle']).toContain(s.tone);
      }
    }
  });

  it('writes no em or en dashes', () => {
    const all = JSON.stringify([
      messageStatus, touchStatus, replyKindStatus, sponsorshipStatus,
      reviewKindStatus, noGoReasonStatus, readResultStatus, linkedinStepStatus,
    ]);
    expect(all).not.toMatch(new RegExp(`[${String.fromCharCode(0x2013)}${String.fromCharCode(0x2014)}]`));
  });
});

describe('companyStatus', () => {
  it('shows Held: reply in green when a touch holds the company, over everything else', () => {
    const s = companyStatus(company({ heldByTouchId: 7, pausedAt: '2026-11-09T12:00:00Z' }));
    expect(s).toMatchObject({ label: 'Held: reply', tone: 'ok' });
  });
  it('shows Paused in grey when he paused it', () => {
    expect(companyStatus(company({ pausedAt: '2026-11-09T12:00:00Z' }))).toMatchObject({ label: 'Paused', tone: 'idle' });
  });
  it('otherwise follows the stored status', () => {
    expect(companyStatus(company({ status: 'working' }))).toMatchObject({ label: 'Emailing', tone: 'info' });
    expect(companyStatus(company({ status: 'backlog' }))).toMatchObject({ label: 'Backlog', tone: 'idle' });
    expect(companyStatus(company({ status: 'candidate' }))).toMatchObject({ label: 'Proposed', tone: 'idle' });
    expect(companyStatus(company({ status: 'closed' }))).toMatchObject({ label: 'Closed', tone: 'idle' });
    expect(companyStatus(company({ status: 'removed' }))).toMatchObject({ label: 'Removed', tone: 'idle' });
  });
});
