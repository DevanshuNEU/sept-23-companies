import type { Id, Revision, CloseReason, RemoveReason, RoleKind, ReplyKind, LinkedInStep, PersonRef, ShellSummary, SearchIndex, PostingHit, DailyEntry, TodayView, SendingStatus, Check, OutboxView, OutboxDetail, DraftCompare, LinkedInPerson, LinkedInView, NeedsYouView, ReviewFix, NoGoView, CompanyView, DoneView, PlanView, OutcomePromptView, WinsView, ProgressView, FindingsView, WatchView, ScoreView, PlanRow, NoteDraft, OutcomeKind } from './types';

export interface WriteOptions { requestId: string; keepalive?: boolean } // keepalive: phase 2 uses fetch keepalive for flushes on pagehide
export type ApiErrorCode = 'blocked' | 'conflict' | 'capacity' | 'not_allowed' | 'not_found' | 'network';
export class ApiError extends Error { constructor(public code: ApiErrorCode, message: string, public checks: Check[] = []) { super(message) } }

export interface OutreachClient {
  readonly source: 'sample' | 'live';
  shell(): Promise<ShellSummary>;
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
  resumeCompany(companyId: Id, revision: Revision, o: WriteOptions): Promise<{ released: number }>;
  closeCompany(companyId: Id, reason: CloseReason, note: string, revision: Revision, o: WriteOptions): Promise<void>;
  reopenCompany(companyId: Id, revision: Revision, o: WriteOptions): Promise<void>;
  pauseSending(o: WriteOptions): Promise<SendingStatus>;
  resumeSending(acknowledgeBlock: boolean, o: WriteOptions): Promise<SendingStatus>; // 'not_allowed' after a hard block without acknowledge

  linkedin(): Promise<LinkedInView>;
  logLinkedIn(personId: Id, step: LinkedInStep, o: WriteOptions, replyKind?: Exclude<ReplyKind, 'unsorted' | 'auto_reply'>): Promise<LinkedInPerson>;

  needsYou(): Promise<NeedsYouView>;
  resolveReview(reviewId: Id, fix: ReviewFix, revision: Revision, o: WriteOptions): Promise<void>; // edit needs a source

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

  // Idempotency keys are `plan:<entryId>:request` and `plan:<entryId>:apply`: the same key twice returns the first result and writes nothing.
  logRequest(entryId: Id, input: { personId: Id; idempotencyKey: string }, revision: Revision, o: WriteOptions): Promise<PlanRow>;
  logApplied(entryId: Id, input: { postingId: Id; idempotencyKey: string }, revision: Revision, o: WriteOptions): Promise<PlanRow>; // 'not_allowed' with no role
  logOutcome(touchId: Id, kind: OutcomeKind, revision: Revision, o: WriteOptions, replyKind?: Exclude<ReplyKind, 'unsorted' | 'auto_reply'>): Promise<{ revision: Revision }>;
  skipRow(entryId: Id, revision: Revision, o: WriteOptions): Promise<PlanRow>; // carried to tomorrow; 'not_allowed' on a done row
  saveNote(noteId: Id, edit: { text: string; reasonLine: string; revision: Revision }, o: WriteOptions): Promise<NoteDraft>;
  confirmReason(noteId: Id, revision: Revision, o: WriteOptions): Promise<NoteDraft>; // reasonState becomes 'confirmed'; 'not_allowed' with an empty line
  markWinsSeen(winIds: Id[], o: WriteOptions): Promise<void>; // 'not_found' for an unknown id
  dismissWatch(itemId: Id, o: WriteOptions): Promise<void>;
}
