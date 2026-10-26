import type { CompanyRef } from '@/api';
import {
  channelLabel,
  companyStatus,
  jobKindLabel,
  jobStatus,
  laneLabel,
  linkedinStepStatus,
  messageStatus,
  noGoReasonStatus,
  readResultStatus,
  replyKindStatus,
  reviewKindStatus,
  sponsorshipStatus,
  touchStatus,
  watchKindLabel,
  hideKindLabel,
  winKindLabel,
} from '@/lib/status';

const company = (over: Partial<CompanyRef> = {}): CompanyRef => ({
  id: 1,
  name: 'Graphite',
  status: 'working',
  pausedAt: null,
  heldByTouchId: null,
  held: false,
  heldByReply: null,
  ...over,
});

describe('status vocabulary', () => {
  it('status words match the spec', () => {
    expect(messageStatus.send_unknown).toEqual({ label: 'Checking', tone: 'info', icon: 'clock' });
    expect(replyKindStatus.asked_to_stop).toMatchObject({ label: 'Asked to stop', tone: 'bad' });
    expect(messageStatus.approved).toMatchObject({ label: 'Approved', tone: 'info' });
    expect(messageStatus.failed).toMatchObject({ label: 'Failed', tone: 'bad' });
    expect(messageStatus.drafted).toMatchObject({ label: 'Draft', tone: 'idle' });
    expect(touchStatus.open).toMatchObject({ label: 'Waiting', tone: 'idle' });
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

  it('names every job and its last result', () => {
    expect(jobKindLabel).toEqual({
      install: 'Install', rebuild: 'Rebuild', crawl: 'Board read', daily_list: 'Daily list', sender: 'Sender', import: 'Import',
      discover: 'Discover', resolve: 'Resolve', find_boards: 'Find boards', read_boards: 'Read boards',
      watch_pages: 'Watch pages', read_postings: 'Read postings', rank: 'Rank', plan: 'Plan', prepare: 'Prepare',
    });
    expect(jobStatus.running).toMatchObject({ label: 'Running', tone: 'info' });
    expect(jobStatus.ok).toMatchObject({ label: 'Finished', tone: 'idle' });
    expect(jobStatus.partial).toMatchObject({ label: 'Partial', tone: 'warn' });
    expect(jobStatus.failed).toMatchObject({ label: 'Failed', tone: 'bad' });
    expect(jobStatus.late).toMatchObject({ label: 'Late', tone: 'warn' });
  });

  it('gives every status a label and a tone', () => {
    const maps = [
      messageStatus, touchStatus, replyKindStatus, sponsorshipStatus,
      reviewKindStatus, noGoReasonStatus, readResultStatus, linkedinStepStatus, jobStatus,
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
      reviewKindStatus, noGoReasonStatus, readResultStatus, linkedinStepStatus, jobStatus, jobKindLabel,
    ]);
    expect(all).not.toMatch(new RegExp(`[${String.fromCharCode(0x2013)}${String.fromCharCode(0x2014)}]`));
  });
});

describe('companyStatus', () => {
  it('shows Held: reply in green when a touch holds the company, over everything else', () => {
    const s = companyStatus(company({ heldByTouchId: 7, pausedAt: '2026-11-09T12:00:00Z' }));
    expect(s).toMatchObject({ label: 'Held: reply', tone: 'ok' });
  });
  it('shows Held: reply for a reply on another channel, with no email touch holding it', () => {
    const reply = { channel: 'linkedin_message', person: null, at: '2026-11-09T12:00:00Z' } as const;
    expect(companyStatus(company({ held: true, heldByReply: reply }))).toMatchObject({ label: 'Held: reply', tone: 'ok' });
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

describe('plan words', () => {
  it('names every channel, lane, win and watch kind', () => {
    expect(channelLabel).toEqual({
      linkedin_request: 'LinkedIn request', linkedin_message: 'LinkedIn message', email: 'Email',
      application: 'Application', hn_reply: 'HN reply', other: 'Other',
    });
    expect(laneLabel).toEqual({
      level_role: 'Role at your level', intern_junior: 'Intern or junior work',
      reason_no_role: 'Reason to reach out', any_us_role: 'Open US role',
    });
    expect(winKindLabel).toEqual({ accepted: 'Accepted', replied: 'Replied', referred: 'Referral', interview: 'Interview', offer: 'Offer' });
    expect(watchKindLabel).toEqual({
      careers_changed: 'Careers page changed', new_reason_no_role: 'New company to reach', merge_candidate: 'Possible duplicate',
    });
    expect(hideKindLabel).toEqual({ no_go: 'No-go', over_size: 'Over the size limit', no_sponsorship: 'No sponsorship' });
  });
  it('gives every word a label and writes no dash', () => {
    const dash = new RegExp(`[${String.fromCharCode(0x2013)}${String.fromCharCode(0x2014)}]`);
    for (const map of [channelLabel, laneLabel, winKindLabel, watchKindLabel, hideKindLabel]) {
      for (const label of Object.values(map)) {
        expect(label.length).toBeGreaterThan(0);
        expect(label).not.toMatch(dash);
      }
    }
  });
});
