/*
 * Sample data for Today as a company queue (#9 company-first ruling, P2): 8 companies, each with the lines the API
 * derives for a company row (outreach/api/queries/queue.py), and the In flight list under the queue. Like plan-data.ts
 * every derived line is stored here as data: the UI computes none of them.
 *
 * Numbers the tests and the e2e spec rely on (scenario `queue`, 2026-11-10): 8 rows, the first 2 done (a request
 * logged today at the company, and the application where there is a role), Pylon first to do, Turso carried from
 * Monday, 5 companies in flight with Browserbase's reply first (Resend and Baseten among them, asked today).
 */
import type { CompanyQueue, Id, InFlightCompany, IsoDate, LevelTier, ListGroup, PlanRow, PlanStep, QueueStanding } from '../types';
import type { PlanCtx } from './plan-data';

export const QUEUE_HANDOFF = 'Open the search and reach out to the people you pick. Then log who you asked on this row.';
const SEARCH = 'https://www.linkedin.com/search/results/people/';
const US = 'geoUrn=%5B%22103644278%22%5D&origin=FACETED_SEARCH';
/** The API's people search for a company with no saved search and no LinkedIn id: its name, in the United States. */
export const keywordSearch = (company: string) => `${SEARCH}?keywords=${encodeURIComponent(company)}&${US}`;
/** The one line he copies to his terminal, as the API writes it. */
export const terminalLine = (company: string, id: Id, role: string | null) => `Knock: ${company} (company ${id})${role ? `, role: ${role}` : ''}`;

const NEW: QueueStanding = { status: 'new', text: 'New', reason: 'No outreach yet', asked: 0, room: 4, freeOn: null };
/** A wave he sent today: n asked, room for the rest, free again in 14 days. */
export function askedToday(n: number, freeOn: IsoDate): QueueStanding {
  const room = Math.max(0, 4 - n);
  const [, m, d] = freeOn.split('-').map(Number);
  const day = `${['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][(m ?? 1) - 1]} ${d}`;
  const text = [`${n} asked today`, ...(room > 0 ? [`room for ${room} more`] : []), `free again ${day}`].join(', ');
  return { status: 'in_flight', text, reason: 'Request sent today, no answer yet', asked: n, room, freeOn };
}

interface Spec {
  company: string; lane: ListGroup;
  role?: { title: string; tier: LevelTier; loc: string; age: number; line: string };
  reason: string; reasons: string[]; size: string | null; hiring: number; sponsorship: string;
  standing?: QueueStanding; lastTouch?: string; emailHint?: string; people: [onRecord: number, asked: number];
  saved?: string; careers: string | null; carriedFrom?: IsoDate;
  done?: [asked: number, last: string]; // asked today, and the newest person asked ("Ana Ruiz (Engineer)")
}

const ROWS: Spec[] = [
  {
    company: 'Resend', lane: 'level_role', role: { title: 'Software Engineer, New Grad', tier: 'new_grad', loc: 'San Francisco or remote US', age: 2, line: 'Software Engineer, New Grad, posted 2 days ago' },
    reason: 'Resend has a new grad role open and has sponsored H-1B before.', reasons: ['Open role at your level', 'Has sponsored before', 'Small company'],
    size: '38 people, Series A', hiring: 3, sponsorship: 'Has sponsored before, E-Verify enrolled', people: [4, 3], careers: 'https://resend.example/careers/new-grad', done: [3, 'Ana Ruiz (Engineer)'],
  },
  {
    company: 'Baseten', lane: 'level_role', role: { title: 'Forward Deployed Engineer, New Grad', tier: 'new_grad', loc: 'San Francisco', age: 5, line: 'Forward Deployed Engineer, New Grad, posted 5 days ago' },
    reason: 'Baseten has a new grad forward deployed role open and has sponsored H-1B before.', reasons: ['Open role at your level', 'Has sponsored before'],
    size: '150 people, Series C', hiring: 6, sponsorship: 'Has sponsored before', people: [2, 2], careers: 'https://jobs.ashbyhq.com/baseten', done: [2, 'Kofi Mensah (Engineer)'],
  },
  {
    company: 'Pylon', lane: 'level_role', role: { title: 'Software Engineer I', tier: 'swe1', loc: 'San Francisco', age: 1, line: 'Software Engineer I, posted 1 day ago' },
    reason: 'Pylon posted a Software Engineer I role yesterday and says it sponsors visas.', reasons: ['Open role at your level', 'Posted 1 day ago', 'Says it sponsors'],
    size: '30 people (estimate), Series A', hiring: 3, sponsorship: 'Says it sponsors, E-Verify enrolled', people: [1, 0],
    saved: `${SEARCH}?currentCompany=%5B%2281234%22%5D&geoUrn=%5B%22103644278%22%5D&titleFreeText=engineer&origin=FACETED_SEARCH`,
    careers: 'https://pylon.example/careers/swe-1',
  },
  {
    company: 'Inngest', lane: 'level_role', role: { title: 'Software Engineer II', tier: 'swe2', loc: 'Remote US', age: 6, line: 'Software Engineer II, first seen 6 days ago' },
    reason: 'Inngest has a Software Engineer II role open and the team is small.', reasons: ['Open role at your level', 'Small company'],
    size: '35 people (estimate)', hiring: 2, sponsorship: 'Sponsorship not stated', people: [3, 2],
    standing: { status: 'asked_before', text: '2 asked before. No answer for 20 days', reason: 'No answer for 20 days', asked: 0, room: 4, freeOn: null },
    lastTouch: 'Request to Jonah Park (Manager) on Oct 21', careers: 'https://inngest.example/careers/swe-2',
  },
  {
    company: 'Cerebrium', lane: 'level_role', role: { title: 'Member of Technical Staff', tier: 'swe1', loc: 'New York', age: 3, line: 'Member of Technical Staff, posted 3 days ago' },
    reason: 'Cerebrium has a Member of Technical Staff role open and no one on the team is saved yet.', reasons: ['Open role at your level', 'Small company', 'E-Verify enrolled'],
    size: '18 people (estimate), seed (guess)', hiring: 1, sponsorship: 'Sponsorship not stated, E-Verify enrolled', people: [0, 0], careers: 'https://cerebrium.example/careers/mts',
  },
  {
    company: 'Turso', lane: 'intern_junior', role: { title: 'Software Engineering Intern', tier: 'not_stated', loc: 'Remote US', age: 4, line: 'Software Engineering Intern, posted 4 days ago' },
    reason: 'Turso posted an intern role you can do on OPT, and its founder reads every request.', reasons: ['Intern role on OPT', 'Founder led team'],
    size: '45 people (guess), seed', hiring: 1, sponsorship: 'OPT only', people: [1, 0], emailHint: 'Email works here: a founder has a published address.',
    careers: 'https://turso.example/careers/intern', carriedFrom: '2026-11-09',
  },
  {
    company: 'Trigger.dev', lane: 'reason_no_role',
    reason: 'Trigger.dev raised a seed round in October and has no role posted. That is a reason to write.', reasons: ['Raised in the last 6 months', 'Small company'],
    size: null, hiring: 0, sponsorship: 'Sponsorship not stated', people: [0, 0], careers: 'https://trigger.example',
  },
  {
    company: 'Hex', lane: 'level_role', role: { title: 'Software Engineer, New Grad', tier: 'new_grad', loc: 'San Francisco', age: 12, line: 'Software Engineer, New Grad, posted 12 days ago' },
    reason: 'Hex has a new grad role open for a 2027 start.', reasons: ['Open role at your level', 'Unclear on sponsorship'],
    size: '180 people, Series B', hiring: 4, sponsorship: 'Unclear on sponsorship', people: [2, 0], careers: 'https://jobs.lever.co/hex',
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
    const queue: CompanyQueue = {
      sizeLine: spec.size, roleLine: spec.role?.line ?? null, hiring: spec.hiring,
      hiringLine: spec.hiring === 0 ? 'No new engineering roles in the last 30 days' : `${spec.hiring} new engineering ${spec.hiring === 1 ? 'role' : 'roles'} in the last 30 days`,
      sponsorshipLine: spec.sponsorship, reasons: [...spec.reasons],
      standing: asked > 0 ? askedToday(asked, c.addDays(c.date, 14)) : { ...(spec.standing ?? NEW) },
      lastTouch: asked > 0 ? `Request to ${spec.done?.[1] ?? 'someone'} on Nov 10` : (spec.lastTouch ?? null),
      emailHint: spec.emailHint ?? null,
      peopleOnRecord: spec.people[0],
      peopleLine: spec.people[0] === 0 ? 'Nobody on record yet' : `${spec.people[0]} ${spec.people[0] === 1 ? 'person' : 'people'} on record, ${(asked || spec.people[1]) === 0 ? 'none' : asked || spec.people[1]} asked`,
      peopleSearchUrl: search, searchSaved: spec.saved !== undefined, careersUrl: spec.careers,
      terminalLine: terminalLine(spec.company, ref.id, role?.title ?? null),
    };
    return {
      entryId: 6500 + n, position: n, channel: 'linkedin_request', lane: spec.lane,
      company: { ...c.co(spec.company), domain: ref.domain, hq: ref.hq, revision: 1 },
      person: null, role, applyUrl: role?.url ?? null, reason: spec.reason, reasonSources: [], note: null,
      findSomeone: { searchUrl: search, handoff: QUEUE_HANDOFF }, emailTouchId: null,
      steps, state: asked > 0 ? 'done' : 'todo', carriedFrom: spec.carriedFrom && asked === 0 ? spec.carriedFrom : null, revision: 1, queue,
    };
  });
  return { rows, requests, applications };
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
