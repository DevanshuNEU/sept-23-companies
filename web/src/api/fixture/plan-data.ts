/*
 * Sample data for the people-plan views (#33): Today as a plan, the outcome prompt, wins, progress, findings,
 * the watch list and the score breakdown. Like data.ts it stands in for #11, so every derived field the views show
 * (row state, reason, `enough`, `quiet`, the win line, score share, tomorrow's preview) is stored here as data.
 * The UI computes none of them.
 *
 * It takes no values from data.ts, only a `PlanCtx` built there, so the two files never import each other at run time.
 * Company names are real and public; every person, number, URL (`.example` hosts) and quote is invented, except the
 * LinkedIn people-search links, which use the real search address and are never fetched.
 *
 * Numbers the tests and the e2e specs rely on (default, 2026-11-10): see Task 2 of docs/superpowers/plans/2026-10-21-web-experience.md.
 */
import type {
  Check, CompanyRef, CompanyStatus, FindingsView, FunnelKey, Id, IsoDate, IsoTime, JobRunSummary, LevelTier, ListGroup, NoteDraft, NoteFact,
  OutcomePromptView, PersonRef, PlanRow, PlanStep, PlanView, ProgressPeriod, ProgressView, RoleKind, RoleToName, SavedBy, ScoreSignal, ScoreView,
  SourceLink, WatchView, WinsView, Win,
} from '../types';
import type { Scenario } from './data';

/** What data.ts hands over: the clock, the company registry and the people and roles it already made. */
export interface PlanCtx {
  asOf: IsoTime;
  date: IsoDate;
  listJob: JobRunSummary | null;
  et(date: IsoDate, hm: string): IsoTime;
  addDays(date: IsoDate, n: number): IsoDate;
  companies: { name: string; id: Id; status: CompanyStatus; hq: string; domain: string | null }[];
  co(name: string): CompanyRef;
  person(name: string, title: string, kind: RoleKind, company: string): PersonRef;
  role(company: string, title: string, tier: LevelTier, loc: string, age: number): RoleToName;
  touch: { priya: Id; marcus: Id };
}

export interface PlanFixture {
  plan: PlanView; prompt: OutcomePromptView; wins: WinsView; progress: ProgressView; findings: FindingsView; watch: WatchView;
  scores: Record<Id, ScoreView>; // fixture-internal lookup by company id, not the wire
}

const MAX_CHARS = 200;
const MIN_REQUESTS = 30;
const RANKER = '2026.11.02';
const link = (label: string, host: string, path: string): SourceLink => ({ label, url: `https://${host}.example/${path}` });
const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '');

/** Whole calendar days from `from` to `to`, both "YYYY-MM-DD". */
function daysBetween(from: IsoDate, to: IsoDate): number {
  const ms = (d: IsoDate) => { const [y, m, day] = d.split('-').map(Number); return Date.UTC(y ?? 0, (m ?? 1) - 1, day ?? 1); };
  return Math.round((ms(to) - ms(from)) / 86_400_000);
}

/* ------------------------------------------------------------------ the plan */

type Mode = 'default' | 'done' | 'fresh';

interface RowSpec {
  company: string; lane: ListGroup; channel: PlanRow['channel'];
  person?: { name: string; title: string; kind: RoleKind; foundOn: SourceLink | null };
  role?: { title: string; tier: LevelTier; loc: string; age: number };
  reason: string; sources: SourceLink[];
  note?: { text: string; facts: NoteFact[]; state: NoteDraft['reasonState']; line: string; by?: SavedBy };
  carriedFrom?: IsoDate;
}

const copyFact = (f: NoteFact): NoteFact => ({ ...f, source: { ...f.source } }); // each build owns its copies, so a write never leaks into the next fixture
const fact = (text: string, label: string, host: string, path: string): NoteFact => ({ text, source: link(label, host, path), inNote: true });

const ROWS: RowSpec[] = [
  {
    company: 'Resend', lane: 'level_role', channel: 'linkedin_request',
    person: { name: 'Dana Whitfield', title: 'Software Engineer', kind: 'engineer', foundOn: link('Resend team page', 'resend', 'team') },
    role: { title: 'Software Engineer, New Grad', tier: 'new_grad', loc: 'San Francisco or remote US', age: 2 },
    reason: 'Resend has a new grad role open and has sponsored H-1B before.', sources: [link('Resend careers page', 'resend', 'careers/new-grad'), link('Department of Labor filings', 'dol', 'resend')],
    note: {
      text: "Hi Dana, I read Resend's post on how you moved email events to a queue. I built a small version of that at school and have a question about your retry rules.",
      facts: [fact('Resend moved email events to a queue', 'Resend engineering blog, Oct 22', 'resend', 'blog/events-queue')],
      state: 'confirmed', line: 'Her team moved email events to a queue in October (blog post).',
    },
  },
  {
    company: 'Baseten', lane: 'level_role', channel: 'linkedin_request',
    person: { name: 'Tomas Reyes', title: 'Forward Deployed Engineer', kind: 'engineer', foundOn: link('Baseten team page', 'baseten', 'team') },
    role: { title: 'Forward Deployed Engineer, New Grad', tier: 'new_grad', loc: 'San Francisco', age: 5 },
    reason: 'Baseten has a new grad forward deployed role open and has sponsored H-1B before.', sources: [link('Baseten careers page', 'baseten', 'careers/fde-new-grad')],
    note: {
      text: "Hi Tomas, your talk on cold start times for model servers matched a problem I hit last spring. I would like to ask how the forward deployed team picks its first projects.",
      facts: [fact('Baseten gave a talk on cold start times', 'Baseten talk, Oct 30', 'baseten', 'talks/cold-starts')],
      state: 'confirmed', line: 'He gave a talk on cold starts that matches my project.',
    },
  },
  {
    company: 'Pylon', lane: 'level_role', channel: 'linkedin_request',
    person: { name: 'Lena Ortiz', title: 'Software Engineer', kind: 'engineer', foundOn: link('Pylon team page', 'pylon', 'team') },
    role: { title: 'Software Engineer I', tier: 'swe1', loc: 'San Francisco', age: 1 },
    reason: 'Pylon posted a Software Engineer I role yesterday and says it sponsors visas.', sources: [link('Pylon careers page', 'pylon', 'careers/swe-1'), link('Pylon blog, Oct 14', 'pylon', 'blog/usage-billing')],
    note: {
      text: "Hi Lena, I read Pylon's October post on usage billing and how you replay late events. I built a similar metering pipeline in class. Could I ask you how your team works?",
      facts: [
        fact('Pylon shipped usage based billing', 'Pylon blog, Oct 14', 'pylon', 'blog/usage-billing'),
        fact('Pylon replays late events from a queue', 'Pylon engineering notes', 'pylon', 'engineering/late-events'),
      ],
      state: 'confirmed', line: 'Her team shipped usage billing in October (blog post).',
    },
  },
  {
    company: 'Inngest', lane: 'level_role', channel: 'linkedin_request',
    person: { name: 'Jonah Park', title: 'Engineering Manager', kind: 'manager', foundOn: link('Inngest team page', 'inngest', 'team') },
    role: { title: 'Software Engineer II', tier: 'swe2', loc: 'Remote US', age: 6 },
    reason: 'Inngest has a Software Engineer II role open and the team is small.', sources: [link('Inngest careers page', 'inngest', 'careers/swe-2')],
    note: {
      text: "Hi Jonah, your changelog for durable steps explained something I got wrong in my own job runner. I would like to ask about the Software Engineer II role on your team.",
      facts: [fact('Inngest shipped durable steps', 'Inngest changelog, Nov 3', 'inngest', 'changelog/durable-steps')],
      state: 'proposed', line: 'He leads the team that shipped durable steps in November.',
    },
  },
  {
    company: 'Cerebrium', lane: 'level_role', channel: 'linkedin_request',
    role: { title: 'Member of Technical Staff', tier: 'swe1', loc: 'New York', age: 3 },
    reason: 'Cerebrium has a Member of Technical Staff role open and no one on the team is saved yet.', sources: [link('Cerebrium careers page', 'cerebrium', 'careers/mts')],
  },
  {
    company: 'Turso', lane: 'intern_junior', channel: 'linkedin_request',
    person: { name: 'Ines Duarte', title: 'Founder', kind: 'founder', foundOn: link('Turso about page', 'turso', 'about') },
    role: { title: 'Software Engineering Intern', tier: 'not_stated', loc: 'Remote US', age: 4 },
    reason: 'Turso posted an intern role you can do on OPT, and its founder reads every request.', sources: [link('Turso careers page', 'turso', 'careers/intern')],
    note: {
      text: "Hi Ines, I tried libSQL embedded replicas on a side project and hit a sync edge case. Is the Software Engineering Intern role open to a May 2026 graduate on OPT?",
      facts: [fact('Turso offers embedded replicas', 'Turso docs', 'turso', 'docs/embedded-replicas')],
      state: 'written', line: 'She founded the company, and the intern role fits my OPT.',
    },
    carriedFrom: '2026-11-09',
  },
  {
    company: 'Mintlify', lane: 'reason_no_role', channel: 'email',
    person: { name: 'Marcus Feld', title: 'Software Engineer', kind: 'engineer', foundOn: null }, // the draft already waits in the Outbox
    reason: 'Mintlify announced a Series A on Nov 5 and has no role posted. That is a reason to write.', sources: [link('Mintlify announcement, Nov 5', 'mintlify', 'blog/series-a')],
  },
];

const HANDOFF = (company: string, role: string) =>
  `Find one person at ${company} to ask for a referral for ${role}. Prefer an engineer on the team, then a manager. Reply with their name, title and LinkedIn profile link.`;
const searchUrl = (company: string) =>
  `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(`engineer ${company}`)}&origin=GLOBAL_SEARCH_HEADER`;

/** Stands in for #15's `check_note`: the codes and severities are its own. A note rule that fails is a `block`. */
const NOTE_PHRASE_RULES = [
  { code: 'banned_phrase', passCode: 'pass_nobannedphrases', passTitle: 'No banned phrases', title: 'Banned phrase', detail: '"I\'d love to" is on the banned list. Say it plainly.', find: /I['\u2019]d love to/gi },
  { code: 'dash', passCode: 'pass_noemorendashes', passTitle: 'No em or en dashes', title: 'Em or en dash', detail: 'Use a comma, a colon or two sentences instead.', find: /[\u2013\u2014]/g },
];
const BLOCK_FIRST = { block: 0, warn: 1, pass: 2 } as const;

/** Every check for a note's text, in one pass: the length, then each phrase rule. Blocks come first, so a passing note lists only passes. */
export function rescanNote(text: string, maxChars: number): Check[] {
  const over = text.length - maxChars;
  const checks: Check[] = [
    over > 0
      ? { code: 'note_too_long', severity: 'block', title: `Over the limit by ${over} characters`, detail: `A note can be ${maxChars} characters or fewer.`, fix: null, spans: [{ field: 'body', start: maxChars, end: text.length }] }
      : { code: 'pass_notelength', severity: 'pass', title: `Within ${maxChars} characters`, detail: '', fix: null, spans: [] },
  ];
  for (const r of NOTE_PHRASE_RULES) {
    const spans = [...text.matchAll(r.find)].map((m) => ({ field: 'body' as const, start: m.index ?? 0, end: (m.index ?? 0) + m[0].length }));
    checks.push(spans.length
      ? { code: r.code, severity: 'block', title: r.title, detail: r.detail, fix: null, spans }
      : { code: r.passCode, severity: 'pass', title: r.passTitle, detail: '', fix: null, spans: [] });
  }
  return checks.sort((a, b) => BLOCK_FIRST[a.severity] - BLOCK_FIRST[b.severity]);
}

const NOTE_CHECKS = (): Check[] => rescanNote('', MAX_CHARS);

interface Built { rows: PlanRow[]; requests: number; applications: number }

function buildRows(c: PlanCtx, mode: Mode): Built {
  let touchId = 2100;
  let minute = 2;
  const stepAt = () => { minute += 2; return c.et(c.date, `09:${String(minute).padStart(2, '0')}`); };
  const doneUpTo = mode === 'done' ? 7 : mode === 'default' ? 2 : 0;
  let requests = 0;
  let applications = 0;

  const rows = ROWS.map((spec, i): PlanRow => {
    const n = i + 1;
    const isDone = n <= doneUpTo;
    // In the finished day the person on row 5 is found, so every row has someone.
    const filledIn = mode === 'done' && spec.company === 'Cerebrium';
    const spp = filledIn
      ? { name: 'Wen Zhao', title: 'Member of Technical Staff', kind: 'engineer' as RoleKind, foundOn: link('Cerebrium team page', 'cerebrium', 'team') }
      : spec.person;
    const person = spp ? { person: c.person(spp.name, spp.title, spp.kind, spec.company), foundOn: spp.foundOn } : null;
    const role = spec.role ? c.role(spec.company, spec.role.title, spec.role.tier, spec.role.loc, spec.role.age) : null;
    const companyRef = c.companies.find((x) => x.name === spec.company);
    if (!companyRef) throw new Error(`Unknown fixture company ${spec.company}`);

    let steps: PlanStep[] = [];
    if (spec.channel === 'linkedin_request') {
      steps = [{ kind: 'request', touchId: null, at: null }];
      if (role) steps.push({ kind: 'apply', touchId: null, at: null });
      if (isDone && person) steps = steps.map((s) => ({ kind: s.kind, touchId: touchId++, at: stepAt() }));
      requests += steps.filter((s) => s.kind === 'request' && s.at).length;
      applications += steps.filter((s) => s.kind === 'apply' && s.at).length;
    }

    const noteSpec = spec.note;
    const note: NoteDraft | null = noteSpec ? {
      noteId: 6100 + n, text: noteSpec.text, chars: noteSpec.text.length, maxChars: MAX_CHARS,
      reasonLine: noteSpec.line, reasonState: noteSpec.state, needsYourLine: noteSpec.state === 'proposed',
      facts: noteSpec.facts.map(copyFact), checks: NOTE_CHECKS(), savedBy: noteSpec.by ?? 'claude', savedAt: c.et(c.date, '08:01'), revision: 1,
    } : filledIn ? wenNote(c, n) : null;

    return {
      entryId: 6000 + n, position: n, channel: spec.channel, lane: spec.lane,
      company: { ...c.co(spec.company), domain: companyRef.domain, hq: companyRef.hq, revision: 1 },
      person, role, applyUrl: role?.url ?? null,
      reason: spec.reason, reasonSources: spec.sources.map((x) => ({ ...x })), note,
      findSomeone: person || spec.channel === 'email' ? null : {
        searchUrl: searchUrl(spec.company), handoff: HANDOFF(spec.company, role?.title ?? 'an engineering role'),
      },
      emailTouchId: spec.channel === 'email' ? c.touch.marcus : null,
      steps, state: isDone ? 'done' : 'todo',
      carriedFrom: spec.carriedFrom && !isDone ? spec.carriedFrom : null, revision: 1,
    };
  });
  // An email row has no steps of its own: it is done once its draft is reviewed in the Outbox.
  return { rows, requests, applications };
}

function wenNote(c: PlanCtx, n: number): NoteDraft {
  const text = "Hi Wen, I saw Cerebrium's post on serverless GPUs that scale to zero. I would like to ask how the team picks what to build next for the Member of Technical Staff role.";
  return {
    noteId: 6100 + n, text, chars: text.length, maxChars: MAX_CHARS,
    reasonLine: 'She works on the serverless GPU product (blog post).', reasonState: 'confirmed', needsYourLine: false,
    facts: [fact('Cerebrium runs serverless GPUs that scale to zero', 'Cerebrium blog, Oct 27', 'cerebrium', 'blog/scale-to-zero')],
    checks: NOTE_CHECKS(), savedBy: 'claude', savedAt: c.et(c.date, '08:01'), revision: 1,
  };
}

const TOMORROW_PREVIEW: PlanView['tomorrow']['preview'] = [
  { companyName: 'Hex', personName: 'Aiden Frost', roleTitle: 'Software Engineer, New Grad' },
  { companyName: 'Warp', personName: 'Rhea Kapoor', roleTitle: 'Software Engineer I' },
  { companyName: 'Clerk', personName: null, roleTitle: 'Product Engineer' },
  { companyName: 'Railway', personName: null, roleTitle: 'Software Engineer, Platform' },
  { companyName: 'Zed Industries', personName: null, roleTitle: 'Software Engineer, Early Career' },
];

/* ------------------------------------------------------------------ progress */

type Counts = [requests: number, accepts: number, replies: number, referrals: number, interviews: number];
interface Effort { requests: number; applications: number; emails: number }

const LABELS: Record<FunnelKey, string> = { requests: 'Requests sent', accepts: 'Accepted', replies: 'Replied', referrals: 'Referrals', interviews: 'Interviews' };
const FROM: Record<FunnelKey, FunnelKey | null> = { requests: null, accepts: 'requests', replies: 'requests', referrals: 'replies', interviews: 'replies' };
const ORDER: FunnelKey[] = ['requests', 'accepts', 'replies', 'referrals', 'interviews'];

function period(label: string, from: IsoDate, to: IsoDate, n: Counts, e: Effort, flags: { enough: boolean; quiet: boolean }): ProgressPeriod {
  const count = (k: FunnelKey) => n[ORDER.indexOf(k)] ?? 0;
  return {
    label, from, to,
    stages: ORDER.map((stage) => {
      const parent = FROM[stage];
      return { stage, label: LABELS[stage], count: count(stage), from: parent ? { stage: parent, count: count(parent) } : null };
    }),
    effort: [{ label: 'Requests sent', count: e.requests }, { label: 'Applications sent', count: e.applications }, { label: 'Emails reviewed', count: e.emails }],
    enough: flags.enough, quiet: flags.quiet,
  };
}

interface Numbers { week: Counts; month: Counts; weekEffort: Effort; monthEffort: Effort; base: { requests: number; accepts: number; replies: number; median: number | null; since: IsoDate }; quiet: boolean; linkedin: [number, number] }

function numbersFor(scenario: Scenario): Numbers {
  const base: Numbers = {
    week: [31, 9, 2, 0, 1], month: [52, 17, 4, 1, 2], weekEffort: { requests: 31, applications: 24, emails: 8 }, monthEffort: { requests: 52, applications: 41, emails: 14 },
    base: { requests: 142, accepts: 41, replies: 9, median: 6, since: '2026-09-21' }, quiet: false, linkedin: [52, 60],
  };
  switch (scenario) {
    case 'plan_done':
      return { ...base, week: [35, 9, 2, 0, 1], month: [56, 17, 4, 1, 2], weekEffort: { requests: 35, applications: 28, emails: 9 }, monthEffort: { requests: 56, applications: 45, emails: 15 },
        base: { ...base.base, requests: 146 }, linkedin: [56, 64] };
    case 'win':
      return { ...base, week: [31, 10, 2, 0, 1], month: [52, 18, 4, 1, 2], base: { ...base.base, accepts: 42 } };
    case 'quiet_week':
      return { ...base, week: [28, 4, 0, 0, 0], weekEffort: { requests: 28, applications: 22, emails: 6 }, quiet: true };
    case 'first_week':
      return { week: [9, 2, 0, 0, 0], month: [9, 2, 0, 0, 0], weekEffort: { requests: 9, applications: 7, emails: 2 }, monthEffort: { requests: 9, applications: 7, emails: 2 },
        base: { requests: 9, accepts: 2, replies: 0, median: null, since: '2026-11-04' }, quiet: false, linkedin: [9, 12] };
    case 'fresh': case 'empty':
      return { week: [0, 0, 0, 0, 0], month: [0, 0, 0, 0, 0], weekEffort: { requests: 0, applications: 0, emails: 0 }, monthEffort: { requests: 0, applications: 0, emails: 0 },
        base: { requests: 0, accepts: 0, replies: 0, median: null, since: '2026-11-10' }, quiet: false, linkedin: [0, 0] };
    default:
      return base;
  }
}

/* ------------------------------------------------------------------ wins, prompt, findings, watch */

function buildWins(c: PlanCtx, scenario: Scenario, rows: PlanRow[]): WinsView {
  const none = scenario === 'fresh' || scenario === 'first_week' || scenario === 'empty';
  const at = (date: IsoDate, hm: string) => c.et(date, hm);
  const kept: Win[] = none ? [] : [
    {
      winId: 3001, kind: 'replied', at: at('2026-11-09', '12:30'), touchId: c.touch.priya, person: c.person('Priya Natarajan', 'Engineering Manager', 'manager', 'Browserbase'), company: c.co('Browserbase'),
      line: 'Priya Natarajan at Browserbase replied and wants to talk.', sentNote: "Hi Priya, your team's write-up on pooling browser sessions answered a question I had been stuck on for a week.",
      role: 'Software Engineer, New Grad', replyKind: 'interested', unseen: false,
    },
    {
      winId: 3002, kind: 'interview', at: at('2026-11-06', '15:00'), touchId: 2210, person: c.person('Camille Roche', 'Head of Engineering', 'manager', 'Distyl AI'), company: c.co('Distyl AI'),
      line: 'Interview at Distyl AI, set up by Camille Roche.', sentNote: 'Hi Camille, I read how your team evaluates agent runs and built a small harness of my own.',
      role: 'Software Engineer, New Grad', replyKind: null, unseen: false,
    },
    {
      winId: 3003, kind: 'replied', at: at('2026-11-05', '10:05'), touchId: 2211, person: c.person('Hugo Lindqvist', 'Staff Engineer', 'engineer', 'Supabase'), company: c.co('Supabase'),
      line: 'Hugo Lindqvist at Supabase replied and wants to talk.', sentNote: 'Hi Hugo, your post on row level security policies changed how I write my own.',
      role: null, replyKind: 'interested', unseen: false,
    },
    {
      winId: 3004, kind: 'referred', at: at('2026-11-02', '14:20'), touchId: 2212, person: c.person('Imani Okafor', 'Software Engineer', 'engineer', 'Crusoe'), company: c.co('Crusoe'),
      line: 'Imani Okafor at Crusoe referred you.', sentNote: 'Hi Imani, I read your note on scheduling GPU jobs and ran into the same limits in a class project.',
      role: 'Software Engineer, New Grad', replyKind: null, unseen: false,
    },
    {
      winId: 3005, kind: 'replied', at: at('2026-10-28', '09:40'), touchId: 2213, person: c.person('Noor Haddad', 'Software Engineer', 'engineer', 'Graphite'), company: c.co('Graphite'),
      line: 'Noor Haddad at Graphite replied and wants to talk.', sentNote: 'Hi Noor, your stacked diffs talk matched how I review my own branches.',
      role: null, replyKind: 'interested', unseen: false,
    },
  ];
  const dana = rows[0];
  if (scenario === 'win' && dana?.person) {
    kept.unshift({
      winId: 3010, kind: 'accepted', at: c.et(c.date, '09:41'), touchId: dana.steps[0]?.touchId ?? 2100, person: dana.person.person, company: c.co('Resend'),
      line: 'Dana Whitfield at Resend accepted your request.', sentNote: dana.note?.text ?? null, role: dana.role?.title ?? null, replyKind: null, unseen: true,
    });
  }
  const tally = (k: Win['kind']) => kept.filter((w) => w.kind === k).length;
  return {
    asOf: c.asOf, wins: kept, unseen: kept.filter((w) => w.unseen).length,
    counts: { accepted: tally('accepted'), replied: tally('replied'), referred: tally('referred'), interview: tally('interview'), offer: tally('offer') },
    since: '2026-10-18',
  };
}

function buildPrompt(c: PlanCtx, scenario: Scenario): OutcomePromptView {
  const none = scenario === 'fresh' || scenario === 'first_week' || scenario === 'empty';
  if (none) return { asOf: c.asOf, touches: [] };
  const touch = (touchId: Id, name: string, title: string, kind: RoleKind, company: string, sent: IsoDate, context: string) => ({
    touchId, person: c.person(name, title, kind, company), company: c.co(company), channel: 'linkedin_request' as const,
    sentAt: c.et(sent, '11:20'), ageDays: daysBetween(sent, c.date), context, revision: 1,
  });
  return {
    asOf: c.asOf,
    touches: [
      touch(2201, 'Marta Kowalski', 'Software Engineer', 'engineer', 'Hex', '2026-11-02', 'Request with a note about their notebook runtime post'),
      touch(2202, 'Owen Brandt', 'Engineering Manager', 'manager', 'Warp', '2026-10-30', 'Request with a note about the new terminal blocks'),
    ],
  };
}

function buildFindings(c: PlanCtx, scenario: Scenario): FindingsView {
  const month = '2026-10-01';
  if (scenario === 'fresh' || scenario === 'first_week' || scenario === 'empty') return { asOf: c.asOf, month, reportedOn: null, findings: [], changes: [] };
  return {
    asOf: c.asOf, month, reportedOn: '2026-11-01',
    findings: [
      {
        id: 7001, text: 'Engineers on the team accepted more often than founders.',
        counts: [{ label: 'Engineers on the team accepted', count: 14, of: 33 }, { label: 'Founders accepted', count: 5, of: 24 }],
        caution: null, changed: 'In September founders were ahead. The daily list now puts engineers first.',
      },
      {
        id: 7002, text: 'Notes that name a role got replies more often than notes that do not.',
        counts: [{ label: 'Replied, role named in the note', count: 5, of: 71 }, { label: 'Replied, no role named', count: 0, of: 29 }],
        caution: 'Only 5 replies in all. Treat this as a hint, not a rule.', changed: null,
      },
    ],
    changes: [{ at: c.et('2026-11-01', '08:00'), text: 'The daily list now puts engineers on the team ahead of founders.' }],
  };
}

function buildWatch(c: PlanCtx, scenario: Scenario): WatchView {
  if (scenario === 'empty') return { asOf: c.asOf, items: [] };
  return {
    asOf: c.asOf,
    items: [
      {
        id: 7101, kind: 'careers_changed', company: c.co('Pylon'), text: 'The careers page moved to a new address. The old one lists no roles.',
        at: c.et('2026-11-09', '08:12'), link: link('New Pylon careers page', 'pylon', 'jobs'), reviewId: null,
      },
      {
        id: 7102, kind: 'new_reason_no_role', company: c.co('Mintlify'), text: 'Mintlify announced a Series A on Nov 5. You have a reason to write without a role.',
        at: c.et('2026-11-06', '08:05'), link: link('Mintlify announcement, Nov 5', 'mintlify', 'blog/series-a'), reviewId: null,
      },
      {
        id: 7103, kind: 'merge_candidate', company: c.co('Hex'), text: 'Hex and Hex Technologies may be one company. They share a careers page.',
        at: c.et('2026-11-08', '08:07'), link: null, reviewId: null,
      },
    ],
  };
}

/* ------------------------------------------------------------------ scores */

interface Sig { name: string; label: string; value: number; weight: number; reason: string; source: SourceLink | null }
const r4 = (n: number) => Math.round(n * 10_000) / 10_000;

function signalsOf(raw: Sig[]): { signals: ScoreSignal[]; score: number } {
  const points = raw.map((s) => r4(s.value * s.weight));
  const total = points.reduce((a, b) => a + Math.abs(b), 0);
  return {
    signals: raw.map((s, i) => ({
      name: s.name, label: s.label, value: s.value, weight: s.weight, points: points[i] ?? 0, share: total === 0 ? 0 : r4((points[i] ?? 0) / total),
      neutral: s.value === 0, reason: s.reason, source: s.source,
    })),
    score: r4(points.reduce((a, b) => a + b, 0)),
  };
}

function pylonSignals(): Sig[] {
  return [
    { name: 'level_match', label: 'Level match', value: 0.9, weight: 2, reason: 'Software Engineer I is open, which matches your level.', source: link('Pylon careers page', 'pylon', 'careers/swe-1') },
    { name: 'sponsorship', label: 'Sponsorship', value: 0.8, weight: 1.5, reason: 'The posting says the company sponsors visas.', source: link('Pylon careers page', 'pylon', 'careers/swe-1') },
    { name: 'posting_fresh', label: 'Posting age', value: 0.9, weight: 1, reason: 'The role was posted 1 day ago.', source: null },
    { name: 'stage', label: 'Funding stage', value: 0.6, weight: 0.5, reason: 'Seed to Series A companies answer more often.', source: link('Pylon funding announcement', 'pylon', 'blog/seed') },
    { name: 'open_roles', label: 'Open engineering roles', value: 0.4, weight: 0.5, reason: '2 engineering roles are open.', source: null },
    { name: 'size', label: 'Company size', value: 0, weight: 1, reason: 'No size is confirmed yet, so size adds nothing either way.', source: null },
    { name: 'people_to_ask', label: 'People to ask', value: -0.5, weight: 1, reason: 'Only one engineer is public on the team page, so there are few people to ask.', source: link('Pylon team page', 'pylon', 'team') },
    { name: 'domain_ok', label: 'Email domain', value: 1, weight: 0.2, reason: 'The mail server for pylon.example answers.', source: null },
  ];
}

function genericSignals(name: string, hq: string): Sig[] {
  const h = [...name].reduce((a, ch) => a + ch.charCodeAt(0), 0);
  const v = (k: number) => (((h * (k + 3)) % 21) - 10) / 10;
  const level = 0.5 + (h % 5) / 10;
  const word = (x: number, yes: string, no: string, flat: string) => (x > 0 ? yes : x < 0 ? no : flat);
  const careers = link(`${name} careers page`, slug(name), 'careers');
  const size = v(1);
  const stage = v(2);
  const fresh = v(3);
  const people = v(4);
  const sponsor = v(5);
  return [
    { name: 'level_match', label: 'Level match', value: level, weight: 2, reason: `A role at ${name} is close to your level.`, source: careers },
    { name: 'sponsorship', label: 'Sponsorship', value: sponsor, weight: 1.5, reason: word(sponsor, 'Filings show it has sponsored before.', 'The posting leans against sponsorship.', 'Nothing is stated about sponsorship.'),
      source: sponsor > 0 ? link('Department of Labor filings', 'dol', slug(name)) : null },
    { name: 'posting_fresh', label: 'Posting age', value: fresh, weight: 1, reason: word(fresh, 'The role was posted recently.', 'The role has been open a long time.', 'The role is a few weeks old.'), source: null },
    { name: 'stage', label: 'Funding stage', value: stage, weight: 0.5, reason: word(stage, 'An early stage company, so it answers more often.', 'A later stage company, so it answers less often.', 'The stage is not known.'), source: null },
    { name: 'size', label: 'Company size', value: size, weight: 1, reason: word(size, `A small team in ${hq}.`, 'Close to the size cap.', 'No size is confirmed yet, so size adds nothing either way.'), source: null },
    { name: 'people_to_ask', label: 'People to ask', value: people, weight: 1, reason: word(people, 'Several engineers are public on the team page.', 'Few engineers are public on the team page.', 'The team page does not say.'),
      source: people !== 0 ? link(`${name} team page`, slug(name), 'team') : null },
  ];
}

function buildScores(c: PlanCtx, scenario: Scenario): Record<Id, ScoreView> {
  if (scenario === 'empty') return {};
  const built = c.companies.map((co) => {
    const { signals, score } = signalsOf(co.name === 'Pylon' ? pylonSignals() : genericSignals(co.name, co.hq));
    return { co, signals, score };
  });
  const ranked = built.filter((b) => b.co.status === 'candidate' || b.co.status === 'working' || b.co.status === 'backlog')
    .sort((a, b) => b.score - a.score);
  const out: Record<Id, ScoreView> = {};
  for (const b of built) {
    const at = ranked.indexOf(b);
    const h = [...b.co.name].reduce((a, ch) => a + ch.charCodeAt(0), 0);
    const strongest: ListGroup = h % 2 === 0 ? 'level_role' : 'any_us_role';
    out[b.co.id] = {
      asOf: c.asOf, companyId: b.co.id, rankerVersion: RANKER, score: b.score, rank: at < 0 ? null : at + 1, ofCompanies: ranked.length,
      lanes: b.co.name === 'Pylon'
        ? [{ lane: 'level_role', strongest: true }, { lane: 'any_us_role', strongest: false }]
        : [{ lane: 'level_role', strongest: strongest === 'level_role' }, { lane: 'any_us_role', strongest: strongest === 'any_us_role' }],
      signals: b.signals, scoredAt: c.et(c.date, '08:00'),
      previous: b.co.name === 'Pylon' ? { rankerVersion: '2026.10.28', score: 3.7 } : null,
    };
  }
  return out;
}

/* ------------------------------------------------------------------ the whole fixture */

export function buildPlanFixture(scenario: Scenario, c: PlanCtx): PlanFixture {
  const n = numbersFor(scenario);
  const noList = scenario === 'weekend' || scenario === 'job_failed' || scenario === 'empty';
  const mode: Mode = scenario === 'plan_done' ? 'done' : scenario === 'fresh' ? 'fresh' : 'default';
  const built: Built = noList ? { rows: [], requests: 0, applications: 0 } : buildRows(c, mode);
  const { rows } = built;
  const done = rows.filter((r) => r.state === 'done').length;

  const notReady: PlanView['notReady'] =
    scenario === 'weekend' ? { reason: 'weekend', text: "No list on weekends. Monday's list will have 7 people." }
    : scenario === 'job_failed' ? { reason: 'job_failed', text: "Today's list is not ready. It is built at 08:00 on weekdays." }
    : scenario === 'empty' ? { reason: 'not_built', text: "Today's list has not been built yet." }
    : null;
  const tomorrow: PlanView['tomorrow'] = scenario === 'empty'
    ? { total: 0, text: 'Nothing is planned yet.', preview: [] }
    : { total: 7, text: scenario === 'weekend' ? 'Monday: 7 people' : 'Tomorrow: 7 people', preview: TOMORROW_PREVIEW.map((p) => ({ ...p })) };
  const [week, month] = [n.week, n.month];
  const [asOfDate, to] = [c.date, c.date];
  const weekFrom = c.addDays(asOfDate, -6);
  const monthFrom = `${asOfDate.slice(0, 7)}-01`;

  const plan: PlanView = {
    asOf: c.asOf, date: c.date, builtAt: noList ? null : c.et(c.date, '08:01'), listJob: c.listJob, notReady, rows, done, total: rows.length,
    linkedinMonth: { used: n.linkedin[0], limit: 150, hardUsed: n.linkedin[1], hardLimit: 200 },
    tomorrow, week: { requests: week[0], accepts: week[1], replies: week[2] },
  };

  const wins = buildWins(c, scenario, rows);
  const progress: ProgressView = {
    asOf: c.asOf,
    today: { planned: rows.length, done, requests: built.requests, applications: built.applications, accepted: wins.wins.filter((w) => w.unseen && w.kind === 'accepted').length, replied: 0 },
    week: period('Last 7 days', weekFrom, to, week, n.weekEffort, { enough: week[0] >= MIN_REQUESTS, quiet: n.quiet }),
    month: period('This month', monthFrom, to, month, n.monthEffort, { enough: month[0] >= MIN_REQUESTS, quiet: false }),
    base: {
      since: n.base.since, requests: n.base.requests, accepts: n.base.accepts, replies: n.base.replies, medianDaysToReply: n.base.median,
      enough: n.base.requests >= MIN_REQUESTS, minRequests: MIN_REQUESTS,
    },
  };

  return {
    plan, prompt: buildPrompt(c, scenario), wins, progress, findings: buildFindings(c, scenario), watch: buildWatch(c, scenario), scores: buildScores(c, scenario),
  };
}
