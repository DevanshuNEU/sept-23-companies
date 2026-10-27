/*
 * Sample data for Today as a company queue (#9 company-first ruling, P2): 8 companies, each with the lines the API
 * derives for a company row (outreach/api/queries/queue.py), and the In flight list under the queue. Like plan-data.ts
 * every derived line is stored here as data: the UI computes none of them.
 *
 * Numbers the tests and the e2e spec rely on (scenario `queue`, 2026-11-10): 8 rows. Top picks (2026-10-26): the 2
 * with a new opening come first, Pylon (first seen 6 hours ago) then Cerebrium (posted yesterday), so Pylon is first to
 * do; then Resend and Baseten, done (a request logged today at the company, and the application where there is a
 * role); Turso carried from Monday; 5 companies in flight with Browserbase's reply first (Resend and Baseten among
 * them, asked today). The bench holds 12: Retell AI and Railway (new early career openings; he applied to Railway on
 * Oct 20, before this role), Temporal (a new SWE II opening), then 9 by rank. Crusoe, passed on Nov 7, is not on it.
 */
import { buildProfile } from './profile-data';
import type { BenchCompany, BenchView, CompanyQueue, EmailFitView, Id, InFlightCompany, IsoDate, LevelTier, ListGroup, PlanRow, PlanStep, QueueOpening, QueueStanding } from '../types';
import type { PlanCtx } from './plan-data';

export const QUEUE_HANDOFF = 'Open the search and reach out to the people you pick. Then log who you asked on this row.';
const SEARCH = 'https://www.linkedin.com/search/results/people/';
const US = 'geoUrn=%5B%22103644278%22%5D&origin=FACETED_SEARCH';
/** The API's people search for a company with no saved search and no LinkedIn id: its name, in the United States. */
export const keywordSearch = (company: string) => `${SEARCH}?keywords=${encodeURIComponent(company)}&${US}`;
/** The one line he copies to his terminal, as the API writes it. */
export const terminalLine = (company: string, id: Id, role: string | null) => `Knock: ${company} (company ${id})${role ? `, role: ${role}` : ''}`;

/** The API's LinkedIn company search for a name (never fetched). */
export const companySearch = (company: string) => `https://www.linkedin.com/search/results/companies/?keywords=${encodeURIComponent(company)}`;
const LEVELS: Partial<Record<LevelTier, string>> = { new_grad: 'New grad role', early_career: 'Early career role', swe1: 'SWE I role', swe2: 'SWE II role' };
const EARLY = new Set<LevelTier>(['new_grad', 'early_career', 'swe1']);

/** The opening a row names, as the API words it. `fresh` is the age words when the opening is new ("6 hours ago"). */
export function openingOf(role: { postingId: Id; title: string; url: string }, tier: LevelTier, age: string, openedAt: string, fresh: string | null, intern = false, appliedNote: string | null = null): QueueOpening {
  return {
    postingId: role.postingId, title: role.title, url: role.url, level: intern ? 'Intern role' : (LEVELS[tier] ?? null), age, openedAt,
    fresh: fresh !== null, earlyFit: !intern && EARLY.has(tier), freshTag: fresh ? `New opening, ${fresh}` : null, appliedNote,
  };
}
/** As the API's `_why_today`: a new opening's level and title, an older role's level and age, else the top reason. */
export const whyTodayOf = (o: QueueOpening | null, reasons: string[]) =>
  o?.fresh ? `${o.level ?? 'Open role'}: ${o.title}` : o ? `${o.level ?? 'Open role'}, ${o.age}` : (reasons[0] ?? 'Ranked for today.');

/** The profile judge's two fields, as the API writes them: its line and fit, or "not judged yet". */
export interface JudgeSpec { fit: number; builds: string; risk: string }
const lowerFirst = (s: string) => (s.length > 1 && /[A-Z]/.test(s[0] ?? '') && /[a-z]/.test(s[1] ?? '') ? s[0]!.toLowerCase() + s.slice(1) : s);
export const judgeOf = (j?: JudgeSpec): Pick<CompanyQueue, 'judgeLine' | 'judgeFit'> =>
  j ? { judgeLine: `Judge: ${lowerFirst(j.builds)} Fit ${j.fit}. Risk: ${lowerFirst(j.risk)}`, judgeFit: j.fit } : { judgeLine: 'Judge: not judged yet.', judgeFit: null };

/* ------------------------------------------------------------------ email fit (2026-10-27) */

const NO_WEBSITE = 'No website on file, so no address pattern';
const SIZE_UNKNOWN = 'Size not on file, no sign it is large';

/**
 * The API's email fit for a fixture company (outreach/plan/email_fit.py): the blockers given, plus no website when the
 * domain is null, make it LinkedIn only; else email, with the website and the size line. A wave in flight adds its room.
 */
export function emailFitOf(domain: string | null, o: { size?: string; blockers?: string[]; standing?: QueueStanding } = {}): EmailFitView {
  const website = { text: domain, note: domain ? 'estimate, your old dashboard' : null };
  const st = o.standing;
  const wave = st?.status === 'in_flight' && st.room === 0 && st.freeOn ? [`Wave in flight: ${st.asked} asked, free again ${shortDay(st.freeOn)}`] : [];
  const blocked = [...(o.blockers ?? []), ...wave, ...(domain ? [] : [NO_WEBSITE])];
  if (blocked.length > 0) return { verdict: 'linkedin_only', label: 'LinkedIn only', reasons: blocked, website };
  const room = st?.status === 'in_flight' && st.room > 0 ? [`${st.asked} asked in this wave, room for ${st.room} more`] : [];
  return { verdict: 'email', label: 'Email', reasons: [`Website known (${domain})`, o.size ?? SIZE_UNKNOWN, ...room], website };
}

/** As the API's `email_text`: the Today header's count. */
export const emailTextOf = (rows: PlanRow[]): string => {
  const queued = rows.filter((r) => r.queue !== null);
  const n = queued.filter((r) => r.queue?.emailFit.verdict === 'email').length;
  if (queued.length === 0) return '';
  return n === 0 ? `None of today's ${queued.length} can be emailed yet` : `${n} of today's ${queued.length} can be emailed`;
};
export const emailCountOf = (rows: PlanRow[]): number => rows.filter((r) => r.queue?.emailFit.verdict === 'email').length;

/** As the API's `lane_text`. */
export const laneTextOf = (n: number): string =>
  n === 0
    ? 'No other company can be emailed today. Companies come back after their wave or with a website on file.'
    : `${n} more ${n === 1 ? 'company' : 'companies'} you can email today, by rank. Find up to 2 people at each and bring the names to your terminal.`;

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function shortDay(day: IsoDate): string {
  const [, m, d] = day.split('-').map(Number);
  return `${MONTHS[(m ?? 1) - 1]} ${d}`;
}

const NEW: QueueStanding = { status: 'new', text: 'New', reason: 'No outreach yet', asked: 0, room: 4, freeOn: null };
/** A wave he sent today: n asked, room for the rest, free again in 14 days. */
export function askedToday(n: number, freeOn: IsoDate): QueueStanding {
  const room = Math.max(0, 4 - n);
  const text = [`${n} asked today`, ...(room > 0 ? [`room for ${room} more`] : []), `free again ${shortDay(freeOn)}`].join(', ');
  return { status: 'in_flight', text, reason: 'Request sent today, no answer yet', asked: n, room, freeOn };
}

interface Spec {
  company: string; lane: ListGroup;
  role?: { title: string; tier: LevelTier; loc: string; age: number; line: string; words: string; opened: string; fresh?: string };
  reason: string; reasons: string[]; size: string | null; hiring: number; sponsorship: string;
  standing?: QueueStanding; lastTouch?: string; emailHint?: string; people: [onRecord: number, asked: number];
  saved?: string; careers: string | null; carriedFrom?: IsoDate;
  done?: [asked: number, last: string]; // asked today, and the newest person asked ("Ana Ruiz (Engineer)")
  judge?: JudgeSpec; // the profile judge's judgment; none is "not judged yet"
  applied?: string; // his application there before this role was first seen: the opening says so
  email?: { size?: string; blockers?: string[] }; // the email fit's size line and blockers (no website is read from the domain)
}

const ROWS: Spec[] = [
  {
    company: 'Resend', lane: 'level_role', role: { title: 'Software Engineer, New Grad', tier: 'new_grad', loc: 'San Francisco or remote US', age: 4, line: 'Software Engineer, New Grad, posted 4 days ago', words: 'posted 4 days ago', opened: '2026-11-06T05:00:00Z' },
    reason: 'Resend has a new grad role open and has sponsored H-1B before.', reasons: ['Open role at your level', 'Has sponsored before', 'Small company'],
    size: '38 people, Series A', hiring: 3, sponsorship: 'Has sponsored before, E-Verify enrolled', people: [4, 3], careers: 'https://resend.example/careers/new-grad', done: [3, 'Ana Ruiz (Engineer)'], email: { size: 'About 38 people: a founder or engineer reads this' },
    judge: { fit: 64, builds: 'Builds an email API for developers.', risk: 'Little agent or AI work on its pages.' },
  },
  {
    company: 'Baseten', lane: 'level_role', role: { title: 'Forward Deployed Engineer, New Grad', tier: 'new_grad', loc: 'San Francisco', age: 5, line: 'Forward Deployed Engineer, New Grad, posted 5 days ago', words: 'posted 5 days ago', opened: '2026-11-05T05:00:00Z' },
    reason: 'Baseten has a new grad forward deployed role open and has sponsored H-1B before.', reasons: ['Open role at your level', 'Has sponsored before'],
    size: '150 people, Series C', hiring: 6, sponsorship: 'Has sponsored before', people: [2, 2], careers: 'https://jobs.ashbyhq.com/baseten', done: [2, 'Kofi Mensah (Engineer)'], email: { size: 'About 150 people: a founder or engineer reads this' },
  },
  {
    company: 'Pylon', lane: 'level_role', role: { title: 'Software Engineer I', tier: 'swe1', loc: 'San Francisco', age: 0, line: 'Software Engineer I, first seen today', words: 'first seen 6 hours ago', opened: '2026-11-10T09:14:00Z', fresh: '6 hours ago' },
    reason: 'Pylon opened a Software Engineer I role this morning and says it sponsors visas.', reasons: ['Open role at your level', 'New opening today', 'Says it sponsors'],
    size: '30 people (estimate), Series A', hiring: 3, sponsorship: 'Says it sponsors, E-Verify enrolled', people: [1, 0],
    judge: { fit: 82, builds: 'Builds a support platform for B2B teams, with AI agents that answer tickets.', risk: 'No E-Verify statement on its pages.' },
    saved: `${SEARCH}?currentCompany=%5B%2281234%22%5D&geoUrn=%5B%22103644278%22%5D&titleFreeText=engineer&origin=FACETED_SEARCH`,
    careers: 'https://pylon.example/careers/swe-1', email: { size: 'About 30 people: a founder or engineer reads this' },
  },
  {
    company: 'Inngest', lane: 'level_role', role: { title: 'Software Engineer II', tier: 'swe2', loc: 'Remote US', age: 6, line: 'Software Engineer II, first seen 6 days ago', words: 'first seen 6 days ago', opened: '2026-11-04T16:00:00Z' },
    reason: 'Inngest has a Software Engineer II role open and the team is small.', reasons: ['Open role at your level', 'Small company'],
    size: '35 people (estimate)', hiring: 2, sponsorship: 'Sponsorship not stated', people: [3, 2],
    standing: { status: 'asked_before', text: '2 asked before. No answer for 20 days', reason: 'No answer for 20 days', asked: 0, room: 4, freeOn: null },
    lastTouch: 'Request to Jonah Park (Manager) on Oct 21', careers: 'https://inngest.example/careers/swe-2', email: { size: 'About 35 people: a founder or engineer reads this' },
  },
  {
    company: 'Cerebrium', lane: 'level_role', role: { title: 'Member of Technical Staff', tier: 'swe1', loc: 'New York', age: 1, line: 'Member of Technical Staff, posted 1 day ago', words: 'posted yesterday', opened: '2026-11-09T05:00:00Z', fresh: 'yesterday' },
    reason: 'Cerebrium has a Member of Technical Staff role open and no one on the team is saved yet.', reasons: ['Open role at your level', 'Small company', 'E-Verify enrolled'],
    size: '18 people (estimate), seed (guess)', hiring: 1, sponsorship: 'Sponsorship not stated, E-Verify enrolled', people: [0, 0], careers: 'https://cerebrium.example/careers/mts',
    judge: { fit: 74, builds: 'Builds serverless GPU infrastructure for AI apps.', risk: 'Unknown: its pages say nothing about the level.' },
  },
  {
    company: 'Turso', lane: 'intern_junior', role: { title: 'Software Engineering Intern', tier: 'not_stated', loc: 'Remote US', age: 4, line: 'Software Engineering Intern, posted 4 days ago', words: 'posted 4 days ago', opened: '2026-11-06T05:00:00Z' },
    reason: 'Turso posted an intern role you can do on OPT, and its founder reads every request.', reasons: ['Intern role on OPT', 'Founder led team'],
    size: '45 people (guess), seed', hiring: 1, sponsorship: 'OPT only', people: [1, 0], emailHint: 'Email works here: a founder has a published address.',
    careers: 'https://turso.example/careers/intern', carriedFrom: '2026-11-09', email: { size: 'About 45 people: a founder or engineer reads this' },
  },
  {
    company: 'Trigger.dev', lane: 'reason_no_role',
    reason: 'Trigger.dev raised a seed round in October and has no role posted. That is a reason to write.', reasons: ['Raised in the last 6 months', 'Small company'],
    size: null, hiring: 0, sponsorship: 'Sponsorship not stated', people: [0, 0], careers: 'https://trigger.example',
  },
  {
    company: 'Hex', lane: 'level_role', role: { title: 'Software Engineer, New Grad', tier: 'new_grad', loc: 'San Francisco', age: 12, line: 'Software Engineer, New Grad, posted 12 days ago', words: 'posted 12 days ago', opened: '2026-10-29T04:00:00Z' },
    reason: 'Hex has a new grad role open for a 2027 start.', reasons: ['Open role at your level', 'Unclear on sponsorship'],
    size: '180 people, Series B', hiring: 4, sponsorship: 'Unclear on sponsorship', people: [2, 0], careers: 'https://jobs.lever.co/hex', applied: 'You applied on Oct 20; this is a new role.',
    email: { blockers: ['2 people there already emailed (up to 2)'] },
  },
];

export interface QueueBuilt { rows: PlanRow[]; requests: number; applications: number }

/** The 8 company rows. `done` rows have their request logged today (and the application, with a role). */
export function buildQueueRows(c: PlanCtx, mode: 'default' | 'fresh'): QueueBuilt {
  let touchId = 2300;
  let minute = 2;
  const at = () => { minute += 3; return c.et(c.date, `09:${String(minute).padStart(2, '0')}`); };
  let requests = 0;
  let applications = 0;
  const rows = ROWS.map((spec, i): PlanRow => {
    const n = i + 1;
    const ref = c.companies.find((x) => x.name === spec.company);
    if (!ref) throw new Error(`Unknown fixture company ${spec.company}`);
    const role = spec.role ? c.role(spec.company, spec.role.title, spec.role.tier, spec.role.loc, spec.role.age) : null;
    const asked = mode === 'default' ? (spec.done?.[0] ?? 0) : 0;
    let steps: PlanStep[] = [{ kind: 'request', touchId: null, at: null }];
    if (role) steps.push({ kind: 'apply', touchId: null, at: null });
    if (asked > 0) steps = steps.map((s) => ({ kind: s.kind, touchId: touchId++, at: at() }));
    requests += asked;
    applications += steps.filter((s) => s.kind === 'apply' && s.at).length;
    const search = spec.saved ?? keywordSearch(spec.company);
    const opening = role && spec.role ? openingOf(role, spec.role.tier, spec.role.words, spec.role.opened, spec.role.fresh ?? null, spec.lane === 'intern_junior', spec.applied ?? null) : null;
    const standing = asked > 0 ? askedToday(asked, c.addDays(c.date, 14)) : { ...(spec.standing ?? NEW) };
    const queue: CompanyQueue = {
      sizeLine: spec.size, roleLine: spec.role?.line ?? null, hiring: spec.hiring,
      hiringLine: spec.hiring === 0 ? 'No new engineering roles in the last 30 days' : `${spec.hiring} new engineering ${spec.hiring === 1 ? 'role' : 'roles'} in the last 30 days`,
      sponsorshipLine: spec.sponsorship, reasons: [...spec.reasons],
      standing,
      lastTouch: asked > 0 ? `Request to ${spec.done?.[1] ?? 'someone'} on Nov 10` : (spec.lastTouch ?? null),
      emailHint: spec.emailHint ?? null,
      peopleOnRecord: spec.people[0],
      peopleLine: spec.people[0] === 0 ? 'Nobody on record yet' : `${spec.people[0]} ${spec.people[0] === 1 ? 'person' : 'people'} on record, ${(asked || spec.people[1]) === 0 ? 'none' : asked || spec.people[1]} asked`,
      peopleSearchUrl: search, searchSaved: spec.saved !== undefined, careersUrl: spec.careers,
      terminalLine: terminalLine(spec.company, ref.id, role?.title ?? null),
      whyToday: whyTodayOf(opening, spec.reasons), opening, websiteUrl: ref.domain ? `https://${ref.domain}` : null,
      linkedinCompanyUrl: companySearch(spec.company), ...judgeOf(spec.judge),
      emailFit: emailFitOf(ref.domain, { ...spec.email, standing }),
      profile: buildProfile({
        company: spec.company, size: spec.size, domain: ref.domain, standing, onRecord: spec.people[0], contacted: spec.people[1] || asked,
        opening, judge: spec.judge,
        role: role && spec.role && opening ? { title: role.title, url: role.url, level: opening.level, age: spec.role.words, location: spec.role.loc, early: opening.earlyFit } : null,
      }),
    };
    return {
      entryId: 6500 + n, position: n, channel: 'linkedin_request', lane: spec.lane,
      company: { ...c.co(spec.company), domain: ref.domain, hq: ref.hq, revision: 1 },
      person: null, role, applyUrl: role?.url ?? null, reason: spec.reason, reasonSources: [], note: null,
      findSomeone: { searchUrl: search, handoff: QUEUE_HANDOFF }, emailTouchId: null,
      steps, state: asked > 0 ? 'done' : 'todo', carriedFrom: spec.carriedFrom && asked === 0 ? spec.carriedFrom : null, revision: 1, queue,
    };
  });
  return { rows: pickOrder(rows), requests, applications };
}

/** As the API's `in_pick_order`: new early career openings first, then other new openings, each newest first, then the
 * plan's order; positions 1 up. */
export function pickOrder(rows: PlanRow[]): PlanRow[] {
  const group = (r: PlanRow) => { const o = r.queue?.opening; return !o?.fresh ? 2 : o.earlyFit ? 0 : 1; };
  const at = (r: PlanRow) => r.queue?.opening?.openedAt ?? '';
  return rows
    .map((r, i) => ({ r, i }))
    .sort((a, b) => group(a.r) - group(b.r) || (group(a.r) < 2 ? at(b.r).localeCompare(at(a.r)) : 0) || a.i - b.i)
    .map(({ r }, i) => ({ ...r, position: i + 1 }));
}

/* ------------------------------------------------------------------ the bench */

interface BenchSpec {
  company: string; score: number;
  role?: { title: string; tier: LevelTier; age: number; words: string; opened: string; fresh?: string };
  reasons: string[]; size: string | null; hiring: number; sponsorship: string; applied?: string; judge?: JudgeSpec;
  email?: { size?: string; blockers?: string[] };
}

const BENCH: BenchSpec[] = [
  { company: 'Retell AI', score: 61, role: { title: 'Software Engineer, New Grad', tier: 'new_grad', age: 0, words: 'first seen 3 hours ago', opened: '2026-11-10T12:14:00Z', fresh: '3 hours ago' },
    reasons: ['Open role at your level', 'New opening today', 'Small company'], size: '40 people (estimate), Series A', hiring: 2, sponsorship: 'Sponsorship not stated, E-Verify enrolled', email: { size: 'About 40 people: a founder or engineer reads this' },
    judge: { fit: 88, builds: 'Builds voice AI agents that answer phone calls for businesses.', risk: 'A small team that may hire senior first.' } },
  { company: 'Railway', score: 58, role: { title: 'Software Engineer, Early Career', tier: 'early_career', age: 1, words: 'posted yesterday', opened: '2026-11-09T05:00:00Z', fresh: 'yesterday' },
    reasons: ['Open role at your level', 'Has sponsored before'], size: '60 people, Series B', hiring: 3, sponsorship: 'Has sponsored before', applied: 'You applied on Oct 20; this is a new role.' },
  { company: 'Temporal', score: 55, role: { title: 'Software Engineer II', tier: 'swe2', age: 0, words: 'first seen 10 hours ago', opened: '2026-11-10T05:14:00Z', fresh: '10 hours ago' },
    reasons: ['Open role at your level', 'Has sponsored before'], size: '300 people, Series D', hiring: 5, sponsorship: 'Has sponsored before, E-Verify enrolled',
    email: { blockers: ['About 300 people: over 200, a cold email gets lost there'] } },
  { company: 'Graphite', score: 54, role: { title: 'Software Engineer, New Grad', tier: 'new_grad', age: 9, words: 'posted 9 days ago', opened: '2026-11-01T04:00:00Z' },
    reasons: ['Open role at your level', 'Small company'], size: '45 people (estimate), Series B', hiring: 1, sponsorship: 'Sponsorship not stated', email: { size: 'About 45 people: a founder or engineer reads this' } },
  { company: 'Supabase', score: 52, reasons: ['Raised in the last 6 months', 'Like your interview companies'], size: '120 people, Series C', hiring: 0, sponsorship: 'Has sponsored before' },
  { company: 'Fly.io', score: 50, role: { title: 'Platform Engineer', tier: 'swe1', age: 7, words: 'posted 7 days ago', opened: '2026-11-03T05:00:00Z' },
    reasons: ['Open role at your level'], size: '90 people (estimate)', hiring: 2, sponsorship: 'Sponsorship not stated' },
  { company: 'Weaviate', score: 49, reasons: ['Small company', 'E-Verify enrolled'], size: '70 people (estimate), Series B', hiring: 0, sponsorship: 'Sponsorship not stated, E-Verify enrolled' },
  { company: 'Clerk', score: 47, role: { title: 'Product Engineer', tier: 'open', age: 8, words: 'first seen 8 days ago', opened: '2026-11-02T15:00:00Z' },
    reasons: ['Open role, no level in the title'], size: '50 people (estimate), Series B', hiring: 1, sponsorship: 'Sponsorship not stated' },
  { company: 'Tailscale', score: 45, role: { title: 'Software Engineer, New Grad', tier: 'new_grad', age: 14, words: 'posted 14 days ago', opened: '2026-10-27T04:00:00Z' },
    reasons: ['Open role at your level', 'Has sponsored before'], size: '200 people, Series C', hiring: 2, sponsorship: 'Has sponsored before' },
  { company: 'Sourcegraph', score: 43, reasons: ['Like your interview companies'], size: '150 people (estimate)', hiring: 0, sponsorship: 'Sponsorship not stated' },
  { company: 'Render', score: 41, role: { title: 'Backend Engineer, New Grad', tier: 'new_grad', age: 10, words: 'posted 10 days ago', opened: '2026-10-31T04:00:00Z' },
    reasons: ['Open role at your level'], size: '180 people, Series C', hiring: 1, sponsorship: 'Unclear on sponsorship' },
  { company: 'PlanetScale', score: 40, reasons: ['Small company'], size: '80 people (estimate)', hiring: 0, sponsorship: 'Sponsorship not stated' },
];
/** He said Not now to these before today: they are not on the bench while they cool off. */
export const PASSED_EARLIER: { company: string; on: IsoDate; reason: string }[] = [{ company: 'Crusoe', on: '2026-11-07', reason: 'too_big' }];
export const benchRoomText = (room: number) =>
  room === 0 ? "No room to add more this week: the 7-day limit is taken by today's picks and the requests already sent." : `Room to add ${room} more ${room === 1 ? 'company' : 'companies'} this week.`;

/** As the API's `room_today`: the whole waves of 4 the 7-day limits leave, less today's picks still to do. */
export function benchRoom(week: { used: number; limit: number; hardUsed: number; hardLimit: number }, rows: PlanRow[]): number {
  const waves = Math.floor(Math.max(0, Math.min(week.limit - week.used, week.hardLimit - week.hardUsed)) / 4);
  const pending = rows.filter((r) => r.state === 'todo' && !r.steps.some((st) => st.kind === 'request' && st.at)).length;
  return Math.max(0, waves - pending);
}

export function buildBench(c: PlanCtx, room: number): BenchView {
  const companies = BENCH.map((spec, i): BenchCompany => {
    const ref = c.companies.find((x) => x.name === spec.company);
    if (!ref) throw new Error(`Unknown fixture company ${spec.company}`);
    const role = spec.role ? c.role(spec.company, spec.role.title, spec.role.tier, ref.hq, spec.role.age) : null;
    const opening = role && spec.role ? openingOf(role, spec.role.tier, spec.role.words, spec.role.opened, spec.role.fresh ?? null, false, spec.applied ?? null) : null;
    const queue: CompanyQueue = {
      sizeLine: spec.size, roleLine: role && spec.role ? `${role.title}, ${spec.role.words}` : null, hiring: spec.hiring,
      hiringLine: spec.hiring === 0 ? 'No new engineering roles in the last 30 days' : `${spec.hiring} new engineering ${spec.hiring === 1 ? 'role' : 'roles'} in the last 30 days`,
      sponsorshipLine: spec.sponsorship, reasons: [...spec.reasons], standing: { ...NEW }, lastTouch: null, emailHint: null,
      peopleOnRecord: 0, peopleLine: 'Nobody on record yet', peopleSearchUrl: keywordSearch(spec.company), searchSaved: false,
      careersUrl: role?.url ?? (ref.domain ? `https://${ref.domain}` : null), terminalLine: terminalLine(spec.company, ref.id, role?.title ?? null),
      whyToday: whyTodayOf(opening, spec.reasons), opening, websiteUrl: ref.domain ? `https://${ref.domain}` : null, linkedinCompanyUrl: companySearch(spec.company),
      ...judgeOf(spec.judge), emailFit: emailFitOf(ref.domain, spec.email),
      profile: buildProfile({
        company: spec.company, size: spec.size, domain: ref.domain, standing: { ...NEW }, onRecord: 0, contacted: 0,
        opening, judge: spec.judge,
        role: role && spec.role && opening ? { title: role.title, url: role.url, level: opening.level, age: spec.role.words, location: ref.hq, early: opening.earlyFit } : null,
      }),
    };
    return {
      company: { ...c.co(spec.company), domain: ref.domain, hq: ref.hq, revision: 1 }, position: i + 1,
      lane: spec.role ? 'level_role' : 'reason_no_role', role, queue, fresh: opening?.fresh ?? false, earlyFit: opening !== null && opening.fresh && opening.earlyFit,
    };
  });
  const emailLane = emailLaneOf(companies);
  return { asOf: c.asOf, date: c.date, companies, room, roomText: benchRoomText(room), emailLane, emailLaneText: laneTextOf(emailLane.length) };
}

/**
 * The email lane, as the API: up to 6 companies not on today's list whose fit is email, by rank (here the bench's
 * scores, highest first), positions 1 up. The API's lane may name companies the bench also shows, as here.
 */
export function emailLaneOf(companies: BenchCompany[], listed: ReadonlySet<Id> = new Set()): BenchCompany[] {
  const score = new Map(BENCH.map((b) => [b.company, b.score]));
  return companies
    .filter((b) => b.queue.emailFit.verdict === 'email' && !listed.has(b.company.id))
    .sort((a, b) => (score.get(b.company.name) ?? 0) - (score.get(a.company.name) ?? 0))
    .slice(0, 6)
    .map((b, i) => ({ ...structuredClone(b), position: i + 1 }));
}

/** A bench company as the row the API writes when it joins today's list. */
export function benchToRow(b: BenchCompany, entryId: Id): PlanRow {
  const steps: PlanStep[] = [{ kind: 'request', touchId: null, at: null }];
  if (b.role) steps.push({ kind: 'apply', touchId: null, at: null });
  return {
    entryId, position: 0, channel: 'linkedin_request', lane: b.lane, company: { ...b.company }, person: null, role: b.role,
    applyUrl: b.role?.url ?? null, reason: b.queue.reasons[0] ?? 'Ranked for today.', reasonSources: [], note: null,
    findSomeone: { searchUrl: b.queue.peopleSearchUrl, handoff: QUEUE_HANDOFF }, emailTouchId: null, steps, state: 'todo',
    carriedFrom: null, revision: 1, queue: structuredClone(b.queue),
  };
}

/** The In flight list: Browserbase's reply first, then by the day each is free again. */
export function buildInFlight(c: PlanCtx, rows: PlanRow[]): InFlightCompany[] {
  const today = rows.filter((r) => r.queue?.standing.status === 'in_flight' && r.queue.standing.freeOn !== null);
  const fixed: InFlightCompany[] = [
    { company: c.co('Warp'), status: 'in_flight', text: '4 asked 6 days ago, free again Nov 18', reason: 'Request sent 6 days ago, no answer yet', freeOn: '2026-11-18' },
    { company: c.co('Zed Industries'), status: 'in_flight', text: '2 asked 5 days ago, room for 2 more, free again Nov 19', reason: 'Request accepted 5 days ago, no answer yet', freeOn: '2026-11-19' },
  ];
  const fromRows = today.map((r): InFlightCompany => ({
    company: c.co(r.company.name), status: 'in_flight', text: r.queue?.standing.text ?? '', reason: r.queue?.standing.reason ?? '', freeOn: r.queue?.standing.freeOn ?? null,
  }));
  const replied: InFlightCompany = { company: c.co('Browserbase'), status: 'replied', text: 'Someone replied', reason: 'Someone replied', freeOn: null };
  return [replied, ...[...fixed, ...fromRows].sort((a, b) => (a.freeOn ?? '9999').localeCompare(b.freeOn ?? '9999') || a.company.name.localeCompare(b.company.name))];
}
