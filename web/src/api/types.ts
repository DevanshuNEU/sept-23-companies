export type Id = number;
export type IsoTime = string;   // UTC, "2026-11-10T15:14:00Z"
export type IsoDate = string;   // a calendar date, "2026-11-10"; never pass it to new Date()
export type Revision = number;

export type CompanyStatus = 'candidate' | 'backlog' | 'working' | 'closed' | 'removed';
export type CloseReason = 'referral' | 'interview' | 'no_sponsorship' | 'no_referrals' | 'already_hired' | 'not_now' | 'other';
export type RemoveReason = 'not_a_fit' | 'already_in_touch' | 'bigger_than_it_looks' | 'other'; // stored as company.remove_reason text
export type RoleKind = 'engineer' | 'manager' | 'founder' | 'recruiter' | 'alum' | 'other';
export type LevelTier = 'new_grad' | 'early_career' | 'swe1' | 'swe2' | 'open' | 'not_stated' | 'senior';
export type Confidence = 'confirmed' | 'estimate' | 'guess';
export type FactKind = 'size' | 'stage' | 'sponsorship' | 'domain' | 'hq' | 'everify';
export type SourceKind = 'hand' | 'posting' | 'old_dashboard' | 'yc' | 'builtin' | 'linkedin' | 'website' | 'formd' | 'lca' | 'everify' | 'other';
export type SponsorshipShown = 'has_sponsored' | 'says_yes' | 'says_no' | 'opt_only' | 'unclear' | 'not_stated'; // derived
export type MessageState = 'planned' | 'drafted' | 'approved' | 'sending' | 'send_unknown' | 'sent' | 'failed' | 'skipped';
export type TouchState = 'open' | 'replied' | 'bounced' | 'closed';
export type ReplyKind = 'unsorted' | 'interested' | 'not_now' | 'said_no' | 'asked_to_stop' | 'auto_reply';
export type OutboxGroup = 'replied' | 'review' | 'approved' | 'sending' | 'checking' | 'sent' | 'paused' | 'closed'; // derived
export type CheckSeverity = 'block' | 'warn' | 'pass';
export type ReviewKind =
  | 'board_unreadable' | 'board_empty' | 'sponsorship_unclear' | 'size_unknown' | 'size_guess' | 'domain_missing' | 'flagged'
  | 'merge_candidate' | 'careers_changed' | 'size_check' | 'reader_disagrees'; // the last four are the foundation values (#33)
export type NoGoReason = 'no_sponsorship' | 'citizenship' | 'over_300' | 'defence_contractor' | 'agency' | 'said_no' | 'stop_reply' | 'not_a_fit' | 'other';
export type NoGoSource = 'devanshu' | 'rules' | 'reply' | 'import' | 'claude';
export type Actor = 'devanshu' | 'claude' | 'sender' | 'crawler' | 'rules' | 'daily_list' | 'import' | 'system';
export type ImportSource = 'data_js' | 'local_storage' | 'contacts_json' | 'radar_db' | 'devboard' | 'contacted_txt' | 'devvault_notes';
export type JobKind = 'install' | 'rebuild' | 'crawl' | 'daily_list' | 'sender' | 'import';
export type ReadResult = 'ok' | 'empty' | 'error';
export type LinkedInStep = 'request_sent' | 'accepted' | 'message_sent' | 'replied';
export type LinkedInReason = 'past_budget' | 'no_email' | 'alum_first';
export type SenderState = 'running' | 'paused_by_you' | 'paused_hard_block' | 'outside_window' | 'weekend' | 'signed_out'; // derived from sender.pause_kind and the clock
export type SavedBy = 'claude' | 'devanshu' | 'sender' | 'import';

export interface CompanyRef { id: Id; name: string; status: CompanyStatus; pausedAt: IsoTime | null; heldByTouchId: Id | null }
export interface PersonRef { id: Id; name: string; title: string; roleKind: RoleKind; email: string | null; linkedinUrl: string | null; revision: Revision }
export interface Fact {
  id: Id; fact: FactKind; value: string; low: number | null; high: number | null; confidence: Confidence;
  locked: boolean; sourceKind: SourceKind; sourceRef: string; quote: string; observedOn: IsoDate | null;
}
export interface RoleToName {
  postingId: Id; title: string; url: string; location: string; firstSeenAt: IsoTime;
  levelTier: LevelTier; yearsAsked: number | null; start2027: boolean;
}
export interface JobRunSummary {
  job: JobKind; startedAt: IsoTime; finishedAt: IsoTime | null; status: 'running' | 'ok' | 'partial' | 'failed';
  error: string; counts: { label: string; value: number }[]; // from job_run.summary
  expectedNextAt: IsoTime | null; late: boolean;             // derived
}

// Shell (spec 13.1)
export interface ShellSummary {
  asOf: IsoTime; pickedToday: number; capacityToday: number; unhandledReplies: number; draftsToReview: number;
  linkedinToReach: number; openReviewItems: number; noGoCount: number; doneCount: number;
  sending: { state: SenderState; sentToday: number; limitToday: number };
  repliesThisMonth: number; jobs: JobRunSummary[];
  plan: { done: number; total: number }; winsUnseen: number; // #33
}
export interface SearchCompany { id: Id; name: string; aliases: string[]; domain: string | null; status: CompanyStatus; statusLine: string; inProgress: boolean }
export interface SearchPerson { id: Id; companyId: Id; companyName: string; name: string; title: string; email: string | null; statusLine: string }
export interface SearchIndex { asOf: IsoTime; companies: SearchCompany[]; people: SearchPerson[] }
export interface PostingHit { postingId: Id; title: string; companyId: Id; companyName: string; open: boolean }

// Today (spec 13.2)
export interface DailyEntry {
  company: CompanyRef & { domain: string | null; hq: string };
  listGroup: ListGroup; rank: number; state: 'proposed' | 'picked' | 'backlog' | 'removed'; revision: Revision;
  role: RoleToName | null; size: Fact | null; stage: Fact | null; sponsorship: SponsorshipShown;
  sponsorshipFact: Fact | null; domainFact: Fact | null; mailServerOk: boolean | null; // needs #4 0002 (stored MX check)
  why: string; otherRoles: { title: string; firstSeenAt: IsoTime }[]; peopleCount: number; draftCount: number;
  firstProposedOn: IsoDate;
}
export interface StillEmailing {
  company: CompanyRef; day: number; days: number; emailed: number; budget: number;
  nextStep: { kind: 'review_drafts' | 'fix_review' | 'answer_reply' | 'nothing' | 'paused'; text: string };
  state: 'emailing' | 'held' | 'paused' | 'waiting';
}
export interface TodayView {
  asOf: IsoTime; date: IsoDate; builtAt: IsoTime | null; capacity: number; picked: number;
  notReady: { reason: 'weekend' | 'not_built' | 'job_failed'; text: string } | null; // derived, so the browser never decides from its clock
  proposed: DailyEntry[]; backlog: DailyEntry[]; stillEmailing: StillEmailing[]; listJob: JobRunSummary | null;
}

// Outbox (spec 13.4)
export interface SendingStatus {
  state: SenderState; pausedReason: string; pausedAt: IsoTime | null; gmailMessage: string | null;
  sentToday: number; limitToday: number; rampLevel: number; rampLevels: number[]; nextLimitIfClean: number;
  lastStepDown: { at: IsoTime; reason: string } | null;
  signals: { bounceRate3d: number; gmailErrorsToday: number; stopRepliesToday: number };
  window: { start: string; end: string };
  nextSend: { at: IsoTime; tz: string; touchId: Id; personName: string; companyName: string } | null;
  noneReason: string | null; revision: Revision;
}
export type CheckFix =
  | { kind: 'compare'; otherMessageId: Id } | { kind: 'review_item'; reviewId: Id }
  | { kind: 'no_go'; noGoId: Id } | { kind: 'role_kind' };
export interface Check {
  code: string; severity: CheckSeverity; title: string; detail: string; fix: CheckFix | null;
  spans: { field: 'subject' | 'body'; start: number; end: number }[];
}
export interface Schedule { // derived
  earliestAt: IsoTime | null; tz: string;
  reason: 'next_slot' | 'per_company_limit' | 'spacing' | 'recruiter_day_2' | 'window' | 'daily_limit';
  text: string; companySentToday: number; companySlotsLeft: number;
}
export interface OutboxItem {
  touchId: Id; messageId: Id; seq: 0 | 1 | 2 | 3; state: MessageState; touchState: TouchState;
  group: OutboxGroup; sortKey: string; person: PersonRef; company: CompanyRef;
  subject: string; preview: string; summary: string; worst: CheckSeverity | null; blockCount: number; warnCount: number;
  at: IsoTime; schedule: Schedule | null; nextDue: { seq: 1 | 2 | 3; on: IsoDate } | null; revision: Revision;
}
export interface OutboxCounts { replied: number; review: number; approved: number; sending: number; checking: number; sent: number; paused: number; closed: number }
export interface OutboxView {
  asOf: IsoTime; sending: SendingStatus; items: OutboxItem[]; counts: OutboxCounts;
  closedBreakdown: { bounced: number; skipped: number; companyClosed: number };
}
export interface ThreadMessage { seq: 0 | 1 | 2 | 3 | null; direction: 'out' | 'in'; subject: string; body: string; at: IsoTime; from: string } // in: inbound_message
export interface OutboxDetail {
  asOf: IsoTime; item: OutboxItem; body: string; hook: { text: string; on: IsoDate | null }; project: string; role: RoleToName | null;
  checks: Check[]; savedBy: SavedBy; savedAt: IsoTime;
  followups: { seq: 1 | 2 | 3; dueOn: IsoDate; sentAt: IsoTime | null; state: MessageState }[];
  firstSentAt: IsoTime | null; gmailThreadUrl: string | null; // derived from touch.gmail_thread_id
  reply: { from: string; at: IsoTime; text: string; kind: ReplyKind } | null; touchRevision: Revision;
  held: { touchId: Id; personName: string; state: MessageState; seq: 0 | 1 | 2 | 3; dueOn: IsoDate | null }[];
  thread: ThreadMessage[]; bounceReason: string | null;
}
export interface DraftCompare {
  a: { messageId: Id; personName: string; body: string }; b: { messageId: Id; personName: string; body: string };
  similarity: number; matches: { aStart: number; aEnd: number; bStart: number; bEnd: number }[];
}

// LinkedIn (spec 13.3)
export interface LinkedInPerson {
  person: PersonRef; reason: LinkedInReason; steps: { step: LinkedInStep; at: IsoTime }[]; replyKind: ReplyKind | null;
  tab: 'to_reach' | 'waiting' | 'replied';
}
export interface LinkedInGroup {
  company: CompanyRef; role: RoleToName | null; emailed: number; onLinkedIn: number;
  heldBy: { touchId: Id; personName: string } | null; people: LinkedInPerson[];
}
export interface LinkedInView { asOf: IsoTime; groups: LinkedInGroup[]; counts: { toReach: number; waiting: number; replied: number; all: number }; requestsThisWeek: number }

// Needs you (spec 13.5)
export interface ReviewItem {
  id: Id; kind: ReviewKind; company: CompanyRef & { picked: boolean }; holdsEmail: boolean; reason: string;
  openedAt: IsoTime; ageDays: number; reopened: { previous: string; changed: string } | null;
  quote: { text: string; postingId: Id; postingTitle: string; url: string; firstSeenAt: IsoTime } | null;
  reads: { at: IsoTime; result: ReadResult; detail: string }[]; lastOkAt: IsoTime | null; openPostings: number | null;
  sizeFacts: Fact[]; revision: Revision;
}
export interface ResolvedReview { id: Id; kind: ReviewKind; companyName: string; resolution: 'marked_ok' | 'marked_no_go' | 'field_edited'; note: string; at: IsoTime; locked: boolean }
export interface NeedsYouView { asOf: IsoTime; open: ReviewItem[]; resolvedThisWeek: ResolvedReview[]; boardsJob: JobRunSummary | null }
export type ReviewFix =
  | { kind: 'ok' }
  | { kind: 'edit'; field: 'domain' | 'size' | 'sponsorship' | 'board_url'; value: string; source: string }
  | { kind: 'no_go'; reason: NoGoReason; source: string };

// No-go (spec 13.6). Two kinds of row: a no_go table row (liftable), or a removed company (restorable).
export interface NoGoRow {
  kind: 'no_go'; noGoId: Id;
  target: { kind: 'company'; company: CompanyRef } | { kind: 'person'; person: PersonRef; companyName: string };
  reason: NoGoReason; quote: string; note: string; source: NoGoSource; sourceRef: string;
  sourceUrl: string | null; importSource: ImportSource | null; // derived from source_ref
  addedAt: IsoTime;
}
export interface RemovedRow { kind: 'removed'; company: CompanyRef & { revision: Revision }; removeReason: string; note: string; removedAt: IsoTime }
export interface NoGoView {
  asOf: IsoTime; companies: (NoGoRow | RemovedRow)[]; people: NoGoRow[];
  countsByReason: { reason: NoGoReason | 'removed'; count: number }[];
}

// Company (spec 13.7)
export interface PostingRow {
  id: Id; title: string; location: string; url: string; firstSeenAt: IsoTime; lastSeenAt: IsoTime;
  closedAt: IsoTime | null; closeReason: string | null; levelTier: LevelTier;
  sponsorship: 'yes' | 'no' | 'opt_only' | 'unclear' | 'silent'; state: 'open' | 'closed' | 'out_of_lane' | 'hidden'; hiddenRule: string | null;
}
export interface CompanyPerson { person: PersonRef; channel: 'email' | 'linkedin'; statusLine: string; tone: 'ok' | 'info' | 'warn' | 'bad' | 'idle'; lastTouchAt: IsoTime | null }
export interface CompanyEvent { id: Id; at: IsoTime; kind: string; text: string; actor: Actor; importSource: ImportSource | null }
export interface CompanyView {
  asOf: IsoTime;
  company: CompanyRef & { domain: string | null; aliases: string[]; hq: string; pickedOn: IsoDate | null; day: number | null;
    closed: { reason: CloseReason; note: string; at: IsoTime } | null; revision: Revision };
  summary: string; facts: Fact[]; sponsorshipShown: SponsorshipShown; mailServerOk: boolean | null;
  board: { ats: string; token: string; url: string; lastRead: ReadResult | null; lastReadAt: IsoTime | null; errorStreak: number; emptyStreak: number } | null;
  role: RoleToName | null; postings: PostingRow[]; people: CompanyPerson[];
  touches: { touchId: Id; person: PersonRef; state: TouchState; summary: string; messages: ThreadMessage[] }[];
  evidence: Fact[]; events: CompanyEvent[]; reviewItems: ReviewItem[]; rulesetVersion: string;
}

// Done (spec 13.8)
export interface DoneCompany { company: CompanyRef; outcome: 'interview' | 'referral'; outcomeLabel: string; closedAt: IsoTime; howStarted: string; imported: boolean; role: string | null; note: string }
export interface DoneView {
  asOf: IsoTime; companies: DoneCompany[];
  metrics: { repliesThisMonth: number; repliesPerWeek: { weekOf: IsoDate; count: number }[]; replyRate: { replies: number; firstEmails: number }; referrals: number; interviews: number };
}

// Plan, outcomes, progress, wins (#33)
// They mirror foundation tables outreach_touch (channels, outcome dates), application, company_score (signals, lanes)
// and daily_list_entry (person_id, channel, list_group). #11's generated types replace these without touching a view.
export type Channel = 'linkedin_request' | 'linkedin_message' | 'email' | 'application' | 'hn_reply' | 'other';
export type ListGroup = 'level_role' | 'intern_junior' | 'reason_no_role' | 'any_us_role'; // widens DailyEntry.listGroup
export type StepKind = 'request' | 'apply';
export type OutcomeKind = 'accepted' | 'replied' | 'referred' | 'interview' | 'offer' | 'quiet';
export type WinKind = 'accepted' | 'replied' | 'referred' | 'interview' | 'offer';
export type WatchKind = 'careers_changed' | 'new_reason_no_role' | 'merge_candidate';
export type FunnelKey = 'requests' | 'accepts' | 'replies' | 'referrals' | 'interviews';

export interface SourceLink { label: string; url: string }
export interface NoteFact { text: string; source: SourceLink; inNote: boolean }     // a public-page fact and its link
export interface NoteDraft {
  noteId: Id; text: string; chars: number; maxChars: number;                         // maxChars from config, never hard-coded in the UI
  reasonLine: string; reasonState: 'proposed' | 'confirmed' | 'written';             // the one line that says why this person; Claude only proposes it
  needsYourLine: boolean;                                                            // derived: reasonState === 'proposed'
  facts: NoteFact[]; checks: Check[]; savedBy: SavedBy; savedAt: IsoTime; revision: Revision;
}
export interface PlanStep { kind: StepKind; touchId: Id | null; at: IsoTime | null }  // at null: not done yet
export interface PlanRow {
  entryId: Id; position: number; channel: 'linkedin_request' | 'email'; lane: ListGroup;
  company: CompanyRef & { domain: string | null; hq: string; revision: Revision };
  person: { person: PersonRef; foundOn: SourceLink | null } | null;                  // null: find someone
  role: RoleToName | null; applyUrl: string | null;                                  // null with no role
  reason: string; reasonSources: SourceLink[];                                       // derived: one reason and where it comes from
  note: NoteDraft | null;
  findSomeone: { searchUrl: string; handoff: string } | null;                        // set only when person is null
  emailTouchId: Id | null;                                                           // channel 'email': the Outbox item
  steps: PlanStep[];                                                                 // linkedin_request: request, then apply when there is a role
  state: 'todo' | 'done' | 'skipped';                                                // derived
  carriedFrom: IsoDate | null; revision: Revision;
}
export interface PlanView {
  asOf: IsoTime; date: IsoDate; builtAt: IsoTime | null; listJob: JobRunSummary | null;
  notReady: { reason: 'weekend' | 'not_built' | 'job_failed'; text: string } | null;
  rows: PlanRow[]; done: number; total: number;
  linkedinMonth: { used: number; limit: number; hardUsed: number; hardLimit: number };   // plan quota, and every request against the hard total
  tomorrow: { total: number; text: string; preview: { companyName: string; personName: string | null; roleTitle: string | null }[] };
  week: { requests: number; accepts: number; replies: number };
}
export interface PromptTouch { touchId: Id; person: PersonRef; company: CompanyRef; channel: Channel; sentAt: IsoTime; ageDays: number; context: string; revision: Revision }
export interface OutcomePromptView { asOf: IsoTime; touches: PromptTouch[] }
export interface Win {
  winId: Id; kind: WinKind; at: IsoTime; touchId: Id; person: PersonRef | null; company: CompanyRef;
  line: string; sentNote: string | null; role: string | null; replyKind: ReplyKind | null; unseen: boolean;   // line is derived
}
export interface WinsView { asOf: IsoTime; wins: Win[]; unseen: number; counts: { accepted: number; replied: number; referred: number; interview: number; offer: number }; since: IsoDate }
export interface FunnelStage { stage: FunnelKey; label: string; count: number; from: { stage: FunnelKey; count: number } | null }  // rate = count / from.count
export interface ProgressPeriod { label: string; from: IsoDate; to: IsoDate; stages: FunnelStage[]; effort: { label: string; count: number }[]; enough: boolean; quiet: boolean }
export interface BaseRates { since: IsoDate; requests: number; accepts: number; replies: number; medianDaysToReply: number | null; enough: boolean; minRequests: number }
export interface TodayProgress { planned: number; done: number; requests: number; applications: number; accepted: number; replied: number }
export interface ProgressView { asOf: IsoTime; today: TodayProgress; week: ProgressPeriod; month: ProgressPeriod; base: BaseRates }
export interface Finding { id: Id; text: string; counts: { label: string; count: number; of: number }[]; caution: string | null; changed: string | null }
export interface FindingsView { asOf: IsoTime; month: IsoDate; reportedOn: IsoDate | null; findings: Finding[]; changes: { at: IsoTime; text: string }[] }
export interface WatchItem { id: Id; kind: WatchKind; company: CompanyRef; text: string; at: IsoTime; link: SourceLink | null; reviewId: Id | null }
export interface WatchView { asOf: IsoTime; items: WatchItem[] }
export interface ScoreSignal {
  name: string; label: string; value: number; weight: number; points: number; share: number;   // share = points / sum of |points|, signed
  neutral: boolean; reason: string; source: SourceLink | null;
}
export interface ScoreView {
  asOf: IsoTime; companyId: Id; rankerVersion: string; score: number; rank: number | null; ofCompanies: number;
  lanes: { lane: ListGroup; strongest: boolean }[]; signals: ScoreSignal[]; scoredAt: IsoTime;
  previous: { rankerVersion: string; score: number } | null;
}
