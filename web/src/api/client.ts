import type { Id, Revision, CloseReason, RemoveReason, RoleKind, ReplyKind, LinkedInStep, PersonRef, ShellSummary, Pulse, SearchIndex, PostingHit, DailyEntry, TodayView, SendingStatus, Check, OutboxView, OutboxDetail, DraftCompare, LinkedInPerson, LinkedInView, NeedsYouView, ReviewFix, NoGoView, CompanyView, DoneView, HealthView, PlanView, OutcomePromptView, WinsView, ProgressView, FindingsView, WatchView, ScoreView, PlanRow, NoteDraft, OutcomeKind, LogPeopleResult, BenchView, BrowseParams, BrowseView, SwapResult, PassReason, PassResult } from './types';

export interface WriteOptions { requestId: string; keepalive?: boolean } // keepalive: phase 2 uses fetch keepalive for flushes on pagehide
export type ApiErrorCode = 'blocked' | 'conflict' | 'capacity' | 'not_allowed' | 'not_found' | 'network';
export class ApiError extends Error { constructor(public code: ApiErrorCode, message: string, public checks: Check[] = []) { super(message) } }

export interface OutreachClient {
  readonly source: 'sample' | 'live';
  health(): Promise<HealthView>;
  shell(): Promise<ShellSummary>;
  pulse(): Promise<Pulse>; // one growing number; the shell polls it and refetches the plan, shell and Needs you when it moves
  searchIndex(): Promise<SearchIndex>;
  searchPostings(q: string): Promise<PostingHit[]>;

  today(): Promise<TodayView>;
  pick(companyId: Id, revision: Revision, o: WriteOptions): Promise<DailyEntry>;     // 'capacity' at the limit
  unpick(companyId: Id, revision: Revision, o: WriteOptions): Promise<DailyEntry>;
  backlog(companyId: Id, revision: Revision, o: WriteOptions): Promise<DailyEntry>;
  removeCompany(companyId: Id, reason: RemoveReason, note: string, revision: Revision, o: WriteOptions): Promise<void>;
  restoreCompany(companyId: Id, reason: string, revision: Revision, o: WriteOptions): Promise<void>; // removed to backlog

  outbox(): Promise<OutboxView>;
  outboxDetail(touchId: Id): Promise<OutboxDetail>;
  saveMessage(messageId: Id, edit: { subject: string; body: string; revision: Revision }, o: WriteOptions): Promise<OutboxDetail>;
  approve(messageId: Id, revision: Revision, o: WriteOptions): Promise<OutboxDetail>;   // 'blocked' with checks
  unapprove(messageId: Id, revision: Revision, o: WriteOptions): Promise<OutboxDetail>; // 'not_allowed' once claimed
  skip(messageId: Id, revision: Revision, o: WriteOptions): Promise<OutboxDetail>;      // 'not_allowed' in sending, send_unknown
  setReplyKind(touchId: Id, kind: Exclude<ReplyKind, 'unsorted'>, revision: Revision, o: WriteOptions): Promise<OutboxDetail>;
  setRoleKind(personId: Id, kind: RoleKind, revision: Revision, o: WriteOptions): Promise<PersonRef>;
  compareDrafts(messageId: Id, otherMessageId: Id): Promise<DraftCompare>;
  pauseCompany(companyId: Id, revision: Revision, o: WriteOptions): Promise<void>;
  resumeCompany(companyId: Id, revision: Revision, o: WriteOptions): Promise<{ released: number }>; // clears his pause only
  releaseHold(companyId: Id, revision: Revision, o: WriteOptions): Promise<{ released: number }>;   // a reply's hold, across the merged family
  closeCompany(companyId: Id, reason: CloseReason, note: string, revision: Revision, o: WriteOptions): Promise<void>;
  reopenCompany(companyId: Id, revision: Revision, o: WriteOptions): Promise<void>;
  pauseSending(o: WriteOptions): Promise<SendingStatus>;
  resumeSending(acknowledgeBlock: boolean, o: WriteOptions): Promise<SendingStatus>; // 'not_allowed' after a hard block without acknowledge

  linkedin(): Promise<LinkedInView>;
  logLinkedIn(personId: Id, step: LinkedInStep, o: WriteOptions, replyKind?: Exclude<ReplyKind, 'unsorted' | 'auto_reply'>): Promise<LinkedInPerson>;

  needsYou(): Promise<NeedsYouView>;
  resolveReview(reviewId: Id, fix: ReviewFix, revision: Revision, o: WriteOptions): Promise<void>; // edit needs a source
  // Undo from Resolved, after the 8 second window: an ok, a no-go or a merge goes back open (a merge is unmerged). The revision is the
  // settled row's (`ResolvedReview.revision`). 'not_allowed' for a value he set (locked) or an item already open; 'conflict' when stale.
  reopenReview(reviewId: Id, revision: Revision, o: WriteOptions): Promise<void>;

  noGo(): Promise<NoGoView>;
  liftNoGo(noGoId: Id, reason: string, o: WriteOptions): Promise<void>;

  company(companyId: Id): Promise<CompanyView>;
  done(): Promise<DoneView>;

  // Plan, outcomes, progress, wins (#33)
  plan(): Promise<PlanView>;
  outcomePrompt(): Promise<OutcomePromptView>;
  wins(): Promise<WinsView>;
  progress(): Promise<ProgressView>;
  findings(): Promise<FindingsView>;
  watchList(): Promise<WatchView>;
  score(companyId: Id): Promise<ScoreView>; // 'not_found' for an unknown company

  // Idempotency keys are `plan:<entryId>:request` and `plan:<entryId>:apply`, sent in the body: the same key again (any request id, any
  // revision) answers with the row and writes nothing. A new key checks the row's `revision` like every other row write: a stale one is
  // 'conflict' (#11's API checks it on step logs too). A step already logged under another key, a removed company's row, a paused or
  // no-go company are 'not_allowed'; a person or role that is not the row's is the API's bad_request, which reads as 'not_allowed'.
  /** A row with a person sends `personId`; a company row (nobody on it) sends `personName`, the person he asked. */
  logRequest(entryId: Id, input: { personId: Id; idempotencyKey: string } | { personName: string; idempotencyKey: string }, revision: Revision, o: WriteOptions): Promise<PlanRow>;
  logApplied(entryId: Id, input: { postingId: Id; idempotencyKey: string }, revision: Revision, o: WriteOptions): Promise<PlanRow>; // 'not_allowed' with no role
  // Company first (#9, P2). The people he asked at a company row, pasted as `Name | Title | note` lines (up to 10): the API reads them,
  // adds or finds each person and logs one request each with the note. `idempotencyKey` is the paste's (`plan:<entryId>:people:<n>`):
  // the same key again writes nothing. A paused or no-go company or person, or a person already asked since the list date, is
  // 'not_allowed' and nothing is written; a line it cannot read is the API's bad_request. Each person comes back with the note's checks.
  logPeople(entryId: Id, input: { lines: string; idempotencyKey: string }, revision: Revision, o: WriteOptions): Promise<LogPeopleResult>;
  /** His LinkedIn people search for the row's company; the same address again writes nothing. Not a people search: 'not_allowed'. */
  saveSearch(entryId: Id, url: string, o: WriteOptions): Promise<PlanRow>;
  logOutcome(touchId: Id, kind: OutcomeKind, revision: Revision, o: WriteOptions, replyKind?: Exclude<ReplyKind, 'unsorted' | 'auto_reply'>): Promise<{ revision: Revision }>;
  skipRow(entryId: Id, revision: Revision, o: WriteOptions): Promise<PlanRow>; // carried to tomorrow; 'not_allowed' on a done row
  // Top picks (2026-10-26). Not now: the pick leaves today's list (the company is kept, never a no-go) and the bench's best company
  // takes its place, in one write. `idempotencyKey` is `plan:<entryId>:pass`: the same key again answers the same. A stale revision is
  // 'conflict'; a row he already reached is 'not_allowed'. `note` is what he typed under Role not for me or Other (up to 280
  // characters). Wrong company and Wrong location fix the posting or board and give no cooldown.
  passPick(entryId: Id, input: { reason: PassReason | null; note?: string | null; idempotencyKey: string }, revision: Revision, o: WriteOptions): Promise<PassResult>;
  bench(): Promise<BenchView>; // the next best companies, fresh early career fits first
  /** Adds a bench company after today's last pick; 'not_allowed' with no room this week or a company not on the bench. */
  addFromBench(companyId: Id, input: { idempotencyKey: string }, o: WriteOptions): Promise<PlanRow>;
  /** Browse companies (#120): one page of every company the plan could pick, in stored rank order; `after` is the cursor. */
  browse(params: BrowseParams): Promise<BrowseView>;
  /** Swap: the company takes the pick's slot and the pick goes back to the bench (not a Not now, no cooldown, no room used).
   *  `idempotencyKey` is `pick-swap:<entryId>:<companyId>`. A no-go, in flight or held back company, or a pick he already reached, is 'not_allowed'. */
  swapPick(entryId: Id, input: { companyId: Id; idempotencyKey: string }, revision: Revision, o: WriteOptions): Promise<SwapResult>;
  saveNote(noteId: Id, edit: { text: string; reasonLine: string; revision: Revision }, o: WriteOptions): Promise<NoteDraft>;
  confirmReason(noteId: Id, revision: Revision, o: WriteOptions): Promise<NoteDraft>; // reasonState becomes 'confirmed'; 'not_allowed' with an empty line
  markWinsSeen(winIds: Id[], o: WriteOptions): Promise<void>; // 'not_found' for an unknown id
  dismissWatch(itemId: Id, o: WriteOptions): Promise<void>;
}
