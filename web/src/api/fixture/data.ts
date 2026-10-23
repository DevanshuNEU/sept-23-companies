/*
 * Sample data for phase 1. It is a stand-in for #11, not a second backend: rows are stored in the shape the
 * views read, with their derived strings (why, status lines, schedule text) as data, as the mockups show them.
 * Everything comes from docs/design/*.html. Company names are real and public, every person, email (.example),
 * number and fact is invented. The four Outbox sample bodies are copied verbatim from outbox.html.
 *
 * Conventions every view relies on:
 * - The clock is 2026-11-10T15:14:00Z (Tuesday, 10:14 in Boston). Times are UTC, dates are calendar dates.
 * - Size facts: `value` is the number as text ("38"); a `guess` is shown as "about {value}" with `low` and `high`.
 *   A company with no size source has `size: null` (Unknown).
 * - Fact confidence: confirmed = 3 bars, estimate = 2, guess = amber badge. Sponsorship facts read "Has sponsored
 *   H-1B", "Says it sponsors" or "Unclear"; a not_stated sponsorship has no fact.
 * - `OutboxItem.preview` is the second line of a row, `summary` the third; `schedule` on a draft is what would
 *   happen if it were approved now, so the UI never derives it.
 * - `ids` names the rows tests reach for; everything else is found by name in a view.
 */
import type {
  Actor, Check, CheckFix, CheckSeverity, CloseReason, CompanyEvent, CompanyPerson, CompanyRef, CompanyStatus, CompanyView, Confidence,
  DailyEntry, DoneView, DraftCompare, Fact, FactKind, Id, ImportSource, IsoDate, IsoTime, JobRunSummary, LevelTier, LinkedInGroup,
  LinkedInPerson, LinkedInReason, LinkedInStep, LinkedInView, MessageState, NeedsYouView, NoGoReason, NoGoRow, NoGoSource, NoGoView,
  OutboxCounts, OutboxDetail, OutboxGroup, OutboxItem, OutboxView, PersonRef, PostingHit, PostingRow, RemovedRow, ResolvedReview,
  ReviewItem, ReviewKind, RoleKind, RoleToName, Schedule, SearchCompany, SearchIndex, SearchPerson, SenderState, SendingStatus,
  ShellSummary, SourceKind, SponsorshipShown, StillEmailing, ThreadMessage, TodayView, TouchState,
} from '../types';

export type Scenario = 'default' | 'hard_block' | 'signed_out' | 'empty' | 'weekend' | 'job_failed' | 'error' | 'many_sent';
export const SCENARIOS: readonly Scenario[] = ['default', 'hard_block', 'signed_out', 'empty', 'weekend', 'job_failed', 'error', 'many_sent'];

export interface FixtureDb {
  scenario: Scenario;
  shell: ShellSummary;
  searchIndex: SearchIndex;
  postings: PostingHit[];
  today: TodayView;
  outbox: OutboxView;
  details: Record<Id, OutboxDetail>; // by touch id
  compares: DraftCompare[];
  linkedin: LinkedInView;
  needsYou: NeedsYouView;
  noGo: NoGoView;
  companies: Record<Id, CompanyView>;
  done: DoneView;
}

/* ------------------------------------------------------------------ time */

export const AS_OF: IsoTime = '2026-11-10T15:14:00Z';
const TODAY: IsoDate = '2026-11-10';
const TZ = 'America/New_York';
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const pad = (n: number) => String(n).padStart(2, '0');

function parts(date: IsoDate): [number, number, number] {
  const [y, m, d] = date.split('-').map(Number);
  return [y ?? 0, m ?? 1, d ?? 1];
}
function addDays(date: IsoDate, n: number): IsoDate {
  const [y, m, d] = parts(date);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}
function weekday(date: IsoDate): number {
  const [y, m, d] = parts(date);
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay();
}
function addBusinessDays(date: IsoDate, n: number): IsoDate {
  let cur = date;
  for (let left = n; left > 0; ) {
    cur = addDays(cur, 1);
    if (weekday(cur) !== 0 && weekday(cur) !== 6) left -= 1;
  }
  return cur;
}
const monthDay = (date: IsoDate) => `${MONTHS[parts(date)[1] - 1]} ${parts(date)[2]}`;
const dayName = (date: IsoDate) => `${WEEKDAYS[weekday(date)]} ${monthDay(date)}`;
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function utc(date: IsoDate, hm: string, offsetHours: number): IsoTime {
  const [y, m, d] = parts(date);
  const [h, min] = hm.split(':').map(Number);
  return new Date(Date.UTC(y, m - 1, d, (h ?? 0) + offsetHours, min ?? 0)).toISOString().replace('.000Z', 'Z');
}
/** A wall-clock time in Boston. The DST change is Nov 1 at 02:00, so Nov 1 and later is EST. */
const et = (date: IsoDate, hm: string): IsoTime => utc(date, hm, date >= '2026-11-01' ? 5 : 4);
/** A wall-clock time in Pacific time. */
const pt = (date: IsoDate, hm: string): IsoTime => utc(date, hm, date >= '2026-11-01' ? 8 : 7);
const dAgo = (n: number): IsoDate => addDays(TODAY, -n);
/** Noon in Boston, n calendar days before today. Midday keeps the Boston date the same as the UTC date. */
const noon = (n: number): IsoTime => et(dAgo(n), '12:00');

/* ------------------------------------------------------------------ ids */

export const ids = {
  touch: {
    priya: 2001, kenji: 2002, rafael: 2003, dana: 2004, marcus: 2005, omar: 2006, sara: 2007, ava: 2008,
    jordan: 2009, arjun: 2010, felix: 2011, chloe: 2012, ben: 2013, nina: 2014, theo: 2015, will: 2016,
    lena: 2020, tom: 2021,
  },
} as const;
export const messageOf = (touchId: Id): Id => touchId + 3000;

/* ------------------------------------------------------------------ companies */

interface Spec { hq: string; status: CompanyStatus; domain?: string | null; aliases?: string[]; pausedAt?: IsoTime; heldBy?: Id }
const SF = 'San Francisco';
const NY = 'New York';
const RM = 'Remote US';
const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '');
const working = (hq: string, o: Partial<Spec> = {}): Spec => ({ hq, status: 'working', ...o });
const cand = (hq: string, o: Partial<Spec> = {}): Spec => ({ hq, status: 'candidate', ...o });

const BACKLOG_NAMES = [
  'Modal', 'Neon', 'Upstash', 'Convex', 'PostHog', 'Axiom', 'Tinybird', 'Val Town', 'Dagster', 'Prefect', 'Grafbase', 'Hasura', 'Nhost',
  'Appwrite', 'Electric SQL', 'Liveblocks', 'Ably', 'Knock', 'Courier', 'Novu', 'Svix', 'WorkOS', 'Stytch', 'Frigade', 'Trunk', 'Depot',
  'Earthly', 'Dagger', 'Pulumi', 'Encore', 'Zuplo', 'Speakeasy', 'Stainless', 'Fern', 'Buildkite', 'Semaphore', 'Chroma', 'Pinecone',
  'Qdrant', 'LanceDB', 'Mem0',
];
const NO_SPONSOR_NAMES = ['Halberd Systems', 'Quillfeather', 'Ostrander Robotics', 'Tamarack Logistics', 'Veridian Ledger', 'Corvid Freight', 'Pellucid Software', 'Anvilwood Labs', 'Saltmarsh Data'];
const CITIZEN_NAMES = ['Meridian Defense Labs', 'Ironbridge Aerospace', 'Sentinel Harbor', 'Kestrel Dynamics'];
const OVER_300_NAMES = ['Larkspur Health', 'Northgate Retail Tech', 'Cobalt Freight Systems', 'Maplewood Insurance', 'Harlow Pharma Software', 'Bluestem Payments'];
const REMOVED_NAMES = ['Brightwater Analytics', 'Cinderfield Ads', 'Pennywhistle Media', 'Pinfeather Social', 'Marrow Ad Labs'];

/** Declaration order fixes every company id: 500 plus the position, except Browserbase and Resend. */
const SPECS: Record<string, Spec> = {
  Browserbase: working(SF, { heldBy: ids.touch.priya }),
  Mintlify: working(SF),
  Graphite: working(NY, { domain: 'graphite.example' }),
  Supabase: working(RM),
  Cartesia: working(SF, { pausedAt: et('2026-11-09', '14:20') }),
  Pylon: cand(SF),
  Resend: working(SF),
  Cerebrium: working(NY, { domain: null }),
  'Trigger.dev': working(RM, { domain: 'trigger.example' }),
  Baseten: cand(SF),
  Inngest: cand(RM),
  Turso: cand(RM),
  Hex: cand(SF),
  Warp: cand(NY),
  'Zed Industries': cand(RM),
  'Fly.io': cand(RM),
  Railway: cand(SF),
  Weaviate: cand(RM),
  Clerk: cand(SF),
  Tailscale: cand(RM),
  Sourcegraph: cand(RM),
  Temporal: cand('Seattle'),
  Render: cand(SF),
  PlanetScale: cand(SF),
  Crusoe: { hq: SF, status: 'closed' },
  'Retell AI': { hq: SF, status: 'closed' },
  'Distyl AI': { hq: SF, status: 'closed' },
  'Northbeam Labs': { hq: NY, status: 'closed' },
};
BACKLOG_NAMES.forEach((name, i) => { SPECS[name] = { hq: [SF, RM, NY][i % 3] ?? SF, status: 'backlog' }; });
[...NO_SPONSOR_NAMES, ...CITIZEN_NAMES, ...OVER_300_NAMES].forEach((name, i) => { SPECS[name] = { hq: [NY, 'Boston', 'Austin'][i % 3] ?? NY, status: 'closed' }; });
REMOVED_NAMES.forEach((name) => { SPECS[name] = { hq: NY, status: 'removed' }; });

const COMPANY_ORDER = Object.keys(SPECS);
export function companyId(name: string): Id {
  if (name === 'Browserbase') return 288;
  if (name === 'Resend') return 412;
  const at = COMPANY_ORDER.indexOf(name);
  if (at < 0) throw new Error(`Unknown fixture company ${name}`);
  return 500 + at;
}
const domainOf = (name: string): string | null => {
  const s = SPECS[name];
  if (!s) throw new Error(`Unknown fixture company ${name}`);
  return s.domain === undefined ? `${slug(name)}.example` : s.domain;
};

/* ------------------------------------------------------------------ context: registries shared by the builders */

interface RoleOpts { years?: number | null; start2027?: boolean; open?: boolean }
interface Ctx {
  next(): number;
  spec(name: string): Spec;
  co(name: string): CompanyRef;
  person(name: string, title: string, kind: RoleKind, company: string, o?: { email?: string | null; linkedin?: boolean }): PersonRef;
  role(company: string, title: string, tier: LevelTier, loc: string, age: number, o?: RoleOpts): RoleToName;
  fact(kind: FactKind, value: string, conf: Confidence, src: SourceKind, ref: string, o?: Partial<Fact>): Fact;
  people: { person: PersonRef; companyName: string }[];
  postings: PostingHit[];
}

function makeCtx(): Ctx {
  let counter = 100;
  let personId = 7001;
  let postingId = 8001;
  const people: Ctx['people'] = [];
  const postings: PostingHit[] = [];
  const roles = new Map<string, RoleToName>();
  const known = new Map<string, PersonRef>();
  const spec = (name: string): Spec => {
    const s = SPECS[name];
    if (!s) throw new Error(`Unknown fixture company ${name}`);
    return s;
  };
  return {
    next: () => counter++,
    spec,
    co: (name) => {
      const s = spec(name);
      return { id: companyId(name), name, status: s.status, pausedAt: s.pausedAt ?? null, heldByTouchId: s.heldBy ?? null };
    },
    person: (name, title, kind, company, o = {}) => {
      const key = `${company}|${name}`;
      const had = known.get(key);
      if (had) return { ...had };
      const domain = domainOf(company);
      const first = slug(name.split(' ')[0] ?? name);
      const email = o.email !== undefined ? o.email : domain ? `${first}@${domain}` : null;
      const p: PersonRef = {
        id: personId++, name, title, roleKind: kind, email,
        linkedinUrl: o.linkedin === false ? null : `https://linkedin.example/in/${name.toLowerCase().replace(/[^a-z]+/g, '-')}`,
        revision: 1,
      };
      known.set(key, p);
      people.push({ person: p, companyName: company });
      return { ...p };
    },
    role: (company, title, tier, loc, age, o = {}) => {
      const key = `${company}|${title}`;
      const had = roles.get(key);
      if (had) return { ...had };
      const r: RoleToName = {
        postingId: postingId++, title, url: `https://careers.example/${slug(company)}/${postingId - 1}`, location: loc,
        firstSeenAt: noon(age), levelTier: tier, yearsAsked: o.years ?? null, start2027: o.start2027 ?? false,
      };
      roles.set(key, r);
      postings.push({ postingId: r.postingId, title, companyId: companyId(company), companyName: company, open: o.open ?? true });
      return { ...r };
    },
    fact: (kind, value, confidence, sourceKind, sourceRef, o = {}) => ({
      id: counter++, fact: kind, value, low: null, high: null, confidence, locked: false, sourceKind, sourceRef, quote: '', observedOn: null, ...o,
    }),
    people,
    postings,
  };
}

/* ------------------------------------------------------------------ jobs */

const JOBS = {
  crawl: (): JobRunSummary => ({
    job: 'crawl', startedAt: et('2026-11-09', '08:04'), finishedAt: et('2026-11-09', '08:19'), status: 'ok', error: '',
    counts: [{ label: 'Boards read', value: 871 }, { label: 'Empty', value: 1 }, { label: 'Errors', value: 1 }],
    expectedNextAt: et('2026-11-12', '08:00'), late: false,
  }),
  daily_list: (): JobRunSummary => ({
    job: 'daily_list', startedAt: et(TODAY, '08:00'), finishedAt: et(TODAY, '08:01'), status: 'ok', error: '',
    counts: [{ label: 'Companies considered', value: 1010 }, { label: 'Proposed', value: 18 }, { label: 'Backlog', value: 41 }],
    expectedNextAt: et('2026-11-11', '08:00'), late: false,
  }),
  sender: (): JobRunSummary => ({
    job: 'sender', startedAt: et(TODAY, '10:10'), finishedAt: et(TODAY, '10:11'), status: 'ok', error: '',
    counts: [{ label: 'Sent', value: 1 }, { label: 'Checked', value: 1 }],
    expectedNextAt: et(TODAY, '10:20'), late: false,
  }),
  import: (): JobRunSummary => ({
    job: 'import', startedAt: et('2026-10-18', '12:00'), finishedAt: et('2026-10-18', '12:06'), status: 'ok', error: '',
    counts: [{ label: 'Companies', value: 212 }, { label: 'People', value: 388 }, { label: 'Events', value: 1204 }],
    expectedNextAt: null, late: false,
  }),
  rebuild: (): JobRunSummary => ({
    job: 'rebuild', startedAt: et('2026-11-01', '07:00'), finishedAt: et('2026-11-01', '07:12'), status: 'ok', error: '',
    counts: [{ label: 'Postings classified', value: 3190 }],
    expectedNextAt: et('2026-11-08', '07:00'), late: true,
  }),
};
const jobList = (): JobRunSummary[] => [JOBS.crawl(), JOBS.daily_list(), JOBS.sender(), JOBS.import(), JOBS.rebuild()];

/* ------------------------------------------------------------------ Today (today.html) */

interface TodayRow {
  name: string; role: string; tier: LevelTier; age: number; size: number | null; sponsor: SponsorshipShown;
  loc?: string; years?: number; start2027?: boolean; sizeConf?: Confidence; picked?: boolean; people?: number; drafts?: number;
  others?: [string, number][];
}
const LEVEL_ROLE: TodayRow[] = [
  { name: 'Pylon', role: 'Software Engineer, New Grad', tier: 'new_grad', age: 1, size: 30, sponsor: 'says_yes' },
  { name: 'Resend', role: 'Software Engineer, New Grad', tier: 'new_grad', age: 2, size: 38, sizeConf: 'confirmed', sponsor: 'has_sponsored', loc: 'San Francisco or remote US', picked: true, people: 2, drafts: 2, others: [['Software Engineer, Infrastructure', 11], ['Developer Advocate', 30]] },
  { name: 'Cerebrium', role: 'Member of Technical Staff', tier: 'swe1', age: 3, size: 18, sponsor: 'not_stated', picked: true },
  { name: 'Trigger.dev', role: 'Software Engineer I', tier: 'swe1', age: 4, size: null, sponsor: 'not_stated', picked: true, people: 2, drafts: 2 },
  { name: 'Baseten', role: 'Forward Deployed Engineer, New Grad', tier: 'new_grad', age: 5, size: 150, sizeConf: 'confirmed', sponsor: 'has_sponsored' },
  { name: 'Inngest', role: 'Software Engineer II', tier: 'swe2', age: 6, size: 35, sponsor: 'not_stated', years: 2 },
  { name: 'Turso', role: 'Software Engineer, New Grad', tier: 'new_grad', age: 9, size: 45, sizeConf: 'guess', sponsor: 'not_stated' },
  { name: 'Hex', role: 'Software Engineer, New Grad', tier: 'new_grad', age: 12, size: 180, sizeConf: 'confirmed', sponsor: 'unclear', start2027: true },
  { name: 'Warp', role: 'Software Engineer I', tier: 'swe1', age: 15, size: 90, sponsor: 'has_sponsored' },
  { name: 'Zed Industries', role: 'Software Engineer, Early Career', tier: 'early_career', age: 20, size: 40, sponsor: 'not_stated' },
  { name: 'Fly.io', role: 'Software Engineer II', tier: 'swe2', age: 27, size: 120, sponsor: 'has_sponsored' },
];
const ANY_ROLE: TodayRow[] = [
  { name: 'Railway', role: 'Software Engineer, Platform', tier: 'not_stated', age: 3, size: 70, sponsor: 'not_stated' },
  { name: 'Weaviate', role: 'Software Engineer, Go', tier: 'not_stated', age: 6, size: 140, sponsor: 'has_sponsored' },
  { name: 'Clerk', role: 'Product Engineer', tier: 'not_stated', age: 8, size: 110, sponsor: 'not_stated' },
  { name: 'Tailscale', role: 'Software Engineer, Networking', tier: 'not_stated', age: 10, size: 210, sponsor: 'not_stated' },
  { name: 'Sourcegraph', role: 'Software Engineer, Search', tier: 'not_stated', age: 11, size: 190, sponsor: 'has_sponsored' },
  { name: 'Temporal', role: 'Software Engineer, Workflows', tier: 'not_stated', age: 13, size: 240, sponsor: 'not_stated' },
  { name: 'Render', role: 'Software Engineer, Infrastructure', tier: 'not_stated', age: 14, size: 160, sponsor: 'not_stated' },
];
const STAGES = ['Series A', 'Seed', 'Series B', 'Series A', 'Seed', 'Series C'];
const TIER_WORDS: Record<LevelTier, string> = {
  new_grad: 'A new grad', early_career: 'An early career', swe1: 'A SWE I', swe2: 'A SWE II', open: 'An open engineering',
  not_stated: 'An open engineering', senior: 'A senior',
};
const SPONSOR_WORDS: Partial<Record<SponsorshipShown, string>> = {
  has_sponsored: ' that has sponsored H-1B before', says_yes: ' that says it sponsors', unclear: ' that is unclear on sponsorship',
};

function sponsorshipFact(c: Ctx, kind: SponsorshipShown, age: number): Fact | null {
  if (kind === 'has_sponsored') return c.fact('sponsorship', 'Has sponsored H-1B', 'confirmed', 'lca', '2 DOL filings in 2025', { observedOn: dAgo(30) });
  if (kind === 'says_yes') return c.fact('sponsorship', 'Says it sponsors', 'confirmed', 'posting', 'New grad posting', { quote: 'We sponsor visas for this role.', observedOn: dAgo(age) });
  if (kind === 'unclear') {
    return c.fact('sponsorship', 'Unclear', 'estimate', 'posting', 'Software Engineer, New Grad posting', {
      quote: 'We may be able to support visa sponsorship for exceptional candidates.', observedOn: '2026-10-29',
    });
  }
  return null;
}

function sizeFact(c: Ctx, r: { size: number | null; sizeConf?: Confidence }): Fact | null {
  if (r.size === null) return null;
  const guess = r.sizeConf === 'guess';
  return c.fact('size', String(r.size), r.sizeConf ?? 'estimate', 'linkedin', guess ? 'LinkedIn 11 to 50 band' : 'LinkedIn and the careers page', {
    observedOn: dAgo(8), ...(guess ? { low: 11, high: 50, quote: '11 to 50 employees' } : {}),
  });
}

function dailyEntry(c: Ctx, r: TodayRow, rank: number, group: DailyEntry['listGroup'], state: DailyEntry['state'], proposedDaysAgo = 0): DailyEntry {
  const s = c.spec(r.name);
  const domain = domainOf(r.name);
  const role = c.role(r.name, r.role, r.tier, r.loc ?? s.hq, r.age, { years: r.years, start2027: r.start2027 });
  const ago = r.age === 1 ? '1 day ago' : `${r.age} days ago`;
  const sizePhrase = r.size === null ? 'unknown size' : `about ${r.size}`;
  const noDomain = domain === null;
  return {
    company: { ...c.co(r.name), domain, hq: s.hq },
    listGroup: group, rank, state, revision: 1,
    role, size: sizeFact(c, r),
    stage: c.fact('stage', STAGES[rank % STAGES.length] ?? 'Seed', 'estimate', 'website', 'Company blog', { observedOn: dAgo(20) }),
    sponsorship: r.sponsor, sponsorshipFact: sponsorshipFact(c, r.sponsor, r.age),
    domainFact: noDomain ? null : c.fact('domain', domain, 'confirmed', 'website', 'Mail server check', { observedOn: TODAY }),
    mailServerOk: noDomain ? null : true,
    why: `${TIER_WORDS[r.tier]} role posted ${ago}, at a company of ${sizePhrase}${SPONSOR_WORDS[r.sponsor] ?? ''}.`,
    otherRoles: (r.others ?? []).map(([title, age]) => ({ title, firstSeenAt: noon(age) })),
    peopleCount: r.people ?? 0, draftCount: r.drafts ?? 0, firstProposedOn: dAgo(proposedDaysAgo),
  };
}

const BACKLOG_ROLES: [string, LevelTier][] = [
  ['Software Engineer, New Grad', 'new_grad'], ['Software Engineer I', 'swe1'], ['Software Engineer, Backend', 'not_stated'],
  ['Software Engineer II', 'swe2'], ['Software Engineer, Early Career', 'early_career'], ['Product Engineer', 'not_stated'],
];
const BACKLOG_SPONSOR: SponsorshipShown[] = ['not_stated', 'has_sponsored', 'not_stated', 'says_yes', 'not_stated', 'unclear'];

function buildToday(c: Ctx): TodayView {
  const proposed = [
    ...LEVEL_ROLE.map((r, i) => dailyEntry(c, r, i + 1, 'level_role', r.picked ? 'picked' : 'proposed')),
    ...ANY_ROLE.map((r, i) => dailyEntry(c, r, i + 12, 'any_us_role', 'proposed')),
  ];
  const backlog = BACKLOG_NAMES.map((name, i) => {
    const [role, tier] = BACKLOG_ROLES[i % BACKLOG_ROLES.length] ?? BACKLOG_ROLES[0]!;
    const row: TodayRow = { name, role, tier, age: 14 + i, size: 20 + ((i * 17) % 160), sponsor: BACKLOG_SPONSOR[i % BACKLOG_SPONSOR.length] ?? 'not_stated' };
    return dailyEntry(c, row, i + 1, tier === 'not_stated' ? 'any_us_role' : 'level_role', 'backlog', 1 + (i % 9));
  });
  const still = (name: string, day: number, emailed: number, budget: number, kind: StillEmailing['nextStep']['kind'], text: string, state: StillEmailing['state']): StillEmailing => ({
    company: c.co(name), day, days: 3, emailed, budget, nextStep: { kind, text }, state,
  });
  return {
    asOf: AS_OF, date: TODAY, builtAt: et(TODAY, '08:01'), capacity: 5, picked: 3, notReady: null,
    proposed, backlog,
    stillEmailing: [
      still('Browserbase', 3, 6, 7, 'answer_reply', 'Answer Priya Natarajan in Gmail', 'held'),
      still('Mintlify', 2, 5, 8, 'review_drafts', '1 draft and 1 follow-up to review. Recruiter goes tomorrow', 'emailing'),
      still('Graphite', 2, 4, 7, 'fix_review', '1 blocked draft: fix the email domain', 'emailing'),
      still('Supabase', 3, 7, 7, 'nothing', 'Nothing. Follow-ups are scheduled', 'waiting'),
      still('Cartesia', 1, 2, 6, 'paused', "Paused by you: waiting on a friend's intro", 'paused'),
    ],
    listJob: JOBS.daily_list(),
  };
}

/* ------------------------------------------------------------------ Outbox (outbox.html) */

export const GROUP_INDEX: Record<OutboxGroup, number> = { replied: 0, review: 1, approved: 2, sending: 3, checking: 4, sent: 5, paused: 6, closed: 7 };
const ME = 'Devanshu Chicholikar';
const SIGN = `Thanks,\n${ME}`;
const LINKS = 'https://www.devanshuchicholikar.com\nhttps://opencodeintel.com';

const BODY_KENJI = `Hi Kenji,

Your post on retrying long-running tasks without doing the work twice is the problem I keep coming back to. At Pitney Bowes I built idempotent REST endpoints that removed 40% of manual data entry.

I graduated from Northeastern in May 2026, and I want in at Trigger. Lately I've built OpenCodeIntel, code search for AI coding agents with a 12-tool MCP server, at 94% Hit@1 on a 665-query research eval.

Would you refer me for Software Engineer I, or point me to whoever owns that req?

${LINKS}

${SIGN}`;

const BODY_AVA = `Hi Ava,

Your post on durable queues changed how I think about job state. At Pitney Bowes I built idempotent REST endpoints that removed 40% of manual data entry.

I graduated from Northeastern in May 2026, and I want in at Trigger. Lately I've built OpenCodeIntel, code search for AI coding agents with a 12-tool MCP server, at 94% Hit@1 on a 665-query research eval.

Would you refer me for Software Engineer I, or point me to whoever owns that req?

${LINKS}

${SIGN}`;

const BODY_DANA = `Hi Dana,

I'm applying for Software Engineer, New Grad (req 4512) at Mintlify. I'm on F-1 OPT, authorized to work now, and eligible for STEM OPT through 2029. I can start right away.

I graduated from Northeastern in May 2026 and spent two years as a software engineer at Jaksh Enterprise, where I cut quote-page p95 latency 65%. I also built OpenCodeIntel, code search for AI coding agents.

Could we do a short screen on Thursday at 11 ET or Friday at 2 ET?

${LINKS}

${SIGN}`;

const BODY_MARCUS = `Hi Marcus,

One new thing since my last note: I wrote up how a reranker first made my code search worse, then better. It's close to how Mintlify thinks about docs that agents read.

https://www.devanshuchicholikar.com/writing/rerankers-code-search

If referrals aren't something you do, the name of the person who owns the new grad req would help just as much.

${SIGN}`;

const BODY_OMAR = `Hi Omar,

Your write-up last week on how the batch API paces sends under rate limits answered a question I had about sending email at a steady rate.

I graduated from Northeastern in May 2026, and I want in at Resend as a Software Engineer, New Grad. I build developer tools: Saar, my Chrome extension on the Chrome Web Store, tracks Claude token usage in real time and ships with 1,808 Vitest tests.

Could I pick one small issue in your public repo and send a pull request, so you can judge the work first?

https://www.devanshuchicholikar.com
https://getsaar.com

${SIGN}`;

const defaultBody = (first: string, company: string, subject: string): string =>
  `Hi ${first},\n\n${cap(subject.replace(/^Re: /, ''))} is the kind of work I keep coming back to. I graduated from Northeastern in May 2026 and I want to work on this at ${company}.\n\nCould you refer me for the open role, or point me to whoever owns it?\n\n${LINKS}\n\n${SIGN}`;

const ck = (code: string, severity: CheckSeverity, title: string, detail = '', fix: CheckFix | null = null): Check => ({ code, severity, title, detail, fix, spans: [] });
const passes = (titles: string[]): Check[] => titles.map((t) => ck(`pass_${slug(t)}`, 'pass', t));
const CLEAN_TITLES = [
  'Mail server found', '2 links, portfolio first, no shortener, no attachment', 'Not on the no-go or said-no list', 'Company not closed for no sponsorship',
  'No banned phrases', 'No em or en dashes', 'Word count in range (80 to 120)', 'Hook is recent (30 days at most)', 'Subject unique to them, no "Re:"', 'Plain text',
];
const clean = (): Check[] => passes(CLEAN_TITLES);

const sched = (earliestAt: IsoTime | null, reason: Schedule['reason'], text: string, sent: number, left: number): Schedule =>
  ({ earliestAt, tz: TZ, reason, text, companySentToday: sent, companySlotsLeft: left });

/** Follow-up due dates for a first email sent on `firstDate` (3, 7 and 15 business days). Due on or before today means sent. */
function track(firstDate: IsoDate) {
  const followups = ([3, 7, 15] as const).map((n, i) => {
    const dueOn = addBusinessDays(firstDate, n);
    const sent = dueOn <= TODAY;
    return { seq: (i + 1) as 1 | 2 | 3, dueOn, sentAt: sent ? et(dueOn, '09:30') : null, state: (sent ? 'sent' : 'planned') as MessageState };
  });
  const last = [...followups].reverse().find((f) => f.sentAt);
  const next = followups.find((f) => !f.sentAt);
  const nextText = next ? `Follow-up ${next.seq} due ${dayName(next.dueOn)}` : 'No more follow-ups';
  return {
    followups, seq: (last?.seq ?? 0) as 0 | 1 | 2 | 3,
    nextDue: next ? { seq: next.seq, on: next.dueOn } : null,
    summary: last ? `Follow-up ${last.seq} sent ${monthDay(last.dueOn)}. ${nextText}` : nextText,
  };
}

interface ItemSpec {
  touch: number; person: PersonRef; company: string; group: OutboxGroup; state: MessageState; touchState?: TouchState; seq?: 0 | 1 | 2 | 3;
  subject: string; preview?: string; summary: string; at: IsoTime; schedule?: Schedule | null; nextDue?: OutboxItem['nextDue'];
  checks?: Check[]; body?: string; d?: Partial<OutboxDetail>;
}

function buildItem(c: Ctx, s: ItemSpec, rank: number): { item: OutboxItem; detail: OutboxDetail } {
  const checks = s.checks ?? [];
  const blockCount = checks.filter((k) => k.severity === 'block').length;
  const warnCount = checks.filter((k) => k.severity === 'warn').length;
  const item: OutboxItem = {
    touchId: s.touch, messageId: messageOf(s.touch), seq: s.seq ?? 0, state: s.state, touchState: s.touchState ?? 'open', group: s.group,
    sortKey: `${GROUP_INDEX[s.group]}.${String(rank).padStart(5, '0')}`, person: s.person, company: c.co(s.company),
    subject: s.subject, preview: s.preview ?? s.subject, summary: s.summary,
    worst: checks.length === 0 ? null : blockCount ? 'block' : warnCount ? 'warn' : 'pass', blockCount, warnCount,
    at: s.at, schedule: s.schedule ?? null, nextDue: s.nextDue ?? null, revision: 1,
  };
  const first = s.person.name.split(' ')[0] ?? s.person.name;
  const detail: OutboxDetail = {
    asOf: AS_OF, item: { ...item }, body: s.body ?? defaultBody(first, s.company, s.subject),
    hook: { text: cap(s.subject.replace(/^Re: /, '')), on: null }, project: 'OpenCodeIntel', role: null, checks, savedBy: 'claude', savedAt: s.at,
    followups: [], firstSentAt: null, gmailThreadUrl: null, reply: null, touchRevision: 1, held: [], thread: [], bounceReason: null, ...s.d,
  };
  return { item, detail };
}

const out = (seq: 0 | 1 | 2 | 3, subject: string, body: string, at: IsoTime): ThreadMessage => ({ seq, direction: 'out', subject, body, at, from: ME });

const FIRSTS = ['Aiko', 'Bram', 'Calla', 'Dov', 'Elio', 'Fenna', 'Gideon', 'Hana', 'Ivo', 'Jaya', 'Kasia', 'Leif', 'Mireille', 'Nico', 'Odalys', 'Pavel', 'Quinn', 'Rahel', 'Soren', 'Talia', 'Uriel', 'Vesna', 'Wren', 'Xavi', 'Yara', 'Zane', 'Alder', 'Bexley', 'Cosima', 'Dashiell', 'Eluned', 'Florian', 'Greer', 'Halina', 'Idris', 'Jocasta', 'Kendrick'];
const LASTS = ['Aldous', 'Brannigan', 'Castellan', 'Delacroix', 'Esterhazy', 'Fairweather', 'Gallo', 'Hartigan', 'Imbrie', 'Jessup', 'Kowalczyk', 'Marchetti', 'Novak', 'Okafor', 'Pellegrino', 'Quigley', 'Rasmussen', 'Sandoval', 'Thackeray', 'Underhill', 'Vasquez', 'Whitlock', 'Yamashita', 'Zielinski', 'Ashdown', 'Blackwood', 'Calloway', 'Dunmore', 'Everhart', 'Fontaine', 'Grimaldi'];
const GEN_COMPANIES = ['Mintlify', 'Supabase', 'Graphite', 'Resend', 'Trigger.dev', 'Baseten', 'Pylon', 'Inngest', 'Warp'];
const GEN_TITLES: [string, RoleKind][] = [['Software Engineer', 'engineer'], ['Staff Engineer', 'engineer'], ['Engineering Manager', 'manager'], ['Founding Engineer', 'founder'], ['Senior Software Engineer', 'engineer']];
const TOPICS = ['type-safe webhooks', 'queue backpressure', 'schema migrations', 'edge caching', 'rate limit design', 'retries and idempotency', 'log sampling', 'vector search latency', 'feature flag rollouts', 'CLI ergonomics', 'docs search', 'incident reviews', 'background jobs', 'pricing page experiments', 'SDK generation', 'connection pooling', 'observability costs', 'browser automation', 'cold start times', 'typed SQL'];

function prevBusinessDay(date: IsoDate, n: number): IsoDate {
  let cur = date;
  for (let left = n; left > 0; ) {
    cur = addDays(cur, -1);
    if (weekday(cur) !== 0 && weekday(cur) !== 6) left -= 1;
  }
  return cur;
}

/** Deterministic invented sent rows: the first 19 went out today, the rest on earlier business days, newest first. */
function generatedSent(c: Ctx, count: number, startTouch: Id): ItemSpec[] {
  const specs: ItemSpec[] = [];
  for (let i = 0; i < count; i++) {
    const company = GEN_COMPANIES[i % GEN_COMPANIES.length] ?? 'Mintlify';
    const [title, kind] = GEN_TITLES[i % GEN_TITLES.length] ?? GEN_TITLES[0]!;
    const person = c.person(`${FIRSTS[i % FIRSTS.length]} ${LASTS[(i * 7 + 3) % LASTS.length]}`, title, kind, company);
    const topic = TOPICS[i % TOPICS.length] ?? 'queues';
    const subject = `your ${i % 2 ? 'talk' : 'post'} on ${topic}`;
    const date = i < 19 ? TODAY : prevBusinessDay(TODAY, 1 + Math.floor((i - 19) / 6));
    const hm = i < 19 ? `${pad(Math.floor((9 * 60 + 38 - 3 * i) / 60))}:${pad((9 * 60 + 38 - 3 * i) % 60)}` : `${pad(9 + ((i - 19) % 6))}:${pad((i * 11) % 60)}`;
    const at = et(date, hm);
    const t = track(date);
    specs.push({
      touch: startTouch + i, person, company, group: 'sent', state: 'sent', seq: t.seq, subject, summary: t.summary, at, nextDue: t.nextDue,
      d: { followups: t.followups, firstSentAt: at, gmailThreadUrl: `https://mail.google.com/mail/u/0/#sent/thread${startTouch + i}`, thread: [out(0, subject, defaultBody(person.name.split(' ')[0] ?? '', company, subject), at)] },
    });
  }
  return specs;
}

function buildOutbox(c: Ctx, sentTotal: number): { view: OutboxView; details: Record<Id, OutboxDetail>; compares: DraftCompare[] } {
  const T = ids.touch;
  const specs: ItemSpec[] = [];
  const add = (s: ItemSpec) => specs.push(s);

  /* Replied: Priya holds Browserbase. */
  const priya = c.person('Priya Natarajan', 'Engineering Manager', 'manager', 'Browserbase');
  c.person('Lena Szabo', 'Staff Engineer', 'engineer', 'Browserbase');
  c.person('Tom Becker', 'Software Engineer', 'engineer', 'Browserbase');
  const priyaSubject = "your team's session pool write-up";
  const priyaFirst = `Hi Priya,\n\nYour team's write-up on pooling browser sessions answered a question I had been stuck on for a week. I had been opening a fresh browser for every task and paying for it in latency.\n\nI graduated from Northeastern in May 2026, and I want in at Browserbase as a Software Engineer, New Grad. I built OpenCodeIntel, code search for AI coding agents with a 12-tool MCP server.\n\nWould you refer me, or point me to whoever owns that req?\n\n${LINKS}\n\n${SIGN}`;
  const priyaFu1 = `Hi Priya,\n\nOne new thing since my last note: I wrote up how a reranker first made my code search worse, then better.\n\nhttps://www.devanshuchicholikar.com/writing/rerankers-code-search\n\n${SIGN}`;
  const priyaReply = "Hi Devanshu,\n\nThanks for reaching out. We're hiring for Software Engineer, New Grad and I'm happy to chat. Does Thursday at 2 PT work for 20 minutes?\n\nPriya";
  const priyaReplyAt = et(TODAY, '09:12');
  add({
    touch: T.priya, person: priya, company: 'Browserbase', group: 'replied', state: 'sent', touchState: 'replied', seq: 1,
    subject: `Re: ${priyaSubject}`, preview: 'Happy to chat. Does Thursday at 2 PT work for 20 minutes?', summary: 'Holding 2 queued emails at Browserbase', at: priyaReplyAt,
    d: {
      body: priyaFu1, firstSentAt: pt('2026-10-30', '10:02'), gmailThreadUrl: 'https://mail.google.com/mail/u/0/#inbox/thread-priya-natarajan',
      followups: [
        { seq: 1, dueOn: '2026-11-04', sentAt: pt('2026-11-04', '11:20'), state: 'sent' },
        { seq: 2, dueOn: '2026-11-10', sentAt: null, state: 'planned' },
        { seq: 3, dueOn: '2026-11-20', sentAt: null, state: 'planned' },
      ],
      reply: { from: 'Priya Natarajan', at: priyaReplyAt, text: priyaReply, kind: 'interested' },
      held: [
        { touchId: T.lena, personName: 'Lena Szabo', state: 'approved', seq: 0, dueOn: null },
        { touchId: T.tom, personName: 'Tom Becker', state: 'drafted', seq: 1, dueOn: TODAY },
      ],
      thread: [
        out(0, priyaSubject, priyaFirst, pt('2026-10-30', '10:02')),
        out(1, `Re: ${priyaSubject}`, priyaFu1, pt('2026-11-04', '11:20')),
        { seq: null, direction: 'in', subject: `Re: ${priyaSubject}`, body: priyaReply, at: priyaReplyAt, from: 'Priya Natarajan' },
      ],
    },
  });

  /* Drafts to review: two blocked, two with a warning, three clean, one a follow-up. */
  const kenji = c.person('Kenji Watanabe', 'Software Engineer', 'engineer', 'Trigger.dev');
  const kenjiRole = c.role('Trigger.dev', 'Software Engineer I', 'swe1', RM, 4);
  add({
    touch: T.kenji, person: kenji, company: 'Trigger.dev', group: 'review', state: 'drafted', subject: 'your post on safe task retries', summary: "Near-identical to Ava Moreno's draft",
    at: et(TODAY, '09:58'), schedule: sched(et(TODAY, '11:05'), 'next_slot', 'Sends after 11:05 ET', 1, 2), body: BODY_KENJI,
    checks: [
      ck('near_identical', 'block', "Near-identical to Ava Moreno's draft (92% the same)", 'Two people at one company comparing notes is the fastest way to lose both. Change the middle paragraph or the ask.', { kind: 'compare', otherMessageId: messageOf(T.ava) }),
      ...passes([
        'Mail server found for trigger.example', '2 links, portfolio first, no shortener, no attachment', 'Not on the no-go or said-no list',
        'Company not closed for no sponsorship', 'No banned phrases', 'No em or en dashes', '91 words (80 to 120)', 'Hook 13 days old (30 at most)', 'Subject unique to him, no "Re:"',
      ]),
    ],
    d: { role: kenjiRole, hook: { text: 'His post on retries without double work', on: '2026-10-28' } },
  });
  add({
    touch: T.rafael, person: c.person('Rafael Ortiz', 'Software Engineer', 'engineer', 'Graphite', { email: 'rafael@graphtie.example' }), company: 'Graphite', group: 'review', state: 'drafted',
    subject: 'your stacked diffs launch post', summary: 'No mail server for graphtie.example', at: et(TODAY, '09:51'),
    schedule: sched(et('2026-11-11', '08:30'), 'per_company_limit', 'Sends tomorrow', 3, 0),
    checks: [
      ck('no_mail_server', 'block', 'No mail server for graphtie.example', 'The domain on file looks mistyped. Check graphite.example, then correct it on the company page.'),
      ...passes(CLEAN_TITLES.slice(1)),
    ],
    d: { hook: { text: 'Their stacked diffs launch post', on: '2026-11-02' } },
  });
  const dana = c.person('Dana Whitcomb', 'Technical Recruiter', 'recruiter', 'Mintlify');
  const mintlifyRole = c.role('Mintlify', 'Software Engineer, New Grad', 'new_grad', SF, 6);
  add({
    touch: T.dana, person: dana, company: 'Mintlify', group: 'review', state: 'drafted', subject: 'Software Engineer, New Grad on OPT', summary: 'Recruiter: sends on day 2', at: et(TODAY, '09:47'),
    schedule: sched(et('2026-11-11', '08:30'), 'recruiter_day_2', 'Sends Wed Nov 11', 2, 1), body: BODY_DANA,
    checks: [
      ck('recruiter_title', 'warn', 'Recruiter title', "Recruiters go out on day 2, after engineers and managers. They do not count toward Mintlify's 3 a day, but they do count toward today's 30.", { kind: 'role_kind' }),
      ...passes(['87 words (about 90 for recruiters)', 'Work authorization line up front', 'Mail server found for mintlify.example', '2 links, portfolio first, no shortener, no attachment',
        'Not on the no-go or said-no list', 'Company not closed for no sponsorship', 'No banned phrases', 'No em or en dashes', 'Role and req named first', 'Subject unique to her, no "Re:"']),
    ],
    d: { role: mintlifyRole, hook: { text: 'No hook needed for recruiters', on: null } },
  });
  const marcusSubject = 'Re: your post on docs for agents';
  const marcusFirstAt = pt('2026-10-30', '10:02');
  add({
    touch: T.marcus, person: c.person('Marcus Feld', 'Software Engineer', 'engineer', 'Mintlify'), company: 'Mintlify', group: 'review', state: 'drafted', seq: 2,
    subject: marcusSubject, summary: 'Due today', at: et(TODAY, '09:40'), schedule: sched(et('2026-11-11', '08:30'), 'per_company_limit', 'Sends tomorrow', 3, 0), body: BODY_MARCUS,
    checks: passes(['Adds one new thing: you confirm that when you read it', 'Same thread as the first email', '1 link, no shortener, no attachment', 'Not on the no-go or said-no list',
      'No banned phrases', 'No em or en dashes', '59 words (40 to 80)']),
    d: {
      hook: { text: 'His post on docs for agents', on: '2026-10-30' }, role: mintlifyRole, firstSentAt: marcusFirstAt,
      followups: [
        { seq: 1, dueOn: '2026-11-04', sentAt: pt('2026-11-04', '09:10'), state: 'sent' },
        { seq: 2, dueOn: '2026-11-10', sentAt: null, state: 'drafted' },
        { seq: 3, dueOn: '2026-11-20', sentAt: null, state: 'planned' },
      ],
      thread: [
        out(0, 'your post on docs for agents', defaultBody('Marcus', 'Mintlify', 'your post on docs for agents'), marcusFirstAt),
        out(1, marcusSubject, 'Hi Marcus,\n\nA short follow-up on my note about docs for agents. I would still love a name for the new grad req.\n\n' + SIGN, pt('2026-11-04', '09:10')),
      ],
    },
  });
  add({
    touch: T.omar, person: c.person('Omar Haddad', 'Co-founder and CTO', 'founder', 'Resend'), company: 'Resend', group: 'review', state: 'drafted', subject: 'your batch api rate limits post',
    summary: 'All checks passed', at: et(TODAY, '09:36'), schedule: sched(et(TODAY, '10:55'), 'next_slot', 'Sends after 10:55 ET', 0, 3), body: BODY_OMAR,
    checks: passes(['98 words (80 to 120)', ...CLEAN_TITLES.slice(0, 6), 'Hook 7 days old (30 at most)', 'Subject unique to him, no "Re:"', 'Plain text']),
    d: { project: 'Saar', role: c.role('Resend', 'Software Engineer, New Grad', 'new_grad', SF, 2), hook: { text: 'His write-up on how the batch API paces sends', on: '2026-11-03' } },
  });
  add({
    touch: T.sara, person: c.person('Sara Lindqvist', 'Staff Engineer', 'engineer', 'Resend'), company: 'Resend', group: 'review', state: 'drafted', subject: 'your talk on email deliverability',
    summary: 'Hook is 41 days old', at: et(TODAY, '09:33'), schedule: sched(et(TODAY, '11:55'), 'spacing', 'Sends after 11:55 ET', 0, 3),
    checks: [ck('hook_old', 'warn', 'Hook is 41 days old', 'A hook over 30 days old reads as stale. Pick a newer one, or keep it if it still fits.'), ...passes(CLEAN_TITLES.filter((t) => !t.startsWith('Hook')))],
    d: { project: 'Saar', hook: { text: 'Her talk on email deliverability', on: '2026-09-30' } },
  });
  add({
    touch: T.ava, person: c.person('Ava Moreno', 'Software Engineer', 'engineer', 'Trigger.dev'), company: 'Trigger.dev', group: 'review', state: 'drafted', subject: 'your post on durable queues',
    summary: 'All checks passed', at: et(TODAY, '09:30'), schedule: sched(et(TODAY, '11:05'), 'next_slot', 'Sends after 11:05 ET', 1, 2), body: BODY_AVA,
    checks: clean(), d: { role: kenjiRole, hook: { text: 'Her post on durable queues', on: '2026-11-01' } },
  });

  /* Approved: waiting for a slot. */
  add({ touch: T.jordan, person: c.person('Jordan Price', 'Software Engineer', 'engineer', 'Mintlify'), company: 'Mintlify', group: 'approved', state: 'approved', subject: 'your docs search rewrite',
    summary: 'Next send. Third Mintlify email today', at: et(TODAY, '10:42'), schedule: sched(et(TODAY, '10:42'), 'next_slot', 'Sends 10:42 ET', 2, 1), checks: clean() });
  add({ touch: T.arjun, person: c.person('Arjun Mehta', 'Software Engineer', 'engineer', 'Graphite'), company: 'Graphite', group: 'approved', state: 'approved', subject: 'your merge queue post',
    summary: '60 minutes after the last Graphite email', at: et(TODAY, '11:05'), schedule: sched(et(TODAY, '11:05'), 'spacing', 'Sends after 11:05 ET', 2, 1), checks: clean() });
  add({ touch: T.felix, person: c.person('Felix Graham', 'Engineering Manager', 'manager', 'Graphite'), company: 'Graphite', group: 'approved', state: 'approved', subject: "graphite's early career role",
    summary: 'Graphite has 3 today already', at: et('2026-11-11', '08:30'), schedule: sched(et('2026-11-11', '08:30'), 'per_company_limit', 'Sends tomorrow', 3, 0), checks: clean() });

  /* Sending and checking. */
  add({ touch: T.chloe, person: c.person('Chloe Martin', 'Software Engineer', 'engineer', 'Supabase'), company: 'Supabase', group: 'sending', state: 'sending', subject: 'your realtime at scale talk',
    summary: 'Locked: cannot edit, approve or skip', at: et(TODAY, '10:14'), checks: clean() });
  add({ touch: T.ben, person: c.person('Ben Ito', 'Software Engineer', 'engineer', 'Graphite'), company: 'Graphite', group: 'checking', state: 'send_unknown', subject: "your team's stacked prs work",
    summary: 'Gmail did not answer. Looking in Sent at 10:15', at: et(TODAY, '10:05'), checks: clean() });

  /* Sent: Nina and Theo from the mockup, then deterministic invented rows. */
  const nina = track(TODAY);
  const ninaAt = et(TODAY, '09:41');
  add({ touch: T.nina, person: c.person('Nina Kovac', 'Software Engineer', 'engineer', 'Mintlify'), company: 'Mintlify', group: 'sent', state: 'sent', seq: nina.seq, subject: 'the docs linter you shipped',
    summary: nina.summary, at: ninaAt, nextDue: nina.nextDue,
    d: { followups: nina.followups, firstSentAt: ninaAt, gmailThreadUrl: 'https://mail.google.com/mail/u/0/#sent/thread-nina', thread: [out(0, 'the docs linter you shipped', defaultBody('Nina', 'Mintlify', 'the docs linter you shipped'), ninaAt)] } });
  const theo = track('2026-11-05');
  const theoAt = et('2026-11-05', '09:20');
  add({ touch: T.theo, person: c.person('Theo Brandt', 'Software Engineer', 'engineer', 'Supabase'), company: 'Supabase', group: 'sent', state: 'sent', seq: theo.seq, subject: 'your postgres branching post',
    summary: theo.summary, at: theoAt, nextDue: theo.nextDue,
    d: { followups: theo.followups, firstSentAt: theoAt, gmailThreadUrl: 'https://mail.google.com/mail/u/0/#sent/thread-theo', thread: [out(0, 'your postgres branching post', defaultBody('Theo', 'Supabase', 'your postgres branching post'), theoAt)] } });
  generatedSent(c, sentTotal - 2, 2100).forEach(add);

  /* Paused by you: Cartesia. */
  add({ touch: T.will, person: c.person('Will Turner', 'Software Engineer', 'engineer', 'Cartesia'), company: 'Cartesia', group: 'paused', state: 'approved', subject: 'your voice latency benchmarks',
    summary: "Waiting on a friend's intro", at: et('2026-11-09', '14:20'), checks: clean() });
  add({ touch: 2017, person: c.person('Maren Oyelaran', 'Founding Engineer', 'founder', 'Cartesia'), company: 'Cartesia', group: 'paused', state: 'drafted', subject: 'your streaming speech write-up',
    summary: "Waiting on a friend's intro", at: et('2026-11-09', '14:10'), checks: clean() });

  /* Closed: 1 bounced, 4 skipped, 12 because the company closed. */
  add({ touch: 2800, person: c.person('Rosa Diaz', 'Recruiter', 'recruiter', 'Browserbase'), company: 'Browserbase', group: 'closed', state: 'sent', touchState: 'bounced', subject: "your team's session pool write-up",
    summary: 'Bounced: mailbox does not exist', at: et('2026-10-31', '09:00'), d: { bounceReason: 'Gmail: 550 5.1.1 The email account that you tried to reach does not exist.', firstSentAt: pt('2026-10-30', '10:02') } });
  [['Supabase', 'Rune Ashworth'], ['Graphite', 'Ines Calloway'], ['Resend', 'Tobias Fenwick'], ['Trigger.dev', 'Livia Marchetti']].forEach(([company, name], i) => {
    add({ touch: 2801 + i, person: c.person(name ?? '', 'Software Engineer', 'engineer', company ?? ''), company: company ?? '', group: 'closed', state: 'skipped', touchState: 'closed',
      subject: `your post on ${TOPICS[i + 3]}`, summary: 'Skipped by you', at: et('2026-11-06', `1${i}:00`) });
  });
  [['Quillfeather', 3, 'no sponsorship'], ['Crusoe', 2, 'interview'], ['Retell AI', 2, 'interview'], ['Distyl AI', 2, 'interview'], ['Northbeam Labs', 3, 'referral']].forEach(([company, n, why]) => {
    for (let k = 0; k < (n as number); k++) {
      const idx = specs.length;
      add({ touch: 2810 + idx, person: c.person(`${FIRSTS[(idx * 5) % FIRSTS.length]} ${LASTS[(idx * 3 + 1) % LASTS.length]}`, 'Software Engineer', 'engineer', company as string), company: company as string,
        group: 'closed', state: 'drafted', touchState: 'closed', subject: `your post on ${TOPICS[idx % TOPICS.length]}`, summary: `Company closed: ${why as string}`, at: et('2026-11-03', `1${k}:30`) });
    }
  });

  // The Sent group reads newest first: sort its rows by `at` before ranking, each row keeping the slot it was declared in.
  const sentNewestFirst = specs.filter((s) => s.group === 'sent').sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0));
  let nextSent = 0;
  const ordered = specs.map((s) => (s.group === 'sent' ? (sentNewestFirst[nextSent++] ?? s) : s));

  const items: OutboxItem[] = [];
  const details: Record<Id, OutboxDetail> = {};
  ordered.forEach((s, rank) => {
    const built = buildItem(c, s, rank);
    items.push(built.item);
    details[s.touch] = built.detail;
  });

  const compare: DraftCompare = {
    a: { messageId: messageOf(T.kenji), personName: 'Kenji Watanabe', body: BODY_KENJI },
    b: { messageId: messageOf(T.ava), personName: 'Ava Moreno', body: BODY_AVA },
    similarity: 0.92, matches: sharedParagraphs(BODY_KENJI, BODY_AVA),
  };

  return { view: { asOf: AS_OF, sending: sendingStatus(), items, counts: countGroups(items), closedBreakdown: { bounced: 1, skipped: 4, companyClosed: 12 } }, details, compares: [compare] };
}

function sharedParagraphs(a: string, b: string): DraftCompare['matches'] {
  const matches: DraftCompare['matches'] = [];
  let at = 0;
  for (const para of a.split('\n\n')) {
    const bStart = b.indexOf(para);
    if (bStart >= 0 && para.length > 12) matches.push({ aStart: at, aEnd: at + para.length, bStart, bEnd: bStart + para.length });
    at += para.length + 2;
  }
  return matches;
}

export function countGroups(items: OutboxItem[]): OutboxCounts {
  const counts: OutboxCounts = { replied: 0, review: 0, approved: 0, sending: 0, checking: 0, sent: 0, paused: 0, closed: 0 };
  items.forEach((i) => { counts[i.group] += 1; });
  return counts;
}

function sendingStatus(): SendingStatus {
  return {
    state: 'running', pausedReason: '', pausedAt: null, gmailMessage: null, sentToday: 20, limitToday: 30, rampLevel: 2,
    rampLevels: [20, 30, 40, 50, 60, 70, 80, 90, 100], nextLimitIfClean: 40, lastStepDown: null,
    signals: { bounceRate3d: 0.01, gmailErrorsToday: 0, stopRepliesToday: 0 }, window: { start: '08:30', end: '16:30' },
    nextSend: { at: '2026-11-10T15:42:00Z', tz: TZ, touchId: ids.touch.jordan, personName: 'Jordan Price', companyName: 'Mintlify' },
    noneReason: null, revision: 1,
  };
}

/* ------------------------------------------------------------------ LinkedIn (linkedin.html) */

type StepList = [LinkedInStep, number][];
function lperson(c: Ctx, company: string, name: string, title: string, kind: RoleKind, tab: LinkedInPerson['tab'], reason: LinkedInReason, steps: StepList = []): LinkedInPerson {
  const noEmail = reason === 'no_email';
  return {
    person: c.person(name, title, kind, company, noEmail ? { email: null } : {}), reason, tab,
    steps: steps.map(([step, ago]) => ({ step, at: noon(ago) })),
    replyKind: tab === 'replied' ? 'interested' : null,
  };
}

function buildLinkedIn(c: Ctx): LinkedInView {
  const group = (company: string, role: RoleToName, emailed: number, people: LinkedInPerson[], heldBy: LinkedInGroup['heldBy'] = null): LinkedInGroup =>
    ({ company: c.co(company), role, emailed, onLinkedIn: people.length, heldBy, people });
  const L = (company: string) => (name: string, title: string, kind: RoleKind, tab: LinkedInPerson['tab'], reason: LinkedInReason, steps?: StepList) =>
    lperson(c, company, name, title, kind, tab, reason, steps);
  const mint = L('Mintlify'), bb = L('Browserbase'), res = L('Resend'), sup = L('Supabase'), gra = L('Graphite'), car = L('Cartesia');
  const groups: LinkedInGroup[] = [
    group('Mintlify', c.role('Mintlify', 'Software Engineer, New Grad', 'new_grad', SF, 6), 8, [
      mint('Hannah Cole', 'Software Engineer, Editor team', 'engineer', 'to_reach', 'past_budget'),
      mint('Luis Romero', 'Engineering Manager', 'manager', 'waiting', 'past_budget', [['request_sent', 4]]),
      mint('Grace Kim', 'Developer Advocate', 'engineer', 'waiting', 'no_email', [['request_sent', 5], ['accepted', 2]]),
      mint('Ibrahim Sall', 'Software Engineer, Northeastern alum', 'alum', 'waiting', 'alum_first', [['request_sent', 7], ['accepted', 5], ['message_sent', 1]]),
    ]),
    group('Browserbase', c.role('Browserbase', 'Software Engineer, New Grad', 'new_grad', SF, 21), 7, [
      bb('Owen Clarke', 'Software Engineer, Stagehand', 'engineer', 'to_reach', 'past_budget'),
      bb('Maya Ellison', 'Software Engineer', 'engineer', 'waiting', 'past_budget', [['request_sent', 7]]),
    ], { touchId: ids.touch.priya, personName: 'Priya Natarajan' }),
    group('Resend', c.role('Resend', 'Software Engineer, New Grad', 'new_grad', SF, 2), 2, [
      res('Ethan Wu', 'Software Engineer', 'engineer', 'to_reach', 'no_email'),
    ]),
    group('Supabase', c.role('Supabase', 'Software Engineer, New Grad', 'new_grad', RM, 5), 7, [
      sup('Lucan Treadwell', 'Software Engineer', 'engineer', 'to_reach', 'past_budget'),
      sup('Imani Okoye', 'Staff Engineer', 'engineer', 'to_reach', 'past_budget'),
      sup('Sven Halloran', 'Engineering Manager', 'manager', 'to_reach', 'no_email'),
      sup('Reyna Castellanos', 'Software Engineer', 'engineer', 'to_reach', 'past_budget'),
      sup('Dalia Brandt', 'Software Engineer', 'engineer', 'waiting', 'past_budget', [['request_sent', 6]]),
      sup('Kenzo Ishikawa', 'Software Engineer', 'engineer', 'waiting', 'past_budget', [['request_sent', 3], ['accepted', 1]]),
      sup('Anneke Visser', 'Software Engineer', 'engineer', 'replied', 'past_budget', [['request_sent', 8], ['accepted', 7], ['message_sent', 5], ['replied', 2]]),
    ]),
    group('Graphite', c.role('Graphite', 'Software Engineer, Early Career', 'early_career', NY, 10), 7, [
      gra('Percival Moon', 'Software Engineer', 'engineer', 'to_reach', 'past_budget'),
      gra('Yusra Haidari', 'Engineering Manager', 'manager', 'to_reach', 'past_budget'),
      gra('Joaquin Sterne', 'Software Engineer', 'engineer', 'to_reach', 'no_email'),
      gra('Beatrix Hollis', 'Software Engineer', 'engineer', 'to_reach', 'past_budget'),
      gra('Mateus Corvo', 'Software Engineer', 'engineer', 'waiting', 'past_budget', [['request_sent', 5]]),
      gra('Linnea Ahlberg', 'Software Engineer', 'engineer', 'waiting', 'past_budget', [['request_sent', 4], ['accepted', 3]]),
      gra('Mira Shah', 'Software Engineer', 'engineer', 'replied', 'past_budget', [['request_sent', 6], ['accepted', 5], ['message_sent', 4], ['replied', 1]]),
    ]),
    group('Cartesia', c.role('Cartesia', 'Software Engineer, New Grad', 'new_grad', SF, 9), 2, [
      car('Ottoline Fry', 'Software Engineer', 'engineer', 'to_reach', 'past_budget'),
      car('Rune Sorensen', 'Software Engineer', 'engineer', 'waiting', 'past_budget', [['request_sent', 2]]),
    ]),
  ];
  const count = (tab: LinkedInPerson['tab']) => groups.reduce((n, g) => n + g.people.filter((p) => p.tab === tab).length, 0);
  // The mockup's All tab reads 31 while the three tabs add to 23, so `all` is stored as shown.
  return { asOf: AS_OF, groups, counts: { toReach: count('to_reach'), waiting: count('waiting'), replied: count('replied'), all: 31 }, requestsThisWeek: 14 };
}

/* ------------------------------------------------------------------ Needs you (needs-you.html) */

function buildNeedsYou(c: Ctx): NeedsYouView {
  const item = (kind: ReviewKind, company: string, picked: boolean, openedAt: IsoTime, ageDays: number, reason: string, o: Partial<ReviewItem> = {}): ReviewItem => ({
    id: c.next(), kind, company: { ...c.co(company), picked }, holdsEmail: false, reason, openedAt, ageDays, reopened: null, quote: null, reads: [],
    lastOkAt: null, openPostings: null, sizeFacts: [], revision: 1, ...o,
  });
  const read = (date: IsoDate, result: 'ok' | 'empty' | 'error', detail: string) => ({ at: et(date, '08:04'), result, detail });
  const hex = c.role('Hex', 'Software Engineer, New Grad', 'new_grad', SF, 12);
  const open: ReviewItem[] = [
    item('domain_missing', 'Cerebrium', true, et(TODAY, '08:00'), 0, "No email domain on file, so Claude's drafts cannot be checked for a mail server and none can be approved.", { holdsEmail: true }),
    item('size_unknown', 'Trigger.dev', true, et(TODAY, '08:00'), 0, 'You picked a company whose size nobody has confirmed. LinkedIn shows the 11 to 50 band, with no headcount from a second source.', {
      holdsEmail: true,
      sizeFacts: [c.fact('size', '11 to 50', 'guess', 'linkedin', 'LinkedIn company page', { low: 11, high: 50, quote: '11 to 50 employees', observedOn: TODAY })],
    }),
    item('sponsorship_unclear', 'Hex', false, et('2026-11-09', '08:00'), 1, 'The posting says it may sponsor. Setting it to No adds Hex to the no-go list.', {
      quote: { text: 'We may be able to support visa sponsorship for exceptional candidates.', postingId: hex.postingId, postingTitle: hex.title, url: hex.url, firstSeenAt: hex.firstSeenAt },
    }),
    item('board_unreadable', 'Zed Industries', false, '2026-10-30T14:00:00Z', 11,
      "The Ashby board has answered with an error on every read since Oct 22 (HTTP 503). An error never closes a company, so its 2 postings stay open with what we last saw on Oct 19.", {
        reads: [read('2026-10-19', 'ok', 'Ashby board read, 2 postings'), ...['2026-10-22', '2026-10-26', '2026-10-29', '2026-11-02', '2026-11-05'].map((d) => read(d, 'error', 'HTTP 503'))],
        lastOkAt: et('2026-10-19', '08:04'), openPostings: 2,
      }),
    item('board_empty', 'PlanetScale', false, et('2026-11-09', '08:04'), 1,
      'The Greenhouse board returned no jobs twice in a row, so its 3 postings were closed. If they moved boards, give the new one. If they stopped hiring, mark ok.', {
        reads: [read('2026-10-29', 'ok', 'Greenhouse board read, 3 postings'), read('2026-11-02', 'ok', 'Greenhouse board read, 3 postings'), read('2026-11-05', 'empty', 'Greenhouse board returned no jobs'), read('2026-11-09', 'empty', 'Greenhouse board returned no jobs')],
        lastOkAt: et('2026-11-02', '08:04'), openPostings: 0,
      }),
  ];
  const done = (kind: ReviewKind, companyName: string, resolution: ResolvedReview['resolution'], note: string, date: IsoDate): ResolvedReview =>
    ({ id: c.next(), kind, companyName, resolution, note, at: et(date, '15:20'), locked: true });
  const resolvedThisWeek: ResolvedReview[] = [
    done('size_unknown', 'Turso', 'field_edited', 'Set to about 45 by you, from LinkedIn 11 to 50', '2026-11-06'),
    done('sponsorship_unclear', 'Railway', 'marked_ok', 'Marked ok by you', '2026-11-05'),
    done('sponsorship_unclear', 'Clerk', 'field_edited', 'Sponsorship set to Not stated by you', '2026-11-05'),
    done('board_empty', 'Warp', 'marked_ok', 'Marked ok by you', '2026-11-04'),
    done('domain_missing', 'Weaviate', 'field_edited', 'Domain set to weaviate.example by you', '2026-11-04'),
    done('size_guess', 'Fly.io', 'marked_ok', 'Marked ok by you', '2026-11-04'),
  ];
  return { asOf: AS_OF, open, resolvedThisWeek, boardsJob: JOBS.crawl() };
}

/* ------------------------------------------------------------------ No-go (no-go.html) */

const NO_SPONSOR_QUOTES = [
  "We don't sponsor and aren't hiring new grads this year.", 'No visa sponsorship is available for this role.', 'We are not able to sponsor employment visas.',
  'Candidates must be authorized to work in the US without sponsorship.', 'We do not sponsor H-1B or OPT.', 'Sponsorship is not available now or later.',
];
const CITIZEN_QUOTES = ['US citizens only. Active clearance required.', 'Must be eligible for a Top Secret clearance.', 'ITAR rules: US persons only.'];

function buildNoGo(c: Ctx): NoGoView {
  const nogo = (company: string, reason: NoGoReason, o: { quote?: string; note?: string; source: NoGoSource; ref: string; url?: string | null; imp?: ImportSource | null; at: IsoTime }): NoGoRow => ({
    kind: 'no_go', noGoId: c.next(), target: { kind: 'company', company: c.co(company) }, reason, quote: o.quote ?? '', note: o.note ?? '', source: o.source,
    sourceRef: o.ref, sourceUrl: o.url ?? null, importSource: o.imp ?? null, addedAt: o.at,
  });
  const removed = (company: string, removeReason: string, note: string, date: IsoDate): RemovedRow =>
    ({ kind: 'removed', company: { ...c.co(company), revision: 1 }, removeReason, note, removedAt: et(date, '11:00') });
  const rules = (company: string, reason: NoGoReason, role: string, date: IsoDate, o: { quote?: string; note?: string }) =>
    nogo(company, reason, { ...o, source: 'rules', ref: `${role} posting`, url: `https://careers.example/${slug(company)}/${slug(role)}`, at: et(date, '08:04') });

  const rest = (names: string[], reason: NoGoReason, quotes: string[]): NoGoRow[] => names.map((name, i) => {
    const date = addDays('2026-09-20', i * 3 + (reason === 'over_300' ? 1 : 0));
    if (reason === 'over_300') return nogo(name, reason, { note: `${420 + i * 130} people`, source: 'rules', ref: 'LinkedIn company page', url: `https://linkedin.example/company/${slug(name)}`, at: et(date, '08:04') });
    return rules(name, reason, ['Backend Engineer', 'Software Engineer I', 'Platform Engineer'][i % 3] ?? 'Software Engineer', date, { quote: quotes[i % quotes.length] });
  });
  const reasons = ['not_a_fit', 'already_in_touch', 'bigger_than_it_looks', 'other'];
  const companies: (NoGoRow | RemovedRow)[] = [
    rules('Halberd Systems', 'no_sponsorship', 'Backend Engineer', '2026-10-14', { quote: 'We are unable to sponsor visas now or in the future.' }),
    nogo('Quillfeather', 'no_sponsorship', { quote: "We don't sponsor and aren't hiring new grads this year.", note: 'Closed by you', source: 'reply', ref: 'Reply from their CTO', at: et('2026-11-03', '16:10') }),
    rules('Meridian Defense Labs', 'citizenship', 'Software Engineer I', '2026-10-02', { quote: 'Must be a US citizen with an active Secret clearance.' }),
    nogo('Larkspur Health', 'over_300', { note: '1,240 people', source: 'rules', ref: 'LinkedIn company page', url: 'https://linkedin.example/company/larkspur-health', at: et('2026-09-24', '08:04') }),
    removed('Brightwater Analytics', 'not_a_fit', 'Ad tech, not a fit', '2026-11-04'),
    nogo('Ostrander Robotics', 'no_sponsorship', { note: 'Marked by hand in the old dashboard', source: 'import', ref: 'Old dashboard, locked', imp: 'data_js', at: et('2026-09-28', '12:00') }),
    ...rest(NO_SPONSOR_NAMES.slice(3), 'no_sponsorship', NO_SPONSOR_QUOTES),
    ...rest(CITIZEN_NAMES.slice(1), 'citizenship', CITIZEN_QUOTES),
    ...rest(OVER_300_NAMES.slice(1), 'over_300', []),
    ...REMOVED_NAMES.slice(1).map((name, i) => removed(name, reasons[(i + 1) % reasons.length] ?? 'other', ['Sells ads', 'Already applied there', 'Larger than the page says', 'Not my kind of product'][i] ?? '', addDays('2026-10-26', i * 2))),
  ];

  const pnogo = (company: string, name: string, title: string, kind: RoleKind, reason: NoGoReason, source: NoGoSource, ref: string, date: IsoDate, o: { quote?: string; note?: string }): NoGoRow => ({
    kind: 'no_go', noGoId: c.next(), target: { kind: 'person', person: c.person(name, title, kind, company), companyName: company }, reason, quote: o.quote ?? '', note: o.note ?? '',
    source, sourceRef: ref, sourceUrl: null, importSource: null, addedAt: et(date, '14:00'),
  });
  const people: NoGoRow[] = [
    pnogo('Supabase', 'Ruth Okonkwo', 'Staff Engineer', 'engineer', 'stop_reply', 'reply', 'Reply, Nov 4', '2026-11-04', { quote: 'Please take me off your list.' }),
    pnogo('Graphite', 'Tessa Lindgren', 'Engineering Manager', 'manager', 'said_no', 'reply', 'Reply, Nov 2', '2026-11-02', { quote: "I don't do referrals for people I haven't worked with." }),
    pnogo('Mintlify', 'Elena Petrova', 'Recruiter', 'recruiter', 'other', 'devanshu', 'You', '2026-10-30', { note: 'Already talking through a friend' }),
    pnogo('Cerebrium', 'Victor Hale', 'Founder', 'founder', 'other', 'claude', 'Side terminal, Nov 9', '2026-11-09', { quote: 'no cold outreach, please', note: 'Profile says' }),
    pnogo('Resend', 'Caspian Doyle', 'Software Engineer', 'engineer', 'said_no', 'reply', 'Reply, Nov 7', '2026-11-07', { quote: 'Not hiring new grads, sorry.' }),
    pnogo('Graphite', 'Odile Frame', 'Engineering Manager', 'manager', 'other', 'devanshu', 'You', '2026-11-06', { note: 'Left the company' }),
    pnogo('Cartesia', 'Bastien Roque', 'Founder', 'founder', 'other', 'claude', 'Side terminal, Nov 8', '2026-11-08', { note: 'Asked not to be contacted on LinkedIn' }),
  ];
  const countOf = (reason: NoGoReason) => companies.filter((r) => r.kind === 'no_go' && r.reason === reason).length;
  return {
    asOf: AS_OF, companies, people,
    countsByReason: [
      { reason: 'no_sponsorship', count: countOf('no_sponsorship') }, { reason: 'citizenship', count: countOf('citizenship') },
      { reason: 'over_300', count: countOf('over_300') }, { reason: 'removed', count: companies.filter((r) => r.kind === 'removed').length },
    ],
  };
}

/* ------------------------------------------------------------------ Done (done.html) */

function buildDone(c: Ctx): DoneView {
  const row = (name: string, outcome: 'interview' | 'referral', label: string, closedAt: IsoTime, how: string, imported: boolean, role: string | null, note: string) =>
    ({ company: c.co(name), outcome, outcomeLabel: label, closedAt, howStarted: how, imported, role, note });
  const imp = (name: string, label: string) => row(name, 'interview', label, et('2026-10-18', '12:00'), 'Before the switch', true, null, '');
  return {
    asOf: AS_OF,
    companies: [
      imp('Crusoe', 'Interviewing'), imp('Retell AI', 'Interviewed'), imp('Distyl AI', 'Interviewed'),
      row('Northbeam Labs', 'referral', 'Referral', et('2026-11-06', '15:00'), 'Email to Kai Andersen, engineer', false, 'Software Engineer, Early Career', 'Kai submitted the referral Nov 6'),
    ],
    metrics: {
      repliesThisMonth: 9,
      repliesPerWeek: ['2026-09-21', '2026-09-28', '2026-10-05', '2026-10-12', '2026-10-19', '2026-10-26', '2026-11-02', '2026-11-09'].map((weekOf, i) => ({ weekOf, count: [0, 1, 2, 1, 3, 4, 5, 4][i] ?? 0 })),
      replyRate: { replies: 9, firstEmails: 140 }, referrals: 2, interviews: 3,
    },
  };
}

/* ------------------------------------------------------------------ Company pages (company.html, then one generic builder) */

interface Views {
  today: TodayView; outbox: OutboxView; details: Record<Id, OutboxDetail>; linkedin: LinkedInView; needsYou: NeedsYouView; noGo: NoGoView; done: DoneView;
}

const RULESET = '2026.11.02';
const ev = (c: Ctx, at: IsoTime, kind: string, text: string, actor: Actor, importSource: ImportSource | null = null): CompanyEvent => ({ id: c.next(), at, kind, text, actor, importSource });

function postingRow(r: RoleToName): PostingRow {
  return {
    id: r.postingId, title: r.title, location: r.location, url: r.url, firstSeenAt: r.firstSeenAt, lastSeenAt: et('2026-11-09', '08:04'), closedAt: null, closeReason: null,
    levelTier: r.levelTier, sponsorship: 'silent', state: 'open', hiddenRule: null, flags: [],
  };
}

function browserbaseView(c: Ctx, v: Views): CompanyView {
  const T = ids.touch;
  const role = c.role('Browserbase', 'Software Engineer, New Grad', 'new_grad', SF, 21);
  const priya = c.person('Priya Natarajan', 'Engineering Manager', 'manager', 'Browserbase');
  const lena = c.person('Lena Szabo', 'Staff Engineer', 'engineer', 'Browserbase');
  const tom = c.person('Tom Becker', 'Software Engineer', 'engineer', 'Browserbase');
  const alicia = c.person('Alicia Grant', 'Software Engineer', 'engineer', 'Browserbase');
  const jun = c.person('Jun Park', 'Founding Engineer', 'founder', 'Browserbase');
  const rosa = c.person('Rosa Diaz', 'Recruiter', 'recruiter', 'Browserbase');
  const kofi = c.person('Kofi Mensah', 'Staff Engineer', 'engineer', 'Browserbase');
  const owen = c.person('Owen Clarke', 'Software Engineer, Stagehand', 'engineer', 'Browserbase');
  const maya = c.person('Maya Ellison', 'Software Engineer', 'engineer', 'Browserbase');
  const priyaThread = v.details[T.priya]?.thread ?? [];

  const size = c.fact('size', '48', 'confirmed', 'linkedin', 'LinkedIn and careers page', { quote: '51 to 200 employees', observedOn: '2026-11-02' });
  const stage = c.fact('stage', 'Series B', 'estimate', 'other', 'Press release', { quote: 'announced its Series B', observedOn: '2025-06-17' });
  const sponsorship = c.fact('sponsorship', 'Says it sponsors', 'confirmed', 'posting', 'New grad posting', { quote: 'We sponsor visas for this role.', observedOn: '2026-10-20' });
  const domain = c.fact('domain', 'browserbase.example', 'confirmed', 'hand', 'You, Oct 30', { locked: true, observedOn: '2026-10-30' });
  const everify = c.fact('everify', 'Participates in E-Verify', 'confirmed', 'everify', 'E-Verify employer search', { observedOn: '2026-10-21' });
  const evidence: Fact[] = [
    size, sponsorship, domain, stage, everify,
    c.fact('hq', 'San Francisco', 'confirmed', 'website', 'Company website footer', { observedOn: '2026-10-21' }),
    c.fact('size', '51 to 200', 'guess', 'linkedin', 'LinkedIn company page', { low: 51, high: 200, quote: '51 to 200 employees', observedOn: '2026-10-21' }),
    c.fact('sponsorship', 'Has sponsored H-1B', 'confirmed', 'lca', '3 DOL filings in 2025', { observedOn: '2026-10-21' }),
  ];

  const bposting = (id: number, title: string, loc: string, firstAgo: number, lastAgo: number, tier: LevelTier, sp: PostingRow['sponsorship'], state: PostingRow['state'], o: Partial<PostingRow> = {}): PostingRow => ({
    id, title, location: loc, url: `https://careers.example/browserbase/${id}`, firstSeenAt: noon(firstAgo), lastSeenAt: noon(lastAgo), closedAt: null, closeReason: null,
    levelTier: tier, sponsorship: sp, state, hiddenRule: null, flags: [], ...o,
  });
  const postings: PostingRow[] = [
    { ...postingRow(role), lastSeenAt: noon(1), sponsorship: 'yes' },
    bposting(9001, 'Software Engineer, Stagehand', SF, 39, 1, 'not_stated', 'silent', 'open'),
    bposting(9002, 'Senior Software Engineer, Infrastructure', RM, 59, 1, 'senior', 'silent', 'out_of_lane', { hiddenRule: 'Asks more than 3 years' }),
    bposting(9003, 'Software Engineer Intern, Summer 2027', SF, 41, 15, 'not_stated', 'silent', 'closed', { closedAt: noon(12), closeReason: 'Closed on the board' }),
  ];

  const cp = (person: PersonRef, channel: 'email' | 'linkedin', statusLine: string, tone: CompanyPerson['tone'], last: IsoTime | null): CompanyPerson => ({ person, channel, statusLine, tone, lastTouchAt: last });
  const people: CompanyPerson[] = [
    cp(priya, 'email', 'Replied', 'ok', et(TODAY, '09:12')), cp(lena, 'email', 'Held, approved', 'idle', et('2026-11-09', '16:40')), cp(tom, 'email', 'Held, follow-up 1 drafted', 'idle', et('2026-11-05', '10:30')),
    cp(alicia, 'email', 'Sent', 'idle', et('2026-11-05', '10:30')), cp(jun, 'email', 'Sent', 'idle', pt('2026-10-30', '10:02')), cp(rosa, 'email', 'Bounced', 'bad', et('2026-10-31', '09:00')),
    cp(kofi, 'email', 'Sent', 'idle', pt('2026-10-30', '10:02')), cp(owen, 'linkedin', 'Not started', 'idle', null), cp(maya, 'linkedin', 'Requested Nov 3', 'info', noon(7)),
  ];

  const first = (p: PersonRef, at: IsoTime): ThreadMessage[] => [out(0, "your team's session pool write-up", defaultBody(p.name.split(' ')[0] ?? '', 'Browserbase', "your team's session pool write-up"), at)];
  const touch = (touchId: Id, person: PersonRef, state: TouchState, summary: string, messages: ThreadMessage[]) => ({ touchId, person, state, summary, messages });
  const touches = [
    touch(T.priya, priya, 'replied', 'First email, follow-up 1, reply', priyaThread),
    touch(T.lena, lena, 'open', 'First email approved, held', []),
    touch(T.tom, tom, 'open', 'First email. Follow-up 1 drafted, held', first(tom, et('2026-11-05', '10:30'))),
    touch(2030, alicia, 'open', 'First email sent Nov 5', first(alicia, et('2026-11-05', '10:30'))),
    touch(2031, jun, 'open', 'First email sent Oct 30', first(jun, pt('2026-10-30', '10:02'))),
    touch(2800, rosa, 'bounced', 'First email bounced: mailbox does not exist', first(rosa, pt('2026-10-30', '10:02'))),
    touch(2032, kofi, 'open', 'First email sent Oct 30', first(kofi, pt('2026-10-30', '10:02'))),
  ];

  const events: CompanyEvent[] = [
    ev(c, et(TODAY, '09:12'), 'reply', 'Reply from Priya Natarajan. Company held: Lena Szabo and Tom Becker wait.', 'sender'),
    ev(c, et('2026-11-09', '16:40'), 'approved', "Approved Lena Szabo's first email", 'devanshu'),
    ev(c, et('2026-11-05', '10:30'), 'sent', 'Sent first emails to Tom Becker and Alicia Grant', 'sender'),
    ev(c, pt('2026-11-04', '11:20'), 'followup_sent', 'Sent follow-up 1 to Priya Natarajan', 'sender'),
    ev(c, et('2026-10-31', '09:00'), 'bounce', 'First email to Rosa Diaz bounced: mailbox does not exist', 'sender'),
    ev(c, pt('2026-10-30', '10:02'), 'sent', 'Sent first emails to Priya Natarajan, Jun Park and Rosa Diaz', 'sender'),
    ev(c, pt('2026-10-30', '09:30'), 'people_added', 'Claude added 9 people from the side terminal: 7 to email, 2 to LinkedIn', 'claude'),
    ev(c, pt('2026-10-30', '08:45'), 'picked', 'Picked from Today', 'devanshu'),
    ev(c, pt('2026-10-30', '08:40'), 'domain_set', 'Domain set to browserbase.example and locked', 'devanshu'),
    ev(c, et('2026-11-02', '08:05'), 'rules', 'Rules version 2026.11.02 reclassified 4 postings', 'rules'),
    ev(c, et('2026-10-21', '12:00'), 'stage', 'Stage Referral asked, contact Lena Szabo', 'import', 'data_js'),
    ev(c, et('2026-10-02', '12:00'), 'linkedin', 'Lena Szabo messaged on LinkedIn', 'import', 'contacted_txt'),
    ev(c, et('2026-09-30', '12:00'), 'applied', 'Applied to Software Engineer, New Grad', 'import', 'devboard'),
    ev(c, et('2026-09-12', '12:00'), 'posting_seen', 'Posting first seen', 'import', 'radar_db'),
    // Crawler reads on Mondays and Thursdays at 08:04, newest first.
    ...['2026-11-09', '2026-11-05', '2026-11-02', '2026-10-29', '2026-10-26', '2026-10-22', '2026-10-19', '2026-10-15', '2026-10-12', '2026-10-08', '2026-10-05', '2026-10-01', '2026-09-28', '2026-09-24', '2026-09-21', '2026-09-17', '2026-09-14']
      .map((d) => ev(c, et(d, '08:04'), 'board_read', 'Board read ok, 3 postings', 'crawler')),
  ].sort((a, b) => (a.at < b.at ? 1 : -1));

  return {
    asOf: AS_OF,
    company: {
      ...c.co('Browserbase'), domain: 'browserbase.example', aliases: [], hq: SF, pickedOn: '2026-10-30', day: 3, closed: null, revision: 1,
    },
    summary: 'browserbase.example, San Francisco. Picked Fri Oct 30. Priya Natarajan replied today at 9:12, so 2 queued emails are held.',
    facts: [size, stage, sponsorship, domain, everify], sponsorshipShown: 'says_yes', mailServerOk: true,
    board: { ats: 'Ashby', token: 'browserbase', url: 'https://jobs.ashbyhq.com/browserbase', lastRead: 'ok', lastReadAt: et('2026-11-09', '08:04'), errorStreak: 0, emptyStreak: 0 },
    role, postings, people, touches, evidence, events, reviewItems: [], rulesetVersion: RULESET,
  };
}

const NO_GO_LINE: Partial<Record<NoGoReason, string>> = { no_sponsorship: 'No-go: no sponsorship', citizenship: 'No-go: citizenship or clearance', over_300: 'No-go: over 300 people' };
export const STEP_LINE: Record<LinkedInPerson['tab'], string> = { to_reach: 'Not started', waiting: 'Waiting', replied: 'Replied' };
const GROUP_LINE: Record<OutboxGroup, { text: string; tone: CompanyPerson['tone'] }> = {
  replied: { text: 'Replied', tone: 'ok' }, review: { text: 'Draft to review', tone: 'idle' }, approved: { text: 'Approved', tone: 'info' }, sending: { text: 'Sending', tone: 'info' },
  checking: { text: 'Checking', tone: 'info' }, sent: { text: 'Sent', tone: 'idle' }, paused: { text: 'Paused', tone: 'idle' }, closed: { text: 'Closed', tone: 'idle' },
};

function statusLineOf(name: string, v: Views): string {
  if (name === 'Browserbase') return 'Held: reply from Priya Natarajan';
  const still = v.today.stillEmailing.find((s) => s.company.name === name);
  if (still) return still.state === 'paused' ? 'Paused by you' : still.state === 'waiting' ? 'Waiting on follow-ups' : `Emailing, day ${still.day} of 3`;
  const entry = [...v.today.proposed, ...v.today.backlog].find((e) => e.company.name === name);
  if (entry) return entry.state === 'picked' ? 'Picked today' : entry.state === 'backlog' ? 'Backlog' : 'Proposed';
  const done = v.done.companies.find((d) => d.company.name === name);
  if (done) return `Done: ${done.outcomeLabel.toLowerCase()}`;
  const row = v.noGo.companies.find((r) => (r.kind === 'no_go' ? r.target.kind === 'company' && r.target.company.name === name : r.company.name === name));
  if (row) return row.kind === 'removed' ? 'Removed by you' : (NO_GO_LINE[row.reason] ?? 'No-go');
  return 'Candidate';
}

function genericCompany(c: Ctx, name: string, v: Views): CompanyView {
  const s = c.spec(name);
  const domain = domainOf(name);
  const entry = [...v.today.proposed, ...v.today.backlog].find((e) => e.company.name === name);
  const still = v.today.stillEmailing.find((x) => x.company.name === name);
  const done = v.done.companies.find((d) => d.company.name === name);
  const noGoRow = v.noGo.companies.find((r) => (r.kind === 'no_go' ? r.target.kind === 'company' && r.target.company.name === name : r.company.name === name));
  const items = v.outbox.items.filter((i) => i.company.name === name);
  const group = v.linkedin.groups.find((g) => g.company.name === name);
  const reviewItems = v.needsYou.open.filter((r) => r.company.name === name);
  const role = entry?.role ?? group?.role ?? null;
  const facts = entry
    ? [entry.size, entry.stage, entry.sponsorshipFact, entry.domainFact].filter((f): f is Fact => f !== null)
    : domain ? [c.fact('domain', domain, 'confirmed', 'website', 'Mail server check', { observedOn: TODAY })] : [];
  const unreadable = reviewItems.some((r) => r.kind === 'board_unreadable');
  const empty = reviewItems.some((r) => r.kind === 'board_empty');
  const closed: CompanyView['company']['closed'] = done
    ? { reason: done.outcome as CloseReason, note: done.note, at: done.closedAt }
    : noGoRow && noGoRow.kind === 'no_go' ? { reason: 'no_sponsorship', note: noGoRow.quote || noGoRow.note, at: noGoRow.addedAt } : null;

  const people: CompanyPerson[] = [
    ...items.map((i) => ({ person: i.person, channel: 'email' as const, statusLine: GROUP_LINE[i.group].text, tone: GROUP_LINE[i.group].tone, lastTouchAt: i.at })),
    ...(group?.people ?? []).map((p) => ({ person: p.person, channel: 'linkedin' as const, statusLine: STEP_LINE[p.tab], tone: 'idle' as const, lastTouchAt: p.steps[p.steps.length - 1]?.at ?? null })),
  ];
  const events: CompanyEvent[] = [];
  if (entry) events.push(ev(c, et(TODAY, '08:01'), 'proposed', 'Proposed on Today', 'daily_list'));
  if (still) events.push(ev(c, et(addDays(TODAY, -(still.day - 1)), '08:45'), 'picked', 'Picked from Today', 'devanshu'));
  if (done) events.push(ev(c, done.closedAt, 'closed', `Closed: ${done.outcomeLabel}`, done.imported ? 'import' : 'devanshu', done.imported ? 'data_js' : null));
  if (noGoRow) events.push(ev(c, noGoRow.kind === 'no_go' ? noGoRow.addedAt : noGoRow.removedAt, 'no_go', noGoRow.kind === 'no_go' ? 'Added to the no-go list' : 'Removed by you', noGoRow.kind === 'no_go' && noGoRow.source === 'rules' ? 'rules' : 'devanshu'));
  events.sort((a, b) => (a.at < b.at ? 1 : -1));

  return {
    asOf: AS_OF,
    company: {
      ...c.co(name), domain, aliases: [], hq: s.hq, pickedOn: still ? addDays(TODAY, -(still.day - 1)) : entry?.state === 'picked' ? TODAY : null,
      day: still?.day ?? (entry?.state === 'picked' ? 1 : null), closed, revision: 1,
    },
    summary: entry?.why ?? statusLineOf(name, v),
    facts, sponsorshipShown: entry?.sponsorship ?? 'not_stated', mailServerOk: entry?.mailServerOk ?? (domain ? true : null),
    board: entry || group ? {
      ats: 'Ashby', token: slug(name), url: `https://jobs.ashbyhq.com/${slug(name)}`, lastRead: unreadable ? 'error' : empty ? 'empty' : 'ok',
      lastReadAt: et('2026-11-09', '08:04'), errorStreak: unreadable ? 5 : 0, emptyStreak: empty ? 2 : 0,
    } : null,
    role, postings: [...(role ? [postingRow(role)] : []), ...(entry?.otherRoles ?? []).map((o, i): PostingRow => ({
      id: 9100 + i, title: o.title, location: s.hq, url: `https://careers.example/${slug(name)}/${9100 + i}`, firstSeenAt: o.firstSeenAt, lastSeenAt: et('2026-11-09', '08:04'),
      closedAt: null, closeReason: null, levelTier: 'not_stated', sponsorship: 'silent', state: 'open', hiddenRule: null, flags: [],
    }))],
    people,
    touches: items.map((i) => ({ touchId: i.touchId, person: i.person, state: i.touchState, summary: i.summary, messages: v.details[i.touchId]?.thread ?? [] })),
    evidence: facts.map((f) => ({ ...f })), events, reviewItems: reviewItems.map((r) => ({ ...r })), rulesetVersion: RULESET,
  };
}

/* ------------------------------------------------------------------ search, shell, scenarios */

function buildSearch(c: Ctx, v: Views): SearchIndex {
  const companies: SearchCompany[] = COMPANY_ORDER.map((name) => {
    const s = c.spec(name);
    return { id: companyId(name), name, aliases: [], domain: domainOf(name), status: s.status, statusLine: statusLineOf(name, v), inProgress: s.status === 'working' };
  });
  const lineByPerson = new Map<Id, string>();
  v.outbox.items.forEach((i) => lineByPerson.set(i.person.id, GROUP_LINE[i.group].text));
  v.linkedin.groups.forEach((g) => g.people.forEach((p) => lineByPerson.set(p.person.id, `LinkedIn: ${STEP_LINE[p.tab].toLowerCase()}`)));
  v.noGo.people.forEach((r) => { if (r.target.kind === 'person') lineByPerson.set(r.target.person.id, 'No-go'); });
  const people: SearchPerson[] = c.people.map(({ person, companyName }) => ({
    id: person.id, companyId: companyId(companyName), companyName, name: person.name, title: person.title, email: person.email, statusLine: lineByPerson.get(person.id) ?? '',
  }));
  return { asOf: AS_OF, companies, people };
}

export function computeShell(db: Pick<FixtureDb, 'today' | 'outbox' | 'linkedin' | 'needsYou' | 'noGo' | 'done'> & { jobs: JobRunSummary[] }): ShellSummary {
  const s = db.outbox.sending;
  return {
    asOf: db.today.asOf, pickedToday: db.today.picked, capacityToday: db.today.capacity, unhandledReplies: db.outbox.counts.replied, draftsToReview: db.outbox.counts.review,
    linkedinToReach: db.linkedin.counts.toReach, openReviewItems: db.needsYou.open.length, noGoCount: db.noGo.companies.length + db.noGo.people.length,
    doneCount: db.done.companies.length, sending: { state: s.state, sentToday: s.sentToday, limitToday: s.limitToday },
    repliesThisMonth: db.done.metrics.repliesThisMonth, jobs: db.jobs,
  };
}

const BLOCK_MESSAGE = '550 5.4.5 Daily user sending quota exceeded. For more information on Gmail sending limits, go to https://support.google.com/a/answer/166852';

function setAsOf(db: FixtureDb, asOf: IsoTime) {
  db.shell.asOf = asOf; db.searchIndex.asOf = asOf; db.today.asOf = asOf; db.outbox.asOf = asOf; db.linkedin.asOf = asOf;
  db.needsYou.asOf = asOf; db.noGo.asOf = asOf; db.done.asOf = asOf;
  Object.values(db.companies).forEach((x) => { x.asOf = asOf; });
  Object.values(db.details).forEach((x) => { x.asOf = asOf; });
}

function pause(s: SendingStatus, state: SenderState, reason: string, at: IsoTime, noneReason: string) {
  s.state = state; s.pausedReason = reason; s.pausedAt = at; s.nextSend = null; s.noneReason = noneReason;
}

/** A fresh copy each call. The `error` scenario carries the default data; the client rejects its reads. */
export function buildFixture(scenario: Scenario = 'default'): FixtureDb {
  const c = makeCtx();
  const today = buildToday(c);
  const { view: outbox, details, compares } = buildOutbox(c, scenario === 'many_sent' ? 500 : 64);
  const linkedin = buildLinkedIn(c);
  const needsYou = buildNeedsYou(c);
  const noGo = buildNoGo(c);
  const done = buildDone(c);
  const views: Views = { today, outbox, details, linkedin, needsYou, noGo, done };

  const companies: Record<Id, CompanyView> = {};
  for (const name of COMPANY_ORDER) companies[companyId(name)] = name === 'Browserbase' ? browserbaseView(c, views) : genericCompany(c, name, views);
  const bb = companies[companyId('Browserbase')];
  const postings: PostingHit[] = [
    ...c.postings,
    ...(bb ? bb.postings.slice(1).map((p) => ({ postingId: p.id, title: p.title, companyId: bb.company.id, companyName: 'Browserbase', open: p.state === 'open' })) : []),
  ];
  const db: FixtureDb = {
    scenario, shell: computeShell({ ...views, jobs: jobList() }), searchIndex: buildSearch(c, views), postings, today, outbox, details, compares, linkedin, needsYou, noGo, companies, done,
  };

  const s = db.outbox.sending;
  if (scenario === 'hard_block') {
    pause(s, 'paused_hard_block', 'Gmail said the daily limit was reached', et(TODAY, '10:02'), 'Sending is paused until you resume.');
    s.gmailMessage = BLOCK_MESSAGE;
    s.signals.gmailErrorsToday = 1;
    s.lastStepDown = { at: et(TODAY, '10:02'), reason: 'Gmail daily limit reached' };
  } else if (scenario === 'signed_out') {
    pause(s, 'signed_out', 'Gmail sign-in expired. Sign in again with mail/gmail.py auth in the live checkout.', et(TODAY, '09:50'), 'Gmail is signed out.');
  } else if (scenario === 'weekend') {
    setAsOf(db, '2026-11-14T15:14:00Z');
    db.today.date = '2026-11-14';
    db.today.proposed = []; db.today.picked = 0; db.today.builtAt = null;
    db.today.notReady = { reason: 'weekend', text: 'No list on weekends. The backlog is still here.' };
    pause(s, 'weekend', '', et('2026-11-13', '16:30'), 'Weekend. Sending resumes Monday at 08:30.');
    s.sentToday = 0;
  } else if (scenario === 'job_failed') {
    db.today.proposed = []; db.today.picked = 0; db.today.builtAt = null;
    db.today.notReady = { reason: 'job_failed', text: "Today's list is not ready. It is built at 08:00 on weekdays." };
    const failed: JobRunSummary = { ...JOBS.daily_list(), finishedAt: et(TODAY, '08:00'), status: 'failed', error: 'Could not read the company store: database is locked', counts: [] };
    db.today.listJob = failed;
  } else if (scenario === 'empty') {
    db.searchIndex.companies = []; db.searchIndex.people = []; db.postings = [];
    db.today.proposed = []; db.today.backlog = []; db.today.stillEmailing = []; db.today.picked = 0;
    db.outbox.items = []; db.outbox.counts = countGroups([]); db.outbox.closedBreakdown = { bounced: 0, skipped: 0, companyClosed: 0 };
    s.sentToday = 0; s.nextSend = null; s.noneReason = 'Nothing is approved.';
    db.details = {}; db.compares = []; db.companies = {};
    db.linkedin.groups = []; db.linkedin.counts = { toReach: 0, waiting: 0, replied: 0, all: 0 }; db.linkedin.requestsThisWeek = 0;
    db.needsYou.open = []; db.needsYou.resolvedThisWeek = [];
    db.noGo.companies = []; db.noGo.people = []; db.noGo.countsByReason = [];
    db.done.companies = [];
    db.done.metrics = { repliesThisMonth: 0, repliesPerWeek: db.done.metrics.repliesPerWeek.map((w) => ({ ...w, count: 0 })), replyRate: { replies: 0, firstEmails: 0 }, referrals: 0, interviews: 0 };
  }
  const jobs = scenario === 'job_failed' && db.today.listJob ? jobList().map((j) => (j.job === 'daily_list' && db.today.listJob ? db.today.listJob : j)) : jobList();
  db.shell = computeShell({ ...db, jobs });
  if (scenario === 'weekend') db.shell.asOf = '2026-11-14T15:14:00Z';
  return db;
}
