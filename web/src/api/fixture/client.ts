import { ApiError, type OutreachClient } from '../client';
import type {
  CompanyView, DailyEntry, DoneView, DraftCompare, Id, LinkedInPerson, LinkedInView, NeedsYouView, NoGoView, OutboxDetail, OutboxView, PersonRef,
  PostingHit, SearchIndex, SendingStatus, ShellSummary, TodayView,
} from '../types';
import { buildFixture, type FixtureDb } from './data';

const STOP_WORDS = new Set(['of', 'the', 'and', 'a', 'an', 'at', 'for', 'in', 'to']);

/** A posting matches by substring, or by the first letters of its words ("mts" finds "Member of Technical Staff"). */
function postingMatches(title: string, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (q === '') return false;
  const t = title.toLowerCase();
  if (t.includes(q)) return true;
  const initials = t.split(/[^a-z0-9]+/).filter((w) => w && !STOP_WORDS.has(w)).map((w) => w[0]).join('');
  return q.length >= 2 && initials.includes(q);
}

export class FixtureClient implements OutreachClient {
  readonly source = 'sample' as const;
  private readonly latencyMs: number;

  constructor(protected db: FixtureDb = buildFixture(), opts: { latencyMs?: number } = {}) {
    this.latencyMs = opts.latencyMs ?? 0;
  }

  /** Every read waits `latencyMs`, fails in the `error` scenario, and hands back a copy so a caller cannot change the db. */
  protected async read<T>(pick: () => T): Promise<T> {
    if (this.latencyMs > 0) await new Promise((resolve) => setTimeout(resolve, this.latencyMs));
    if (this.db.scenario === 'error') throw new ApiError('network', 'Cannot reach the outreach server');
    return structuredClone(pick());
  }

  shell(): Promise<ShellSummary> { return this.read(() => this.db.shell); }
  searchIndex(): Promise<SearchIndex> { return this.read(() => this.db.searchIndex); }
  searchPostings(q: string): Promise<PostingHit[]> { return this.read(() => this.db.postings.filter((p) => postingMatches(p.title, q)).slice(0, 20)); }
  today(): Promise<TodayView> { return this.read(() => this.db.today); }
  outbox(): Promise<OutboxView> { return this.read(() => this.db.outbox); }
  outboxDetail(touchId: Id): Promise<OutboxDetail> {
    return this.read(() => {
      const d = this.db.details[touchId];
      if (!d) throw new ApiError('not_found', `No email for touch ${touchId}`);
      return d;
    });
  }
  compareDrafts(messageId: Id, otherMessageId: Id): Promise<DraftCompare> {
    return this.read(() => {
      const swap = (x: DraftCompare): DraftCompare => ({
        a: x.b, b: x.a, similarity: x.similarity, matches: x.matches.map((m) => ({ aStart: m.bStart, aEnd: m.bEnd, bStart: m.aStart, bEnd: m.aEnd })),
      });
      for (const x of this.db.compares) {
        if (x.a.messageId === messageId && x.b.messageId === otherMessageId) return x;
        if (x.b.messageId === messageId && x.a.messageId === otherMessageId) return swap(x);
      }
      throw new ApiError('not_found', 'Those drafts cannot be compared');
    });
  }
  linkedin(): Promise<LinkedInView> { return this.read(() => this.db.linkedin); }
  needsYou(): Promise<NeedsYouView> { return this.read(() => this.db.needsYou); }
  noGo(): Promise<NoGoView> { return this.read(() => this.db.noGo); }
  company(companyId: Id): Promise<CompanyView> {
    return this.read(() => {
      const c = this.db.companies[companyId];
      if (!c) throw new ApiError('not_found', `No company ${companyId}`);
      return c;
    });
  }
  done(): Promise<DoneView> { return this.read(() => this.db.done); }

  /* Writes arrive in Task 5. A method may take fewer parameters than the interface, so the stubs take none. */
  private notYet(): Promise<never> { return Promise.reject(new Error('Fixture writes arrive in Task 5')); }
  pick(): Promise<DailyEntry> { return this.notYet(); }
  unpick(): Promise<DailyEntry> { return this.notYet(); }
  backlog(): Promise<DailyEntry> { return this.notYet(); }
  removeCompany(): Promise<void> { return this.notYet(); }
  restoreCompany(): Promise<void> { return this.notYet(); }
  saveMessage(): Promise<OutboxDetail> { return this.notYet(); }
  approve(): Promise<OutboxDetail> { return this.notYet(); }
  unapprove(): Promise<OutboxDetail> { return this.notYet(); }
  skip(): Promise<OutboxDetail> { return this.notYet(); }
  setReplyKind(): Promise<OutboxDetail> { return this.notYet(); }
  setRoleKind(): Promise<PersonRef> { return this.notYet(); }
  pauseCompany(): Promise<void> { return this.notYet(); }
  resumeCompany(): Promise<{ released: number }> { return this.notYet(); }
  closeCompany(): Promise<void> { return this.notYet(); }
  reopenCompany(): Promise<void> { return this.notYet(); }
  pauseSending(): Promise<SendingStatus> { return this.notYet(); }
  resumeSending(): Promise<SendingStatus> { return this.notYet(); }
  logLinkedIn(): Promise<LinkedInPerson> { return this.notYet(); }
  resolveReview(): Promise<void> { return this.notYet(); }
  liftNoGo(): Promise<void> { return this.notYet(); }
}
