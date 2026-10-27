import { ApiError, type OutreachClient, type WriteOptions } from '../client';
import type {
  Check, CloseReason, CompanyRef, CompanyView, DailyEntry, DoneView, DraftCompare, Fact, FactKind, HealthView, Id, IsoTime, LinkedInPerson, LinkedInStep, LinkedInView,
  NeedsYouView, NoGoReason, NoGoView, OutboxDetail, OutboxGroup, OutboxItem, OutboxView, PersonRef, PostingHit, RemovedRow, RemoveReason, ReplyKind,
  ResolvedReview, ReviewFix, Revision, RoleKind, Pulse, SearchIndex, SendingStatus, ShellSummary, TodayView,
  BenchView, FindingsView, FunnelKey, LoggedPerson, LogPeopleResult, NoteDraft, OutcomeKind, OutcomePromptView, PlanRow, PlanView, ProgressPeriod, ProgressView, PromptTouch, ScoreView, StepKind, TomorrowRow, WatchView, Win, WinKind, WinsView, PassReason, PassResult,
} from '../types';
import { buildFixture, computeShell, countGroups, GROUP_INDEX, settledOn, STEP_LINE, type FixtureDb, type SettledReview } from './data';
import { rescanNote, tomorrowText, winsOnDay } from './plan-data';
import { askedToday, benchRoom, benchRoomText, benchToRow, pickOrder } from './queue-data';

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

/* ------------------------------------------------------------------ helpers for writes */

type Obj = Record<string, unknown>;
function eachObject(node: unknown, visit: (o: Obj) => void): void {
  if (Array.isArray(node)) {
    for (const n of node) eachObject(n, visit);
  } else if (node && typeof node === 'object') {
    visit(node as Obj);
    for (const v of Object.values(node)) eachObject(v, visit);
  }
}
const isCompanyRef = (o: Obj, id: Id) => o.id === id && 'heldByTouchId' in o && 'pausedAt' in o;
const isPersonRef = (o: Obj, id: Id) => o.id === id && 'roleKind' in o && 'linkedinUrl' in o;
const refOf = (c: CompanyRef): CompanyRef => ({
  id: c.id, name: c.name, status: c.status, pausedAt: c.pausedAt, heldByTouchId: c.heldByTouchId, held: c.held,
  heldByReply: c.heldByReply,
});
const notAllowed = (message: string) => new ApiError('not_allowed', message);
// The API's words for a settled item's note (outreach.review.words).
const EDIT_LABEL: Record<Extract<ReviewFix, { kind: 'edit' }>['field'], string> = { size: 'Size', domain: 'Email domain', sponsorship: 'Sponsorship', board_url: 'Job board' };
const NO_GO_PHRASE: Record<NoGoReason, string> = {
  no_sponsorship: 'does not sponsor', citizenship: 'needs citizenship', over_300: 'over 300 people', defence_contractor: 'defence contractor',
  agency: 'a staffing agency', said_no: 'they said no', stop_reply: 'they asked us to stop', not_a_fit: 'not a fit', other: 'another reason',
};
const rankOf = (i: OutboxItem) => Number(i.sortKey.split('.')[1]);

/** The group a row belongs in, from its stored state. The browser never works this out. */
function deriveGroup(i: OutboxItem): OutboxGroup {
  if (i.touchState === 'bounced' || i.touchState === 'closed') return 'closed';
  if (i.touchState === 'replied') return 'replied';
  const open = i.state === 'drafted' || i.state === 'approved' || i.state === 'planned';
  if (open && i.company.pausedAt) return 'paused';
  switch (i.state) {
    case 'drafted': case 'planned': return 'review';
    case 'approved': return 'approved';
    case 'sending': return 'sending';
    case 'send_unknown': return 'checking';
    case 'sent': return 'sent';
    default: return 'closed';
  }
}

/** Three scripted rules stand in for #11's checks: a changed middle clears the near-identical block, then two phrase rules. */
const RULES = [
  { code: 'banned_phrase', passTitle: 'No banned phrases', title: 'Banned phrase', detail: '"I\'d love to" is on the banned list. Say it plainly.', find: /I'd love to/gi },
  { code: 'dash', passTitle: 'No em or en dashes', title: 'Em or en dash', detail: 'Use a comma, a colon or two sentences instead.', find: /[\u2013\u2014]/g },
];
const SEVERITY_ORDER = { block: 0, warn: 1, pass: 2 } as const;
const passCode = (title: string) => `pass_${title.toLowerCase().replace(/[^a-z0-9]+/g, '')}`;

function spansOf(re: RegExp, field: 'subject' | 'body', text: string): Check['spans'] {
  return [...text.matchAll(re)].map((m) => ({ field, start: m.index ?? 0, end: (m.index ?? 0) + m[0].length }));
}

function rescan(old: Check[], oldBody: string, edit: { subject: string; body: string }): Check[] {
  const inner = (b: string) => b.split('\n\n').slice(1, -1).join('\n\n');
  const middleChanged = inner(edit.body) !== inner(oldBody);
  const next = old.filter((c) => !(middleChanged && c.code === 'near_identical'));
  for (const r of RULES) {
    const spans = [...spansOf(r.find, 'subject', edit.subject), ...spansOf(r.find, 'body', edit.body)];
    const repl: Check = spans.length
      ? { code: r.code, severity: 'warn', title: r.title, detail: r.detail, fix: null, spans }
      : { code: passCode(r.passTitle), severity: 'pass', title: r.passTitle, detail: '', fix: null, spans: [] };
    const at = next.findIndex((c) => c.code === r.code || c.code === passCode(r.passTitle));
    if (at >= 0) next[at] = repl;
    else next.push(repl);
  }
  return next.sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]);
}

export class FixtureClient implements OutreachClient {
  readonly source = 'sample' as const;
  private readonly latencyMs: number;
  /** First result per requestId, so a repeated write does the work once. Only successes are kept. */
  private readonly results = new Map<string, unknown>();
  /** Today rows taken off the list by removeCompany, so restoreCompany can bring back the same row. */
  private readonly removedEntries = new Map<Id, DailyEntry>();
  /** Plan rows of a removed company. The API leaves a removed entry off the plan, and restoreCompany brings it back carried. */
  private readonly removedPlanRows = new Map<Id, PlanRow[]>();
  /** Idempotency keys (`plan:<entryId>:request`) and the touch each one made, so a repeated key writes nothing. Only successes are kept. */
  private readonly keys = new Map<string, Id>();
  /** Pasted people logs by their key, so the same paste again writes nothing. */
  private readonly pastes = new Map<string, LoggedPerson[]>();
  /** Not now and Add to today by their keys: the same key again answers the same (top picks). */
  private readonly picks = new Map<string, PassResult | Id>();
  /** Prompt touches that got an outcome and left the prompt. A reply on a quiet touch still finds its person here. */
  private readonly leftPrompt = new Map<Id, PromptTouch>();
  /** Touch revisions after an outcome. A touch with no entry is at its own revision, or 1. */
  private readonly touchRevisions = new Map<Id, number>();
  /** Funnel stages already counted for a touch, so an outcome logged twice never counts twice. */
  private readonly counted = new Map<Id, Set<FunnelKey>>();
  private nextId = 900_000;
  /** The pulse: one more for every write that ran (a replay of a request id is no write). */
  private revision = 1;

  constructor(protected db: FixtureDb = buildFixture(), opts: { latencyMs?: number } = {}) {
    this.latencyMs = opts.latencyMs ?? 0;
  }

  /** Every call waits `latencyMs`, and fails in the `error` scenario. */
  private async gate(): Promise<void> {
    if (this.latencyMs > 0) await new Promise((resolve) => setTimeout(resolve, this.latencyMs));
    if (this.db.scenario === 'error') throw new ApiError('network', 'Cannot reach the Knock server');
  }

  /** Reads hand back a copy so a caller cannot change the db. */
  protected async read<T>(pick: () => T): Promise<T> {
    await this.gate();
    return structuredClone(pick());
  }

  /** `work` checks first, then changes every copy of the data. Derived counts are recomputed after it, and the result is cached by requestId. */
  protected async write<T>(o: WriteOptions, work: () => T): Promise<T> {
    await this.gate();
    if (this.results.has(o.requestId)) return structuredClone(this.results.get(o.requestId) as T);
    const result = work();
    this.settle();
    this.revision += 1;
    this.results.set(o.requestId, structuredClone(result));
    return structuredClone(result);
  }

  /** A fixed sample: the shell's jobs and sender, with the queue and board counts of a quiet night. */
  health(): Promise<HealthView> {
    return this.read(() => ({
      asOf: this.db.shell.asOf,
      store: { version: 2, rulesetVersion: 'r-3f9a0c71b2de' },
      jobs: this.db.shell.jobs,
      readQueue: { queued: 14, leased: 2, leaseExpired: 0, done: 812, refused: 3, oldestQueuedAgeHours: 6.5, lastNight: null },
      boards: { active: 47, dueNow: 5, erroring: 2 },
      watchedPages: { total: 9, changedLast7Days: 1 },
      sender: this.db.shell.sending,
    }));
  }
  shell(): Promise<ShellSummary> { return this.read(() => this.db.shell); }
  pulse(): Promise<Pulse> { return this.read(() => ({ revision: this.revision })); }
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
  plan(): Promise<PlanView> { return this.read(() => this.db.plan); }
  bench(): Promise<BenchView> { return this.read(() => this.db.bench); }
  outcomePrompt(): Promise<OutcomePromptView> { return this.read(() => this.db.prompt); }
  wins(): Promise<WinsView> { return this.read(() => this.db.wins); }
  progress(): Promise<ProgressView> { return this.read(() => this.db.progress); }
  findings(): Promise<FindingsView> { return this.read(() => this.db.findings); }
  watchList(): Promise<WatchView> { return this.read(() => this.db.watch); }
  score(companyId: Id): Promise<ScoreView> {
    return this.read(() => {
      const s = this.db.scores[companyId];
      if (!s) throw new ApiError('not_found', `No score for company ${companyId}`);
      return s;
    });
  }


  /* ================================================================== writes */

  /* ---------------------------------------------------- shared by every view */

  private get now(): IsoTime { return this.db.today.asOf; }

  /** Counts and the next send are never edited by a write: they are recomputed from the rows here. */
  private settle(): void {
    const db = this.db;
    db.outbox.counts = countGroups(db.outbox.items);
    db.today.picked = db.today.proposed.filter((e) => e.state === 'picked').length;
    const s = db.outbox.sending;
    if (s.state === 'running') {
      const queue = db.outbox.items.filter((i) => i.group === 'approved' && i.schedule?.earliestAt);
      const next = queue.sort((a, b) => ((a.schedule?.earliestAt ?? '') < (b.schedule?.earliestAt ?? '') ? -1 : 1))[0];
      const at = next?.schedule?.earliestAt;
      s.nextSend = next && at ? { at, tz: next.schedule?.tz ?? '', touchId: next.touchId, personName: next.person.name, companyName: next.company.name } : null;
      s.noneReason = next ? null : 'Nothing is approved.';
    }
    this.settlePlan();
    db.needsYou.settledToday = settledOn(db.needsYou.resolvedThisWeek, db.today.date);
    db.shell = computeShell({ ...db, jobs: db.shell.jobs });
  }

  /** Everything the plan, progress and wins views derive, worked out here from the rows and wins and nowhere else. */
  private settlePlan(): void {
    const { plan, progress, wins } = this.db;
    // As the API: positions count 1 up over the rows on the plan, so a removed company's place is taken.
    plan.rows.forEach((row, i) => { row.position = i + 1; });
    for (const row of plan.rows) if (row.state === 'todo' && row.steps.length > 0 && row.steps.every((st) => st.at !== null)) row.state = 'done';
    plan.done = plan.rows.filter((r) => r.state === 'done').length;
    plan.total = plan.rows.length;

    for (const period of [progress.week, progress.month]) {
      const count = (k: FunnelKey) => period.stages.find((st) => st.stage === k)?.count ?? 0;
      for (const st of period.stages) if (st.from) st.from.count = count(st.from.stage);
      period.enough = count('requests') >= progress.base.minRequests;
      if (period.quiet && count('replies') + count('referrals') + count('interviews') > 0) period.quiet = false;
    }
    progress.base.enough = progress.base.requests >= progress.base.minRequests;
    const week = (k: FunnelKey) => progress.week.stages.find((st) => st.stage === k)?.count ?? 0;
    plan.week = { requests: week('requests'), accepts: week('accepts'), replies: week('replies') };

    const logged = (kind: StepKind) => plan.rows.reduce((n, r) => n + r.steps.filter((st) => st.kind === kind && st.at !== null).length, 0);
    progress.today = { planned: plan.rows.length, done: plan.done, requests: logged('request'), applications: logged('apply'), accepted: winsOnDay(wins.wins, plan.date, 'accepted'), replied: winsOnDay(wins.wins, plan.date, 'replied') };

    wins.unseen = wins.wins.filter((w) => w.unseen).length;
    const tally = (k: WinKind) => wins.wins.filter((w) => w.kind === k).length;
    wins.counts = { accepted: tally('accepted'), replied: tally('replied'), referred: tally('referred'), interview: tally('interview'), offer: tally('offer') };
  }

  private view(id: Id): CompanyView {
    const v = this.db.companies[id];
    if (!v) throw new ApiError('not_found', `No company ${id}`);
    return v;
  }

  private fresh(id: Id, revision: Revision): CompanyView {
    const v = this.view(id);
    if (v.company.revision !== revision) throw new ApiError('conflict', `${v.company.name} changed since you loaded it`);
    return v;
  }

  /** One company write: patch every CompanyRef copy, bump the revision (mirrored on its Today row), log the event, and keep the search row. */
  private touchCompany(id: Id, patch: Partial<CompanyRef>, event: [kind: string, text: string], line?: string): CompanyView {
    const v = this.view(id);
    const revision = v.company.revision + 1;
    eachObject(this.db, (o) => {
      if (!isCompanyRef(o, id)) return;
      Object.assign(o, patch);
      if ('revision' in o) o.revision = revision;
    });
    for (const e of [...this.db.today.proposed, ...this.db.today.backlog]) if (e.company.id === id) e.revision = revision;
    v.events.unshift({ id: this.nextId++, at: this.now, kind: event[0], text: event[1], actor: 'devanshu', importSource: null });
    const row = this.db.searchIndex.companies.find((x) => x.id === id);
    if (row) {
      if (patch.status) { row.status = patch.status; row.inProgress = patch.status === 'working'; }
      if (line !== undefined) row.statusLine = line;
    }
    return v;
  }

  private countReason(reason: NoGoReason | 'removed', delta: number): void {
    const list = this.db.noGo.countsByReason;
    const hit = list.find((r) => r.reason === reason);
    if (hit) hit.count = Math.max(0, hit.count + delta);
    else if (delta > 0) list.push({ reason, count: delta });
  }

  /* ---------------------------------------------------- Today */

  private takeEntry(id: Id): DailyEntry | undefined {
    for (const list of [this.db.today.proposed, this.db.today.backlog]) {
      const at = list.findIndex((e) => e.company.id === id);
      if (at >= 0) return list.splice(at, 1)[0];
    }
    return undefined;
  }

  private entry(id: Id, revision: Revision): { entry: DailyEntry; list: DailyEntry[] } {
    this.fresh(id, revision);
    const list = [this.db.today.proposed, this.db.today.backlog].find((l) => l.some((e) => e.company.id === id));
    const entry = list?.find((e) => e.company.id === id);
    if (!list || !entry) throw new ApiError('not_found', `Company ${id} is not on Today`);
    return { entry, list };
  }

  pick(companyId: Id, revision: Revision, o: WriteOptions): Promise<DailyEntry> {
    return this.write(o, () => {
      const t = this.db.today;
      const { entry, list } = this.entry(companyId, revision);
      if (entry.state === 'picked') throw notAllowed(`${entry.company.name} is already picked`);
      if (t.picked >= t.capacity) throw new ApiError('capacity', `All ${t.capacity} of today's picks are used`);
      if (list === t.backlog) {
        t.backlog.splice(t.backlog.indexOf(entry), 1);
        entry.rank = Math.max(0, ...t.proposed.map((e) => e.rank)) + 1;
        t.proposed.push(entry);
      }
      entry.state = 'picked';
      const v = this.touchCompany(companyId, { status: 'working' }, ['picked', 'Picked from Today'], 'Picked today');
      v.company.pickedOn = t.date;
      v.company.day = 1;
      return entry;
    });
  }

  unpick(companyId: Id, revision: Revision, o: WriteOptions): Promise<DailyEntry> {
    return this.write(o, () => {
      const { entry } = this.entry(companyId, revision);
      if (entry.state !== 'picked') throw notAllowed(`${entry.company.name} is not picked`);
      entry.state = 'proposed';
      const v = this.touchCompany(companyId, { status: 'candidate' }, ['unpicked', 'Taken off the picks'], 'Proposed');
      v.company.pickedOn = null;
      v.company.day = null;
      return entry;
    });
  }

  backlog(companyId: Id, revision: Revision, o: WriteOptions): Promise<DailyEntry> {
    return this.write(o, () => {
      const t = this.db.today;
      const { entry, list } = this.entry(companyId, revision);
      if (list === t.backlog) throw notAllowed(`${entry.company.name} is already in the backlog`);
      t.proposed.splice(t.proposed.indexOf(entry), 1);
      entry.state = 'backlog';
      t.backlog.unshift(entry);
      const v = this.touchCompany(companyId, { status: 'backlog' }, ['backlog', 'Moved to the backlog'], 'Backlog');
      v.company.pickedOn = null;
      v.company.day = null;
      return entry;
    });
  }

  removeCompany(companyId: Id, reason: RemoveReason, note: string, revision: Revision, o: WriteOptions): Promise<void> {
    return this.write(o, () => {
      const v = this.fresh(companyId, revision);
      if (v.company.status === 'removed' || v.company.status === 'closed') throw notAllowed(`${v.company.name} is already ${v.company.status}`);
      // As the API: a company with a LinkedIn request or an application logged is closed on its page, not removed.
      if (this.db.plan.rows.some((row) => row.company.id === companyId && row.steps.some((st) => st.at !== null))) {
        throw notAllowed(`${v.company.name} already has a LinkedIn request or an application logged. Close it on its page instead`);
      }
      const taken = this.takeEntry(companyId);
      if (taken) this.removedEntries.set(companyId, taken);
      // As the API: its row is off today's plan (a removed entry is not on the plan), so the day's total drops.
      const off = this.db.plan.rows.filter((row) => row.company.id === companyId);
      if (off.length > 0) {
        this.removedPlanRows.set(companyId, off);
        this.db.plan.rows = this.db.plan.rows.filter((row) => row.company.id !== companyId);
      }
      this.touchCompany(companyId, { status: 'removed' }, ['removed', note ? `Removed by you: ${note}` : 'Removed by you'], 'Removed by you');
      this.db.noGo.companies.unshift({ kind: 'removed', company: { ...refOf(v.company), revision: v.company.revision }, removeReason: reason, note, removedAt: this.now });
      this.countReason('removed', 1);
    });
  }

  restoreCompany(companyId: Id, reason: string, revision: Revision, o: WriteOptions): Promise<void> {
    return this.write(o, () => {
      const row = this.db.noGo.companies.find((r): r is RemovedRow => r.kind === 'removed' && r.company.id === companyId);
      if (!row) throw new ApiError('not_found', `Company ${companyId} is not in the removed list`);
      const v = this.fresh(companyId, revision);
      this.db.noGo.companies = this.db.noGo.companies.filter((r) => r !== row);
      this.countReason('removed', -1);
      // As the API: today's entry becomes a backlog row, which the plan shows as carried to tomorrow.
      const back = this.removedPlanRows.get(companyId) ?? [];
      this.removedPlanRows.delete(companyId);
      for (const planRow of back) {
        if (planRow.state === 'todo') planRow.state = 'skipped';
        planRow.revision += 1;
      }
      this.db.plan.rows = [...this.db.plan.rows, ...back].sort((a, b) => a.entryId - b.entryId); // entry ids follow the plan's order
      this.touchCompany(companyId, { status: 'backlog' }, ['restored', `Restored to the backlog: ${reason}`], 'Backlog');
      const kept = this.removedEntries.get(companyId);
      this.removedEntries.delete(companyId);
      const entry: DailyEntry = kept ?? {
        company: { ...refOf(v.company), domain: v.company.domain, hq: v.company.hq }, listGroup: 'any_us_role', rank: this.db.today.backlog.length + 1, state: 'backlog',
        revision: v.company.revision, role: null, size: null, stage: null, sponsorship: 'not_stated', sponsorshipFact: null, domainFact: null, mailServerOk: null,
        why: 'Restored from the removed list by you.', otherRoles: [], peopleCount: 0, draftCount: 0, firstProposedOn: this.db.today.date,
      };
      // A kept row sat outside the db while the company changed, so take its ref from the current company.
      entry.company = { ...refOf(v.company), domain: v.company.domain, hq: v.company.hq };
      entry.state = 'backlog';
      entry.revision = v.company.revision;
      this.db.today.backlog.unshift(entry);
    });
  }

  /* ---------------------------------------------------- Outbox */

  /** Both copies of a row (the list and the detail) change together. */
  private patchItem(item: OutboxItem, patch: Partial<OutboxItem>): void {
    Object.assign(item, patch);
    const d = this.db.details[item.touchId];
    if (d) Object.assign(d.item, patch);
  }

  private message(messageId: Id, revision: Revision): { item: OutboxItem; detail: OutboxDetail } {
    const item = this.db.outbox.items.find((i) => i.messageId === messageId);
    const detail = item ? this.db.details[item.touchId] : undefined;
    if (!item || !detail) throw new ApiError('not_found', `No email ${messageId}`);
    if (item.revision !== revision) throw new ApiError('conflict', 'This email changed since you loaded it');
    return { item, detail };
  }

  /** Move a row into the group its state says, keeping every other row in its order. Drafts and replies sort newest first, the approved queue by send time. */
  private place(item: OutboxItem): void {
    const items = this.db.outbox.items;
    const group = deriveGroup(item);
    const others = items.filter((i) => i !== item && i.group === group).sort((a, b) => (a.sortKey < b.sortKey ? -1 : 1));
    const goesBefore = (o: OutboxItem) => (group === 'approved' ? (item.schedule?.earliestAt ?? '~') < (o.schedule?.earliestAt ?? '~') : item.at > o.at);
    const own = item.group === group ? rankOf(item) : Math.max(...items.map(rankOf)) + 1;
    const ordered = [...others];
    const at = others.findIndex(goesBefore);
    ordered.splice(at < 0 ? others.length : at, 0, item);
    const ranks = [...others.map(rankOf), own].sort((a, b) => a - b);
    ordered.forEach((it, n) => this.patchItem(it, { group, sortKey: `${GROUP_INDEX[group]}.${String(ranks[n] ?? 0).padStart(5, '0')}` }));
  }

  /** Store new checks and recompute the row's counts. The summary line only changes when the set of checks does. */
  private setChecks(item: OutboxItem, checks: Check[]): void {
    const detail = this.db.details[item.touchId];
    if (!detail) return;
    const changed = checks.map((c) => c.code).join() !== detail.checks.map((c) => c.code).join();
    detail.checks = checks;
    const blockCount = checks.filter((c) => c.severity === 'block').length;
    const warnCount = checks.filter((c) => c.severity === 'warn').length;
    const first = checks.find((c) => c.severity !== 'pass');
    this.patchItem(item, {
      blockCount, warnCount, worst: checks.length === 0 ? null : blockCount ? 'block' : warnCount ? 'warn' : 'pass',
      ...(changed ? { summary: first ? first.title : 'All checks passed' } : {}),
    });
  }

  saveMessage(messageId: Id, edit: { subject: string; body: string; revision: Revision }, o: WriteOptions): Promise<OutboxDetail> {
    return this.write(o, () => {
      const { item, detail } = this.message(messageId, edit.revision);
      if (item.state !== 'drafted') throw notAllowed('Only a draft can be edited. Withdraw the approval first.');
      const oldBody = detail.body;
      this.patchItem(item, { subject: edit.subject, preview: item.preview === item.subject ? edit.subject : item.preview, revision: item.revision + 1 });
      detail.body = edit.body;
      detail.savedBy = 'devanshu';
      detail.savedAt = this.now;
      this.setChecks(item, rescan(detail.checks, oldBody, edit));
      return detail;
    });
  }

  approve(messageId: Id, revision: Revision, o: WriteOptions): Promise<OutboxDetail> {
    return this.write(o, () => {
      const { item, detail } = this.message(messageId, revision);
      if (item.state !== 'drafted') throw notAllowed('Only a draft can be approved');
      const blocks = detail.checks.filter((c) => c.severity === 'block');
      if (blocks.length > 0) throw new ApiError('blocked', 'A check is blocking this email', blocks);
      this.patchItem(item, { state: 'approved', revision: item.revision + 1 });
      this.place(item);
      return detail;
    });
  }

  unapprove(messageId: Id, revision: Revision, o: WriteOptions): Promise<OutboxDetail> {
    return this.write(o, () => {
      const { item, detail } = this.message(messageId, revision);
      if (item.state !== 'approved') throw notAllowed(item.state === 'drafted' ? 'This email is not approved' : 'The sender has already taken this email');
      this.patchItem(item, { state: 'drafted', revision: item.revision + 1 });
      this.place(item);
      return detail;
    });
  }

  skip(messageId: Id, revision: Revision, o: WriteOptions): Promise<OutboxDetail> {
    return this.write(o, () => {
      const { item, detail } = this.message(messageId, revision);
      if (item.state !== 'drafted' && item.state !== 'approved') throw notAllowed('This email is already on its way or done');
      this.patchItem(item, { state: 'skipped', touchState: 'closed', schedule: null, nextDue: null, summary: 'Skipped by you', revision: item.revision + 1 });
      if (item.seq > 0) detail.followups = detail.followups.map((f) => (f.seq === item.seq ? { ...f, state: 'skipped' } : f));
      this.place(item);
      this.db.outbox.closedBreakdown.skipped += 1;
      return detail;
    });
  }

  /** Said no and asked to stop add a no-go row for the person. Asking to stop also counts as a stop reply today. */
  private noGoReply(person: PersonRef, companyName: string, kind: 'said_no' | 'asked_to_stop', quote: string, url: string | null): void {
    const reason = kind === 'said_no' ? 'said_no' : 'stop_reply';
    const existing = this.db.noGo.people.find((r) => r.target.kind === 'person' && r.target.person.id === person.id);
    if (reason === 'stop_reply' && existing?.reason !== 'stop_reply') this.db.outbox.sending.signals.stopRepliesToday += 1;
    if (existing) {
      existing.reason = reason;
      if (quote) existing.quote = quote;
    } else {
      this.db.noGo.people.unshift({
        kind: 'no_go', noGoId: this.nextId++, target: { kind: 'person', person: { ...person }, companyName }, reason, quote, note: '', source: 'reply',
        sourceRef: `Reply from ${person.name}`, sourceUrl: url, importSource: null, addedAt: this.now,
      });
    }
    const row = this.db.searchIndex.people.find((p) => p.id === person.id);
    if (row) row.statusLine = 'No-go';
  }

  setReplyKind(touchId: Id, kind: Exclude<ReplyKind, 'unsorted'>, revision: Revision, o: WriteOptions): Promise<OutboxDetail> {
    return this.write(o, () => {
      const d = this.db.details[touchId];
      if (!d) throw new ApiError('not_found', `No email for touch ${touchId}`);
      if (!d.reply) throw notAllowed('This email has no reply to sort');
      if (d.touchRevision !== revision) throw new ApiError('conflict', 'This reply changed since you loaded it');
      d.reply.kind = kind;
      d.touchRevision += 1;
      if (kind === 'said_no' || kind === 'asked_to_stop') {
        this.noGoReply(d.item.person, d.item.company.name, kind, (d.reply.text.split('\n\n')[1] ?? d.reply.text).trim(), d.gmailThreadUrl);
      }
      return d;
    });
  }

  setRoleKind(personId: Id, kind: RoleKind, revision: Revision, o: WriteOptions): Promise<PersonRef> {
    return this.write(o, () => {
      const hits: PersonRef[] = [];
      eachObject(this.db, (x) => { if (isPersonRef(x, personId)) hits.push(x as unknown as PersonRef); });
      const person = hits[0];
      if (!person) throw new ApiError('not_found', `No person ${personId}`);
      if (person.revision !== revision) throw new ApiError('conflict', `${person.name} changed since you loaded it`);
      const next = person.revision + 1;
      for (const p of hits) { p.roleKind = kind; p.revision = next; }
      if (kind !== 'recruiter') {
        for (const item of this.db.outbox.items.filter((i) => i.person.id === personId)) {
          const detail = this.db.details[item.touchId];
          if (detail) this.setChecks(item, detail.checks.filter((c) => c.fix?.kind !== 'role_kind'));
        }
      }
      return person;
    });
  }

  /* ---------------------------------------------------- company page actions */

  private itemsOf(companyId: Id): OutboxItem[] { return this.db.outbox.items.filter((i) => i.company.id === companyId); }

  pauseCompany(companyId: Id, revision: Revision, o: WriteOptions): Promise<void> {
    return this.write(o, () => {
      const v = this.fresh(companyId, revision);
      if (v.company.pausedAt) throw notAllowed(`${v.company.name} is already paused`);
      this.touchCompany(companyId, { pausedAt: this.now }, ['paused', 'Paused by you']);
      for (const item of this.itemsOf(companyId)) if (deriveGroup(item) !== item.group) this.place(item);
      const still = this.db.today.stillEmailing.find((s) => s.company.id === companyId);
      if (still) { still.state = 'paused'; still.nextStep = { kind: 'paused', text: 'Paused by you' }; }
    });
  }

  resumeCompany(companyId: Id, revision: Revision, o: WriteOptions): Promise<{ released: number }> {
    return this.write(o, () => {
      const v = this.fresh(companyId, revision);
      const heldBy = v.company.heldByTouchId;
      if (!v.company.pausedAt && heldBy === null) throw notAllowed(`${v.company.name} is not paused or held`);
      let released = 0;
      const holder = heldBy === null ? undefined : this.db.details[heldBy];
      if (holder) { released += holder.held.length; holder.held = []; }
      this.db.linkedin.groups.forEach((g) => { if (g.company.id === companyId) g.heldBy = null; });
      const wasPaused = this.itemsOf(companyId).filter((i) => i.group === 'paused');
      this.touchCompany(companyId, { pausedAt: null, heldByTouchId: null, held: false, heldByReply: null },
        ['resumed', 'Resumed by you']);
      for (const item of wasPaused) if (deriveGroup(item) !== 'paused') { this.place(item); released += 1; }
      const still = this.db.today.stillEmailing.find((s) => s.company.id === companyId);
      if (still && (still.state === 'held' || still.state === 'paused')) {
        still.state = 'emailing';
        if (still.nextStep.kind === 'paused') still.nextStep = { kind: 'nothing', text: 'Nothing. Follow-ups are scheduled' };
      }
      return { released };
    });
  }

  releaseHold(companyId: Id, revision: Revision, o: WriteOptions): Promise<{ released: number }> {
    return this.write(o, () => {
      const v = this.fresh(companyId, revision);
      const heldBy = v.company.heldByTouchId;
      if (heldBy === null && !v.company.held) throw notAllowed(`${v.company.name} is not held by a reply`);
      let released = 0;
      const holder = heldBy === null ? undefined : this.db.details[heldBy];
      if (holder) { released += holder.held.length; holder.held = []; }
      this.db.linkedin.groups.forEach((g) => { if (g.company.id === companyId) g.heldBy = null; });
      this.touchCompany(companyId, { heldByTouchId: null, held: false, heldByReply: null }, ['hold_released', 'Hold released by you']);
      const still = this.db.today.stillEmailing.find((s) => s.company.id === companyId);
      if (still && still.state === 'held') still.state = 'emailing';
      return { released };
    });
  }

  /** Close a company: off Today, its open drafts to Closed, plus the no-go or Done row the reason calls for. */
  private shut(v: CompanyView, reason: CloseReason, note: string, noGo: NoGoReason | null): void {
    const id = v.company.id;
    if (v.company.status === 'closed' || v.company.status === 'removed') throw notAllowed(`${v.company.name} is already ${v.company.status}`);
    const label = reason.replace(/_/g, ' ');
    this.takeEntry(id);
    this.db.today.stillEmailing = this.db.today.stillEmailing.filter((s) => s.company.id !== id);
    const outcome = reason === 'interview' || reason === 'referral' ? reason : null;
    this.touchCompany(id, { status: 'closed' }, ['closed', `Closed: ${label}`], outcome ? `Done: ${outcome === 'interview' ? 'interviewing' : 'referral'}` : noGo ? `No-go: ${noGo.replace(/_/g, ' ')}` : 'Closed');
    v.company.closed = { reason, note, at: this.now };
    for (const item of this.itemsOf(id)) {
      if (item.touchState !== 'open' || !['drafted', 'approved', 'planned'].includes(item.state)) continue;
      this.patchItem(item, { touchState: 'closed', summary: `Company closed: ${label}` });
      this.place(item);
      this.db.outbox.closedBreakdown.companyClosed += 1;
    }
    if (noGo) {
      this.db.noGo.companies.unshift({
        kind: 'no_go', noGoId: this.nextId++, target: { kind: 'company', company: refOf(v.company) }, reason: noGo, quote: '', note, source: 'devanshu', sourceRef: 'You',
        sourceUrl: null, importSource: null, addedAt: this.now,
      });
      this.countReason(noGo, 1);
    }
    if (outcome) {
      const done = this.db.done;
      done.companies.push({
        company: refOf(v.company), outcome, outcomeLabel: outcome === 'interview' ? 'Interviewing' : 'Referral', closedAt: this.now, howStarted: 'Started in this app',
        imported: false, role: v.role?.title ?? null, note,
      });
      if (outcome === 'interview') done.metrics.interviews += 1;
      else done.metrics.referrals += 1;
    }
  }

  closeCompany(companyId: Id, reason: CloseReason, note: string, revision: Revision, o: WriteOptions): Promise<void> {
    return this.write(o, () => this.shut(this.fresh(companyId, revision), reason, note, reason === 'no_sponsorship' ? 'no_sponsorship' : null));
  }

  reopenCompany(companyId: Id, revision: Revision, o: WriteOptions): Promise<void> {
    return this.write(o, () => {
      const v = this.fresh(companyId, revision);
      if (v.company.status !== 'closed') throw notAllowed(`${v.company.name} is not closed`);
      const row = this.db.noGo.companies.find((r) => r.kind === 'no_go' && r.target.kind === 'company' && r.target.company.id === companyId);
      if (row?.kind === 'no_go' && row.source !== 'devanshu') throw notAllowed('Lift the no-go entry first');
      if (row?.kind === 'no_go') {
        this.db.noGo.companies = this.db.noGo.companies.filter((r) => r !== row);
        this.countReason(row.reason, -1);
      }
      const done = this.db.done;
      const gone = done.companies.find((d) => d.company.id === companyId);
      if (gone) {
        done.companies = done.companies.filter((d) => d !== gone);
        if (gone.outcome === 'interview') done.metrics.interviews -= 1;
        else done.metrics.referrals -= 1;
      }
      this.touchCompany(companyId, { status: 'working' }, ['reopened', 'Reopened by you'], 'Reopened');
      v.company.closed = null;
      for (const item of this.itemsOf(companyId)) {
        if (item.touchState !== 'closed' || !['drafted', 'approved', 'planned'].includes(item.state)) continue;
        this.patchItem(item, { touchState: 'open', summary: 'Reopened by you' });
        this.place(item);
        this.db.outbox.closedBreakdown.companyClosed -= 1;
      }
    });
  }

  /* ---------------------------------------------------- sending */

  pauseSending(o: WriteOptions): Promise<SendingStatus> {
    return this.write(o, () => {
      const s = this.db.outbox.sending;
      if (s.state === 'running') {
        Object.assign(s, { state: 'paused_by_you', pausedReason: 'Paused by you', pausedAt: this.now, nextSend: null, noneReason: 'Sending is paused until you resume.', revision: s.revision + 1 });
      }
      return s;
    });
  }

  resumeSending(acknowledgeBlock: boolean, o: WriteOptions): Promise<SendingStatus> {
    return this.write(o, () => {
      const s = this.db.outbox.sending;
      if (s.state === 'signed_out') throw notAllowed('Gmail is signed out. Sign in from the live checkout first.');
      if (s.state === 'paused_hard_block' && !acknowledgeBlock) throw notAllowed('Gmail blocked sending. Read the message and confirm before you resume.');
      if (s.state === 'paused_by_you' || s.state === 'paused_hard_block') {
        Object.assign(s, { state: 'running', pausedReason: '', pausedAt: null, gmailMessage: null, noneReason: null, revision: s.revision + 1 });
      }
      return s;
    });
  }

  /* ---------------------------------------------------- LinkedIn */

  logLinkedIn(personId: Id, step: LinkedInStep, o: WriteOptions, replyKind?: Exclude<ReplyKind, 'unsorted' | 'auto_reply'>): Promise<LinkedInPerson> {
    return this.write(o, () => {
      const view = this.db.linkedin;
      const group = view.groups.find((g) => g.people.some((p) => p.person.id === personId));
      const p = group?.people.find((x) => x.person.id === personId);
      if (!group || !p) throw new ApiError('not_found', `No LinkedIn row for person ${personId}`);
      if (!p.steps.some((s) => s.step === step)) p.steps.push({ step, at: this.now });
      p.tab = p.steps.some((s) => s.step === 'replied') ? 'replied' : p.steps.length > 0 ? 'waiting' : 'to_reach';
      if (replyKind) p.replyKind = replyKind;
      else if (step === 'replied' && !p.replyKind) p.replyKind = 'unsorted';
      if (replyKind === 'said_no' || replyKind === 'asked_to_stop') this.noGoReply(p.person, group.company.name, replyKind, '', null);
      const all = view.groups.flatMap((g) => g.people);
      view.counts = { ...view.counts, toReach: all.filter((x) => x.tab === 'to_reach').length, waiting: all.filter((x) => x.tab === 'waiting').length, replied: all.filter((x) => x.tab === 'replied').length };
      const line = `LinkedIn: ${STEP_LINE[p.tab].toLowerCase()}`;
      const found = this.db.searchIndex.people.find((x) => x.id === personId);
      if (found && !found.statusLine.startsWith('No-go')) found.statusLine = line;
      for (const c of Object.values(this.db.companies)) for (const cp of c.people) if (cp.channel === 'linkedin' && cp.person.id === personId) cp.statusLine = STEP_LINE[p.tab];
      return p;
    });
  }

  /* ---------------------------------------------------- Needs you */

  /** Store a hand-entered fact as the current one for its kind, and mirror it on the Today row. */
  private setFact(v: CompanyView, kind: FactKind, value: string, source: string): void {
    const fact: Fact = { id: this.nextId++, fact: kind, value, low: null, high: null, confidence: 'confirmed', locked: true, sourceKind: 'hand', sourceRef: source, quote: '', observedOn: this.db.today.date };
    v.facts = [fact, ...v.facts.filter((f) => f.fact !== kind)];
    v.evidence.unshift({ ...fact });
    const slot = ({ size: 'size', domain: 'domainFact', sponsorship: 'sponsorshipFact' } as const)[kind as 'size' | 'domain' | 'sponsorship'];
    const row = [...this.db.today.proposed, ...this.db.today.backlog].find((e) => e.company.id === v.company.id);
    if (row && slot) row[slot] = { ...fact };
  }

  resolveReview(reviewId: Id, fix: ReviewFix, revision: Revision, o: WriteOptions): Promise<void> {
    return this.write(o, () => {
      // An item settles the same way from Needs you or from Data gaps.
      const gap = !this.db.needsYou.open.some((r) => r.id === reviewId);
      const open = gap ? this.db.needsYou.dataGaps : this.db.needsYou.open;
      const index = open.findIndex((r) => r.id === reviewId);
      const review = open[index];
      if (!review) throw new ApiError('not_found', `No review item ${reviewId}`);
      if (review.revision !== revision) throw new ApiError('conflict', 'This item changed since you loaded it');
      if (fix.kind === 'edit' && (fix.source.trim() === '' || fix.value.trim() === '')) throw notAllowed('An edit needs a value and a source');
      if (fix.kind === 'edit' && fix.field === 'sponsorship' && fix.value.trim().toLowerCase() === 'no') throw notAllowed('Sponsorship No is a no-go. Use Mark no-go.');
      if (fix.kind === 'merge' && (review.kind !== 'merge_candidate' || !review.merge)) throw notAllowed('Only a possible duplicate can be merged.');
      const v = this.view(review.company.id);
      const settled: SettledReview = { item: structuredClone(review), index, ...(gap ? { gap } : {}) };
      // The API's notes (outreach.review.words), so Resolved reads the same on sample data.
      let resolution: ResolvedReview['resolution'] = 'marked_ok';
      let note = review.kind === 'merge_candidate' ? 'Kept separate by you' : 'Marked ok by you';
      if (fix.kind === 'edit') {
        resolution = 'field_edited';
        note = `${EDIT_LABEL[fix.field]} set to ${fix.value.trim()} by you, from ${fix.source.trim()}`;
        if (fix.field === 'board_url') {
          if (v.board) v.board.url = fix.value;
        } else {
          this.setFact(v, fix.field, fix.value, fix.source);
          if (fix.field === 'domain') {
            v.company.domain = fix.value;
            v.mailServerOk = true;
            const row = this.db.searchIndex.companies.find((x) => x.id === v.company.id);
            if (row) row.domain = fix.value;
            for (const e of [...this.db.today.proposed, ...this.db.today.backlog]) if (e.company.id === v.company.id) { e.company.domain = fix.value; e.mailServerOk = true; }
          }
        }
      } else if (fix.kind === 'no_go') {
        settled.restoreStatus = { companyId: v.company.id, status: v.company.status };
        this.shut(v, fix.reason === 'no_sponsorship' ? 'no_sponsorship' : 'other', fix.source, fix.reason);
        const added = this.db.noGo.companies[0];
        if (added?.kind === 'no_go') settled.noGoId = added.noGoId;
        resolution = 'marked_no_go';
        note = `Added to the no-go list by you: ${NO_GO_PHRASE[fix.reason]}`;
      } else if (fix.kind === 'merge' && review.merge) {
        const keeper = fix.keep === 'a' ? review.merge.a : review.merge.b;
        const loser = fix.keep === 'a' ? review.merge.b : review.merge.a;
        const lost = this.view(loser.companyId);
        settled.restoreStatus = { companyId: loser.companyId, status: lost.company.status };
        this.touchCompany(loser.companyId, { status: 'removed' }, ['merged', `Merged into ${keeper.name}`], `Merged into ${keeper.name}`);
        resolution = 'field_edited';
        note = `Merged ${loser.name} into ${keeper.name} by you`;
      }
      if (gap) this.db.needsYou.dataGaps = open.filter((r) => r.id !== reviewId);
      else this.db.needsYou.open = open.filter((r) => r.id !== reviewId);
      const locked = fix.kind === 'edit';
      this.db.needsYou.resolvedThisWeek.unshift({ id: reviewId, kind: review.kind, companyName: review.company.name, resolution, note, at: this.now, locked, revision: revision + 1, canUndo: !locked });
      if (!locked) this.db.settled[reviewId] = settled;
      v.reviewItems = v.reviewItems.filter((r) => r.id !== reviewId);
    });
  }

  /** Undo from Resolved, after the undo window: an ok, a no-go or a merge goes back open, as the API's `reopen_item`. */
  reopenReview(reviewId: Id, revision: Revision, o: WriteOptions): Promise<void> {
    return this.write(o, () => {
      const view = this.db.needsYou;
      if ([...view.open, ...view.dataGaps].some((r) => r.id === reviewId)) throw notAllowed('This item is open. There is nothing to take back.');
      const row = view.resolvedThisWeek.find((r) => r.id === reviewId);
      if (!row) throw new ApiError('not_found', `No review item ${reviewId}`);
      if (row.revision !== revision) throw new ApiError('conflict', 'This item changed since you loaded it. Nothing was saved.');
      const settled = this.db.settled[reviewId];
      if (row.locked || !row.canUndo || !settled) throw notAllowed('A value you set is locked and cannot be taken back here.');
      if (settled.noGoId !== undefined) {
        const noGo = this.db.noGo;
        const lifted = noGo.companies.find((r) => r.kind === 'no_go' && r.noGoId === settled.noGoId);
        if (lifted?.kind === 'no_go') {
          noGo.companies = noGo.companies.filter((r) => r !== lifted);
          this.countReason(lifted.reason, -1);
        }
      }
      const restore = settled.restoreStatus;
      if (restore && this.db.companies[restore.companyId]) {
        const v = this.touchCompany(restore.companyId, { status: restore.status }, ['reopened', 'Undone by you, so it is open again'], '');
        v.company.closed = null;
      }
      const item = { ...settled.item, revision: revision + 1 };
      const back = settled.gap ? view.dataGaps : view.open;
      back.splice(Math.min(settled.index, back.length), 0, item);
      view.resolvedThisWeek = view.resolvedThisWeek.filter((r) => r.id !== reviewId);
      delete this.db.settled[reviewId];
      const company = this.db.companies[item.company.id];
      if (company) company.reviewItems.push(structuredClone(item));
    });
  }

  /* ---------------------------------------------------- No-go */

  liftNoGo(noGoId: Id, reason: string, o: WriteOptions): Promise<void> {
    return this.write(o, () => {
      const noGo = this.db.noGo;
      const row = [...noGo.companies, ...noGo.people].find((r) => r.kind === 'no_go' && r.noGoId === noGoId);
      if (!row || row.kind !== 'no_go') throw new ApiError('not_found', `No no-go entry ${noGoId}`);
      if (row.target.kind === 'person') {
        noGo.people = noGo.people.filter((r) => r !== row);
        const found = this.db.searchIndex.people.find((p) => row.target.kind === 'person' && p.id === row.target.person.id);
        if (found) found.statusLine = '';
        return;
      }
      noGo.companies = noGo.companies.filter((r) => r !== row);
      this.countReason(row.reason, -1);
      const v = this.view(row.target.company.id);
      if (v.company.status === 'closed') {
        this.touchCompany(v.company.id, { status: 'candidate' }, ['no_go_lifted', `No-go lifted: ${reason}`], 'Candidate');
        v.company.closed = null;
      }
    });
  }

  /* ---------------------------------------------------- the people plan (#33) */

  private planRow(entryId: Id): PlanRow {
    const row = this.db.plan.rows.find((r) => r.entryId === entryId);
    if (row) return row;
    // As the API: a removed company's entry still exists, and every write on it is refused.
    for (const rows of this.removedPlanRows.values()) {
      if (rows.some((r) => r.entryId === entryId)) throw notAllowed('That company was taken off the plan');
    }
    throw new ApiError('not_found', `No plan row ${entryId}`);
  }

  private freshRow(entryId: Id, revision: Revision): PlanRow {
    const row = this.planRow(entryId);
    if (row.revision !== revision) throw new ApiError('conflict', `${row.company.name} changed since you loaded it`);
    return row;
  }

  /** One more request, accept, reply, referral or interview in the week, the month and the base rates. Rates and `from` counts follow in settlePlan. */
  private countStage(stage: FunnelKey): void {
    const { progress } = this.db;
    for (const period of [progress.week, progress.month]) {
      const hit = period.stages.find((st) => st.stage === stage);
      if (hit) hit.count += 1;
    }
    if (stage === 'requests' || stage === 'accepts' || stage === 'replies') progress.base[stage] += 1;
  }

  private countEffort(label: string): void {
    const { progress } = this.db;
    const periods: ProgressPeriod[] = [progress.week, progress.month];
    for (const period of periods) {
      const hit = period.effort.find((e) => e.label === label);
      if (hit) hit.count += 1;
    }
  }

  /**
   * Log one step of a row, as the API does. The key is stored with the touch it made, so the same key later is a
   * replay that changes nothing, at any revision. A new key checks the row's revision like every other row write
   * (a stale one is 'conflict'), and a step already logged is refused.
   */
  private logStep(entryId: Id, kind: StepKind, key: string, revision: Revision, check: (row: PlanRow) => void): PlanRow {
    const row = this.planRow(entryId);
    if (this.keys.has(key)) return row;
    if (row.state === 'skipped') throw notAllowed(`${row.company.name} is carried to tomorrow`);
    const step = row.steps.find((st) => st.kind === kind);
    if (!step) throw notAllowed(kind === 'request' ? 'This row has no request to log' : 'This row has no role to apply to');
    check(row);
    if (row.revision !== revision) throw new ApiError('conflict', `${row.company.name} changed since you loaded it`);
    if (step.at !== null) throw notAllowed(kind === 'request' ? 'Already logged: the request is sent' : 'Already logged: you applied');
    step.at = this.now;
    step.touchId = this.nextId++;
    row.revision += 1;
    this.keys.set(key, step.touchId);
    if (kind === 'request') {
      this.countStage('requests');
      this.countEffort('Requests sent');
      this.db.plan.linkedinWeek.used += 1;
      this.db.plan.linkedinWeek.hardUsed += 1;
    } else {
      this.countEffort('Applications sent');
    }
    return row;
  }

  logRequest(entryId: Id, input: { personId: Id; idempotencyKey: string } | { personName: string; idempotencyKey: string }, revision: Revision, o: WriteOptions): Promise<PlanRow> {
    return this.write(o, () => this.logStep(entryId, 'request', input.idempotencyKey, revision, (row) => {
      // As the API: a company row takes the name of the person he asked; a row with a person takes its id.
      if (!row.person) {
        if (!('personName' in input) || !input.personName.trim()) throw notAllowed('Nobody is on this row. Type the name of the person you asked, then log it');
        return;
      }
      if (!('personId' in input)) throw notAllowed('This row already names its person');
      if (row.person.person.id !== input.personId) throw notAllowed('That person is not on this row');
    }));
  }

  logApplied(entryId: Id, input: { postingId: Id; idempotencyKey: string }, revision: Revision, o: WriteOptions): Promise<PlanRow> {
    return this.write(o, () => this.logStep(entryId, 'apply', input.idempotencyKey, revision, (row) => {
      if (!row.role) throw notAllowed('This row has no role to apply to');
      if (row.role.postingId !== input.postingId) throw notAllowed('That role is not on this row');
    }));
  }

  /**
   * The people he asked at a company row, as the API reads a paste: one line each, `Name | Title | note`, up to 10.
   * Each logs one request; the row's request step is done, the wave grows, and the note's dash or banned phrase comes
   * back as a warning (the note was already sent). The same key again is a replay that writes nothing.
   */
  logPeople(entryId: Id, input: { lines: string; idempotencyKey: string }, _revision: Revision, o: WriteOptions): Promise<LogPeopleResult> {
    return this.write(o, () => {
      const row = this.planRow(entryId);
      const replay = this.pastes.get(input.idempotencyKey);
      if (replay) return { row, people: replay.map((p) => ({ ...p, added: false })) };
      if (row.channel === 'email') throw notAllowed('This row is an email. Review it in the Outbox');
      if (row.person) throw notAllowed('This row already names its person');
      if (row.company.pausedAt) throw notAllowed(`${row.company.name} is paused. Resume it on its page first`);
      const lines = input.lines.split('\n').filter((l) => l.trim());
      if (lines.length === 0) throw notAllowed('Paste one line per person: Name | Title | note');
      if (lines.length > 10) throw notAllowed(`Paste at most 10 people at a time; this has ${lines.length}`);
      const people: LoggedPerson[] = lines.map((line, i) => {
        const parts = line.split('|');
        const [name = '', title = '', ...rest] = parts.length >= 4 && parts[0]?.trim().toLowerCase() === row.company.name.toLowerCase() ? parts.slice(1) : parts;
        const note = rest.join('|').trim();
        const clean = name.split(/\s+/).filter(Boolean).join(' ');
        if (!clean) throw notAllowed(`Line ${i + 1} has no name. Start it with the person's name`);
        if (note.length > 300) throw notAllowed(`A note is ${note.length} characters; LinkedIn takes 300. Paste the note exactly as you sent it`);
        const t = title.trim().toLowerCase();
        const roleKind = /recruit|talent/.test(t) ? 'recruiter' : /founder|ceo/.test(t) ? 'founder' : /manager|head of|director|cto/.test(t) ? 'manager' : /engineer|developer|staff/.test(t) ? 'engineer' : 'other';
        const warnings = /[\u2013\u2014]/.test(note) ? ['Em or en dash: Use a comma, a colon, a full stop or a plain hyphen instead.'] : [];
        return { personId: this.nextId++, name: clean, title: title.trim(), roleKind, added: true, noteChars: note.length, warnings };
      });
      const step = row.steps.find((st) => st.kind === 'request');
      if (step && step.at === null) {
        step.at = this.now;
        step.touchId = this.nextId++;
      }
      row.revision += 1;
      for (let i = 0; i < people.length; i += 1) {
        this.countStage('requests');
        this.countEffort('Requests sent');
      }
      this.db.plan.linkedinWeek.used += people.length;
      this.db.plan.linkedinWeek.hardUsed += people.length;
      const q = row.queue;
      if (q) {
        const before = q.standing.status === 'in_flight' && q.standing.text.includes('today') ? q.standing.asked : 0;
        q.standing = askedToday(before + people.length, this.addDays(this.db.plan.date, 14));
        q.peopleOnRecord += people.length;
        q.peopleLine = `${q.peopleOnRecord} ${q.peopleOnRecord === 1 ? 'person' : 'people'} on record, ${before + people.length} asked`;
        const last = people.at(-1);
        if (last) q.lastTouch = `Request to ${last.name} (${last.roleKind.charAt(0).toUpperCase()}${last.roleKind.slice(1)}) on ${this.dayText(this.db.plan.date)}`;
      }
      this.pastes.set(input.idempotencyKey, people);
      return { row, people };
    });
  }

  /** His LinkedIn people search for the row's company: the link the row opens from now on. */
  saveSearch(entryId: Id, url: string, o: WriteOptions): Promise<PlanRow> {
    return this.write(o, () => {
      const row = this.planRow(entryId);
      const clean = url.trim();
      if (!/^https:\/\/(www\.)?linkedin\.com\/search\/results\/people\/?(\?\S*)?$/.test(clean)) {
        throw notAllowed('That is not a LinkedIn people search. Copy the address of the search page; it starts with https://www.linkedin.com/search/results/people/');
      }
      if (row.queue?.peopleSearchUrl === clean && row.queue.searchSaved) return row;
      if (row.queue) row.queue = { ...row.queue, peopleSearchUrl: clean, searchSaved: true };
      if (row.findSomeone) row.findSomeone = { ...row.findSomeone, searchUrl: clean };
      row.revision += 1;
      return row;
    });
  }

  private addDays(date: string, n: number): string {
    const [y, m, d] = date.split('-').map(Number);
    return new Date(Date.UTC(y ?? 2026, (m ?? 1) - 1, (d ?? 1) + n)).toISOString().slice(0, 10);
  }

  private dayText(date: string): string {
    const [, m, d] = date.split('-').map(Number);
    return `${['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][(m ?? 1) - 1]} ${d}`;
  }

  skipRow(entryId: Id, revision: Revision, o: WriteOptions): Promise<PlanRow> {
    return this.write(o, () => {
      const row = this.freshRow(entryId, revision);
      if (row.state === 'done') throw notAllowed(`${row.company.name} is already done`);
      if (row.state === 'skipped') return row;
      row.state = 'skipped';
      row.revision += 1;
      // As the API: the skipped row carries, so tomorrow's dry run proposes it again (its person first among the people,
      // a find-someone row first among those). The room does not change.
      const t = this.db.plan.tomorrow;
      const carried: TomorrowRow = {
        companyName: row.company.name, personName: row.person?.person.name ?? null, roleTitle: row.role?.title ?? null,
        channel: row.channel, findSomeone: row.person === null,
      };
      const others = t.preview.filter((p) => p.companyName !== carried.companyName);
      const at = carried.findSomeone ? others.findIndex((p) => p.findSomeone) : 0;
      t.preview = [...others.slice(0, at < 0 ? others.length : at), carried, ...others.slice(at < 0 ? others.length : at)].slice(0, t.total);
      t.text = tomorrowText(/^\w+/.exec(t.text)?.[0] ?? 'Tomorrow', t.total, t.preview.length);
      return row;
    });
  }

  /* ---------------------------------------------------- top picks: Not now, the bench (2026-10-26) */

  /** The plan in pick order and the bench's room, as the API reads them after a pick write. */
  private settlePicks(): void {
    const plan = this.db.plan;
    plan.rows = pickOrder(plan.rows);
    this.db.bench.room = benchRoom(plan.linkedinWeek, plan.rows);
    this.db.bench.roomText = benchRoomText(this.db.bench.room);
    this.db.bench.companies.forEach((b, i) => { b.position = i + 1; });
  }

  /**
   * Not now, as the API: the row leaves today's list (the company is kept everywhere and is never a no-go), the bench's
   * first company takes its place, in one write. A row he already reached stays. The same key again answers the same.
   */
  passPick(entryId: Id, input: { reason: PassReason | null; idempotencyKey: string }, revision: Revision, o: WriteOptions): Promise<PassResult> {
    return this.write(o, () => {
      const done = this.picks.get(`pass:${input.idempotencyKey}`);
      if (done !== undefined && typeof done !== 'number') return done;
      const row = this.freshRow(entryId, revision);
      if (row.steps.some((st) => st.kind === 'request' && st.at)) throw notAllowed(`You already reached ${row.company.name} today, so it stays on the list as done`);
      const plan = this.db.plan;
      const at = plan.rows.indexOf(row);
      const next = this.db.bench.companies.shift();
      const added = next ? benchToRow(next, this.nextId++) : null;
      plan.rows.splice(at, 1, ...(added ? [added] : []));
      plan.passed += 1;
      this.settlePicks();
      const result: PassResult = {
        passedEntryId: entryId, company: { ...row.company }, added: added ? structuredClone(plan.rows.find((r) => r.entryId === added.entryId) ?? added) : null,
        text: added ? `Passed. Added ${added.company.name}.` : `Passed on ${row.company.name}. Nothing else is free to add right now.`,
      };
      this.picks.set(`pass:${input.idempotencyKey}`, result);
      return result;
    });
  }

  /** Add to today, as the API: the bench company joins after the last pick while the week has room. */
  addFromBench(companyId: Id, input: { idempotencyKey: string }, o: WriteOptions): Promise<PlanRow> {
    return this.write(o, () => {
      const done = this.picks.get(`add:${input.idempotencyKey}`);
      if (typeof done === 'number') return this.planRow(done);
      const plan = this.db.plan;
      const listed = plan.rows.find((r) => r.company.id === companyId);
      if (listed) throw notAllowed(`${listed.company.name} is already on today's list`);
      const at = this.db.bench.companies.findIndex((b) => b.company.id === companyId);
      if (at < 0) throw notAllowed('That company is not free to add today');
      if (this.db.bench.room <= 0) throw notAllowed("No room to add more this week: the 7-day limit is taken by today's picks and the requests already sent");
      const [b] = this.db.bench.companies.splice(at, 1);
      if (!b) throw notAllowed('That company is not free to add today');
      const row = benchToRow(b, this.nextId++);
      plan.rows.push(row);
      this.settlePicks();
      this.picks.set(`add:${input.idempotencyKey}`, row.entryId);
      return this.planRow(row.entryId);
    });
  }

  /** Where a touch came from, for a win's words: the prompt, a request logged from the plan, or an earlier win. */
  private touchInfo(id: Id): { person: PersonRef | null; company: CompanyRef; role: string | null; sentNote: string | null; revision: Revision } | undefined {
    const revision = this.touchRevisions.get(id) ?? 1;
    const prompted = this.db.prompt.touches.find((t) => t.touchId === id) ?? this.leftPrompt.get(id);
    if (prompted) return { person: prompted.person, company: prompted.company, role: null, sentNote: null, revision: this.touchRevisions.get(id) ?? prompted.revision };
    for (const row of this.db.plan.rows) {
      const step = row.steps.find((st) => st.touchId === id);
      if (step) return { person: step.kind === 'request' ? (row.person?.person ?? null) : null, company: refOf(row.company), role: row.role?.title ?? null, sentNote: step.kind === 'request' ? (row.note?.text ?? null) : null, revision };
    }
    const win = this.db.wins.wins.find((w) => w.touchId === id);
    if (win) return { person: win.person, company: win.company, role: win.role, sentNote: win.sentNote, revision };
    return undefined;
  }

  private winLine(kind: WinKind, person: PersonRef | null, company: string, replyKind: ReplyKind | null): string {
    const who = person ? `${person.name} at ${company}` : company;
    switch (kind) {
      case 'accepted': return `${who} accepted your request.`;
      case 'replied': return replyKind === 'interested' ? `${who} replied and wants to talk.` : replyKind === 'not_now' ? `${who} replied, but not now.` : `${who} replied.`;
      case 'referred': return `${who} referred you.`;
      case 'interview': return person ? `Interview at ${company}, set up by ${person.name}.` : `Interview at ${company}.`;
      case 'offer': return `Offer from ${company}.`;
    }
  }

  logOutcome(touchId: Id, kind: OutcomeKind, revision: Revision, o: WriteOptions, replyKind?: Exclude<ReplyKind, 'unsorted' | 'auto_reply'>): Promise<{ revision: Revision }> {
    return this.write(o, () => {
      const info = this.touchInfo(touchId);
      if (!info) throw new ApiError('not_found', `No touch ${touchId}`);
      if (info.revision !== revision) throw new ApiError('conflict', 'This touch changed since you loaded it');
      const next = revision + 1;
      this.touchRevisions.set(touchId, next);
      const prompt = this.db.prompt;
      const asked = prompt.touches.find((t) => t.touchId === touchId);
      if (asked) {
        this.leftPrompt.set(touchId, asked);
        prompt.touches = prompt.touches.filter((t) => t !== asked);
      }
      if (kind === 'quiet') return { revision: next };

      const refusal = kind === 'replied' && (replyKind === 'said_no' || replyKind === 'asked_to_stop');
      // An interview, referral or offer needs a reply first, so the reply is counted once when it was not already.
      const stages: FunnelKey[] = kind === 'accepted' ? ['accepts'] : kind === 'referred' ? ['replies', 'referrals'] : kind === 'interview' ? ['replies', 'interviews'] : ['replies'];
      const have = this.counted.get(touchId) ?? new Set<FunnelKey>();
      for (const w of this.db.wins.wins) {
        if (w.touchId !== touchId) continue;
        have.add(w.kind === 'accepted' ? 'accepts' : 'replies');
        if (w.kind === 'referred') have.add('referrals');
        if (w.kind === 'interview') have.add('interviews');
      }
      for (const stage of stages) {
        if (have.has(stage)) continue;
        have.add(stage);
        this.countStage(stage);
      }
      this.counted.set(touchId, have);

      if (refusal) {
        if (info.person) this.noGoReply(info.person, info.company.name, replyKind, '', null);
        return { revision: next };
      }
      if (this.db.wins.wins.some((w) => w.touchId === touchId && w.kind === kind)) return { revision: next };
      const kept: ReplyKind | null = kind === 'replied' ? (replyKind ?? 'unsorted') : null;
      const win: Win = {
        winId: this.nextId++, kind, at: this.now, touchId, person: info.person ? { ...info.person } : null, company: { ...info.company },
        line: this.winLine(kind, info.person, info.company.name, kept), sentNote: info.sentNote, role: info.role, replyKind: kept, unseen: true,
      };
      this.db.wins.wins.unshift(win);
      return { revision: next };
    });
  }

  private noteRow(noteId: Id): NoteDraft {
    const note = this.db.plan.rows.find((r) => r.note?.noteId === noteId)?.note;
    if (!note) throw new ApiError('not_found', `No note ${noteId}`);
    return note;
  }

  saveNote(noteId: Id, edit: { text: string; reasonLine: string; revision: Revision }, o: WriteOptions): Promise<NoteDraft> {
    return this.write(o, () => {
      const note = this.noteRow(noteId);
      if (note.revision !== edit.revision) throw new ApiError('conflict', 'This note changed since you loaded it');
      if (edit.reasonLine !== note.reasonLine) note.reasonState = 'written';
      note.text = edit.text;
      note.chars = edit.text.length;
      note.reasonLine = edit.reasonLine;
      // As the API does (#9, 2026-10-26): his note is his reason, and a fact is used when his text holds its words.
      note.needsYourLine = false;
      const low = edit.text.toLowerCase();
      note.facts = note.facts.map((f) => ({ ...f, inNote: f.text.trim() !== '' && low.includes(f.text.toLowerCase()) }));
      note.checks = rescanNote(edit.text, note.maxChars);
      note.savedBy = 'devanshu';
      note.savedAt = this.now;
      note.revision += 1;
      return note;
    });
  }

  confirmReason(noteId: Id, revision: Revision, o: WriteOptions): Promise<NoteDraft> {
    return this.write(o, () => {
      const note = this.noteRow(noteId);
      if (note.revision !== revision) throw new ApiError('conflict', 'This note changed since you loaded it');
      if (note.reasonLine.trim() === '') throw notAllowed('Write the reason line before you confirm it');
      note.reasonState = 'confirmed';
      note.needsYourLine = false;
      note.revision += 1;
      return note;
    });
  }

  markWinsSeen(winIds: Id[], o: WriteOptions): Promise<void> {
    return this.write(o, () => {
      const hits = winIds.map((id) => {
        const win = this.db.wins.wins.find((w) => w.winId === id);
        if (!win) throw new ApiError('not_found', `No win ${id}`);
        return win;
      });
      for (const win of hits) win.unseen = false;
    });
  }

  dismissWatch(itemId: Id, o: WriteOptions): Promise<void> {
    return this.write(o, () => {
      if (!this.db.watch.items.some((i) => i.id === itemId)) throw new ApiError('not_found', `No watch item ${itemId}`);
      this.db.watch.items = this.db.watch.items.filter((i) => i.id !== itemId);
    });
  }
}
