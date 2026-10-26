import type {
  Channel,
  CompanyRef,
  HideKind,
  JobKind,
  JobRunSummary,
  ListGroup,
  LinkedInStep,
  MessageState,
  NoGoReason,
  ReadResult,
  ReplyKind,
  ReviewKind,
  SponsorshipShown,
  TouchState,
  WatchKind,
  WinKind,
} from '@/api';

export type Tone = 'ok' | 'info' | 'warn' | 'bad' | 'idle';
export type Status = { label: string; tone: Tone; icon?: 'block' | 'warn' | 'check' | 'reply' | 'pause' | 'clock' };

// One word per state, the same everywhere (spec section 8). Each map is checked against its union,
// so a new value in the API types fails typecheck until it gets a word here.

export const messageStatus = {
  planned: { label: 'Planned', tone: 'idle' },
  drafted: { label: 'Draft', tone: 'idle' },
  approved: { label: 'Approved', tone: 'info', icon: 'check' },
  sending: { label: 'Sending', tone: 'info', icon: 'clock' },
  send_unknown: { label: 'Checking', tone: 'info', icon: 'clock' },
  sent: { label: 'Sent', tone: 'idle', icon: 'check' },
  failed: { label: 'Failed', tone: 'bad', icon: 'warn' },
  skipped: { label: 'Skipped', tone: 'idle' },
} satisfies Record<MessageState, Status>;

export const touchStatus = {
  open: { label: 'Waiting', tone: 'idle' },
  replied: { label: 'Replied', tone: 'ok', icon: 'reply' },
  bounced: { label: 'Bounced', tone: 'bad', icon: 'warn' },
  closed: { label: 'Closed', tone: 'idle' },
} satisfies Record<TouchState, Status>;

export const replyKindStatus = {
  unsorted: { label: 'Unsorted', tone: 'warn' },
  interested: { label: 'Interested', tone: 'ok' },
  not_now: { label: 'Not now', tone: 'idle' },
  said_no: { label: 'Said no', tone: 'bad' },
  asked_to_stop: { label: 'Asked to stop', tone: 'bad', icon: 'block' },
  auto_reply: { label: 'Auto reply', tone: 'idle' },
} satisfies Record<ReplyKind, Status>;

export const sponsorshipStatus = {
  has_sponsored: { label: 'Has sponsored', tone: 'ok' },
  says_yes: { label: 'Says it sponsors', tone: 'ok' },
  says_no: { label: 'Does not sponsor', tone: 'bad' },
  opt_only: { label: 'OPT only', tone: 'warn' },
  unclear: { label: 'Unclear', tone: 'warn' },
  not_stated: { label: 'Not stated', tone: 'idle' },
} satisfies Record<SponsorshipShown, Status>;

export const reviewKindStatus = {
  board_unreadable: { label: 'Error', tone: 'bad' },
  board_empty: { label: 'Empty', tone: 'warn' },
  sponsorship_unclear: { label: 'Unclear', tone: 'warn' },
  size_unknown: { label: 'Unknown', tone: 'warn' },
  size_guess: { label: 'Guess', tone: 'warn' },
  domain_missing: { label: 'No domain', tone: 'warn' },
  flagged: { label: 'Flagged', tone: 'warn' },
  merge_candidate: { label: 'Duplicate?', tone: 'warn' },
  reader_disagrees: { label: 'Reader disagrees', tone: 'warn' },
  careers_changed: { label: 'Page changed', tone: 'info' },
  size_check: { label: 'Check size', tone: 'warn' },
} satisfies Record<ReviewKind, Status>;

export const noGoReasonStatus = {
  no_sponsorship: { label: 'Does not sponsor', tone: 'bad' },
  citizenship: { label: 'Citizenship or clearance', tone: 'bad' },
  over_300: { label: 'Over 300 people', tone: 'idle' },
  defence_contractor: { label: 'Defence contractor', tone: 'idle' },
  agency: { label: 'Agency', tone: 'idle' },
  said_no: { label: 'Said no', tone: 'bad' },
  stop_reply: { label: 'Asked to stop', tone: 'bad', icon: 'block' },
  not_a_fit: { label: 'Not a fit', tone: 'idle' },
  other: { label: 'Other', tone: 'idle' },
} satisfies Record<NoGoReason, Status>;

export const readResultStatus = {
  ok: { label: 'ok', tone: 'ok', icon: 'check' },
  empty: { label: 'Empty', tone: 'warn' },
  error: { label: 'Error', tone: 'bad', icon: 'warn' },
} satisfies Record<ReadResult, Status>;

export const linkedinStepStatus = {
  request_sent: { label: 'Request sent', tone: 'idle' },
  accepted: { label: 'Accepted', tone: 'info' },
  message_sent: { label: 'Message sent', tone: 'idle' },
  replied: { label: 'Replied', tone: 'ok', icon: 'reply' },
} satisfies Record<LinkedInStep, Status>;

export const jobKindLabel = {
  install: 'Install',
  rebuild: 'Rebuild',
  crawl: 'Board read',
  daily_list: 'Daily list',
  sender: 'Sender',
  import: 'Import',
  discover: 'Discover',
  resolve: 'Resolve',
  find_boards: 'Find boards',
  read_boards: 'Read boards',
  watch_pages: 'Watch pages',
  read_postings: 'Read postings',
  rank: 'Rank',
  plan: 'Plan',
  prepare: 'Prepare',
} satisfies Record<JobKind, string>;

/** The last run of a job. `late` is derived by the API (`expectedNextAt` passed with no new run). */
export const jobStatus = {
  running: { label: 'Running', tone: 'info', icon: 'clock' },
  ok: { label: 'Finished', tone: 'idle', icon: 'check' },
  partial: { label: 'Partial', tone: 'warn', icon: 'warn' },
  failed: { label: 'Failed', tone: 'bad', icon: 'warn' },
  late: { label: 'Late', tone: 'warn', icon: 'clock' },
} satisfies Record<JobRunSummary['status'] | 'late', Status>;

const byCompanyStatus = {
  candidate: { label: 'Proposed', tone: 'idle' },
  backlog: { label: 'Backlog', tone: 'idle' },
  working: { label: 'Emailing', tone: 'info' },
  closed: { label: 'Closed', tone: 'idle' },
  removed: { label: 'Removed', tone: 'idle' },
} satisfies Record<CompanyRef['status'], Status>;

/**
 * A held company shows Held: reply over anything else (a reply on any channel holds it, with or without an email
 * touch); a paused one shows Paused; else the stored status.
 */
export function companyStatus(c: CompanyRef): Status {
  if (c.held || c.heldByTouchId !== null) return { label: 'Held: reply', tone: 'ok', icon: 'reply' };
  if (c.pausedAt !== null) return { label: 'Paused', tone: 'idle', icon: 'pause' };
  return byCompanyStatus[c.status];
}

export const channelLabel = {
  linkedin_request: 'LinkedIn request',
  linkedin_message: 'LinkedIn message',
  email: 'Email',
  application: 'Application',
  hn_reply: 'HN reply',
  other: 'Other',
} satisfies Record<Channel, string>;

export const laneLabel = {
  level_role: 'Role at your level',
  intern_junior: 'Intern or junior work',
  reason_no_role: 'Reason to reach out',
  any_us_role: 'Open US role',
} satisfies Record<ListGroup, string>;

export const winKindLabel = {
  accepted: 'Accepted',
  replied: 'Replied',
  referred: 'Referral',
  interview: 'Interview',
  offer: 'Offer',
} satisfies Record<WinKind, string>;

/** Why the ranker hides a company, in the words the No-go view uses. */
export const hideKindLabel = {
  no_go: 'No-go',
  over_size: 'Over the size limit',
  no_sponsorship: 'No sponsorship',
} satisfies Record<HideKind, string>;

export const watchKindLabel = {
  careers_changed: 'Careers page changed',
  new_reason_no_role: 'New company to reach',
  merge_candidate: 'Possible duplicate',
} satisfies Record<WatchKind, string>;
