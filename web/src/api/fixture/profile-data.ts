/*
 * Sample profiles for the company queue (the Today redesign): what does not change about a company, as the API words it
 * (outreach/api/queries/profile.py). Like queue-data.ts every line is stored here as data; the UI computes none of them.
 *
 * Pylon, Resend and Baseten are well known. Cerebrium and Trigger.dev have unknown values ("Not known": text null),
 * Hex and Railway carry an application from before the role, Inngest a Not now, Pylon the most open roles.
 */
import type { CompanyProfile, IsoDate, ProfileRole, ProfileValue, QueueOpening, QueueStanding } from '../types';

type V = readonly [text: string, note: string] | null;
type Place = readonly [title: string, level: string, age: string, location: string, early: boolean];

/** What a company's profile adds to its queue spec. Anything left out is not known. */
export interface Facts {
  stage?: V; funding?: V; headcount?: V; hq?: V; everify?: V; sponsorship?: V; lca?: V;
  domains?: string[]; why?: string;
  more?: Place[]; // open roles at his level besides the row's own, newest first
  passes?: string[];
}

const NOT_KNOWN: ProfileValue = { text: null, note: null };
const value = (v: V | undefined): ProfileValue => (v ? { text: v[0], note: v[1] } : NOT_KNOWN);
const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '');
const SHORT = new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', month: 'short', day: 'numeric' });
const day = (d: IsoDate) => SHORT.format(new Date(`${d}T12:00:00Z`));

export const FACTS: Record<string, Facts> = {
  Pylon: {
    stage: ['Series A', 'confirmed, SEC Form D'], funding: ['$17,000,000 raised, Form D filed Jul 8, 2026', 'confirmed, SEC Form D'],
    headcount: ['30 people', 'estimate, LinkedIn'], hq: ['San Francisco, CA', 'confirmed, its website'],
    everify: ['E-Verify enrolled', 'confirmed, the E-Verify list, checked Oct 3'], sponsorship: ['Says it sponsors', 'confirmed, a posting'],
    lca: ['4 software LCA filings in FY2024 and FY2025', 'DOL disclosure data'], domains: ['AI agents', 'LLM apps'],
    why: 'Agent work on support tickets is close to the work he wants.',
    more: [
      ['Software Engineer II, Platform', 'SWE II role', 'posted 5 days ago', 'San Francisco, CA', false],
      ['Forward Deployed Engineer', 'Early career role', 'posted 12 days ago', 'Remote US', true],
    ],
  },
  Cerebrium: {
    stage: ['Seed', 'guess, its website'], headcount: ['18 people', 'estimate, LinkedIn'], hq: ['New York, NY', 'estimate, LinkedIn'],
    everify: ['E-Verify enrolled', 'confirmed, the E-Verify list, checked Oct 9'], domains: ['Infrastructure', 'AI agents'],
    why: 'GPU infrastructure for AI apps is the stack he builds on.',
  },
  Resend: {
    stage: ['Series A', 'confirmed, SEC Form D'], funding: ['$18,000,000 raised, Form D filed Jun 24, 2026', 'confirmed, SEC Form D'],
    headcount: ['38 people', 'confirmed, its website'], hq: ['San Francisco, CA', 'confirmed, its website'],
    everify: ['E-Verify enrolled', 'confirmed, the E-Verify list, checked Oct 3'], sponsorship: ['Has sponsored before', 'from its LCA filings'],
    lca: ['7 software LCA filings in FY2024 and FY2025', 'DOL disclosure data'], domains: ['Dev tools', 'Infrastructure'],
    why: 'A developer product with a small, senior team.',
    more: [['Software Engineer, Platform', 'SWE I role', 'posted 9 days ago', 'Remote US', true]],
  },
  Baseten: {
    stage: ['Series C', 'confirmed, SEC Form D'], funding: ['$75,000,000 raised, Form D filed Aug 19, 2026', 'confirmed, SEC Form D'],
    headcount: ['150 people', 'estimate, LinkedIn'], hq: ['San Francisco, CA', 'confirmed, its website'],
    sponsorship: ['Has sponsored before', 'from its LCA filings'], lca: ['12 software LCA filings in FY2024 and FY2025', 'DOL disclosure data'],
    domains: ['Infrastructure', 'LLM apps'], why: 'Model serving is infrastructure he knows, with forward deployed roles open.',
  },
  Inngest: {
    stage: ['Seed', 'estimate, Y Combinator'], headcount: ['35 people', 'estimate, LinkedIn'], hq: ['Remote, US', 'confirmed, its website'],
    domains: ['Dev tools'], why: 'A workflow tool for developers, close to what he has built.',
    passes: ['Not now on Oct 28: too early'],
  },
  Turso: {
    stage: ['Seed', 'estimate, Built In'], headcount: ['45 people', 'guess, Hacker News'], hq: ['Remote, worldwide', 'confirmed, its website'],
    sponsorship: ['OPT only', 'confirmed, a posting'], domains: ['Data', 'Dev tools'], why: 'A database company led by its founders.',
  },
  'Trigger.dev': {
    funding: ['$5,500,000 raised, Form D filed Oct 6, 2026', 'confirmed, SEC Form D'],
  },
  Hex: {
    stage: ['Series B', 'confirmed, SEC Form D'], funding: ['$70,000,000 raised, Form D filed Mar 11, 2026', 'confirmed, SEC Form D'],
    headcount: ['180 people', 'estimate, LinkedIn'], hq: ['San Francisco, CA', 'confirmed, its website'],
    lca: ['3 software LCA filings in FY2024 and FY2025', 'DOL disclosure data'], domains: ['Data'],
    more: [['Software Engineer, Data Platform', 'SWE I role', 'posted 20 days ago', 'San Francisco, CA', true]],
  },
  'Retell AI': {
    stage: ['Series A', 'confirmed, SEC Form D'], headcount: ['40 people', 'estimate, LinkedIn'], hq: ['San Francisco, CA', 'confirmed, its website'],
    everify: ['E-Verify enrolled', 'confirmed, the E-Verify list, checked Oct 14'], domains: ['Voice', 'AI agents'],
    why: 'Voice agents are the work he is aiming at.',
  },
  Railway: {
    stage: ['Series B', 'confirmed, SEC Form D'], headcount: ['60 people', 'confirmed, its website'], hq: ['San Francisco, CA', 'confirmed, its website'],
    sponsorship: ['Has sponsored before', 'from its LCA filings'], lca: ['5 software LCA filings in FY2024 and FY2025', 'DOL disclosure data'],
    domains: ['Infrastructure', 'Dev tools'],
  },
  Temporal: {
    stage: ['Series D', 'confirmed, SEC Form D'], headcount: ['300 people', 'estimate, LinkedIn'], hq: ['Seattle, WA', 'confirmed, its website'],
    everify: ['E-Verify enrolled', 'confirmed, the E-Verify list, checked Oct 3'], sponsorship: ['Has sponsored before', 'from its LCA filings'],
    domains: ['Infrastructure', 'Dev tools'],
  },
};

/** "40 people (estimate), Series A" as the facts a company without its own entry falls back to. */
export function factsFromSize(size: string | null): Facts {
  if (!size) return {};
  const [count, ...rest] = size.split(', ');
  const m = /^(\d+) people(?: \((estimate|guess)\))?$/.exec(count ?? '');
  const stage = rest.join(', ');
  return {
    ...(m ? { headcount: [`${m[1]} people`, `${m[2] ?? 'confirmed'}, its website`] as const } : {}),
    ...(stage ? { stage: [stage[0]!.toUpperCase() + stage.slice(1), 'estimate, its website'] as const } : {}),
  };
}

export interface ProfileInput {
  company: string; size: string | null; domain: string | null;
  role: { title: string; url: string; level: string | null; age: string; location: string; early: boolean } | null;
  opening: QueueOpening | null; standing: QueueStanding; onRecord: number; contacted: number; sponsorshipWord: string | null;
  judge?: { why?: string; risk: string; builds: string; fit: number; domains?: string[] }; applied?: IsoDate; extra?: Facts;
}

/** The people line as the API words it: "2 on record, none asked yet", "4 on record, 4 asked, free again Nov 18". */
export function peopleValue(onRecord: number, contacted: number, standing: QueueStanding): ProfileValue {
  if (onRecord === 0) return { text: 'Nobody on record yet', note: null };
  let asked = 'none asked yet';
  if (standing.asked > 0) asked = `${standing.asked} asked${standing.freeOn ? `, free again ${day(standing.freeOn)}` : ''}`;
  else if (contacted > 0) asked = `${contacted} asked before`;
  return { text: `${onRecord} on record, ${asked}`, note: null };
}

export function buildProfile(p: ProfileInput): CompanyProfile {
  const f: Facts = { ...factsFromSize(p.size), ...(FACTS[p.company] ?? {}), ...(p.extra ?? {}) };
  const roles: ProfileRole[] = [
    ...(p.role ? [{ postingId: 0, title: p.role.title, url: p.role.url, level: p.role.level, age: p.role.age, location: p.role.location, earlyFit: p.role.early }] : []),
    ...(f.more ?? []).map(([title, level, age, location, earlyFit], i) => ({
      postingId: 0, title, url: `https://${slug(p.company)}.example/careers/${slug(title)}-${i + 2}`, level, age, location, earlyFit,
    })),
  ].map((r, i) => ({ ...r, postingId: 9000 + i }));
  const places = [...new Set(roles.map((r) => r.location).filter(Boolean))].slice(0, 3);
  const applied = p.opening?.appliedNote ?? (p.applied ? `You applied on ${day(p.applied)}.` : null);
  const j = p.judge;
  return {
    builds: j ? { text: j.builds, note: 'Judged Nov 9' } : NOT_KNOWN,
    domains: j ? (j.domains ?? f.domains ?? []) : [],
    verdict: j ? { text: j.why ?? f.why ?? null, note: null } : NOT_KNOWN,
    risk: j?.risk ?? null,
    stage: value(f.stage), funding: value(f.funding), headcount: value(f.headcount), hq: value(f.hq),
    rolePlaces: places.length ? { text: places.join('; '), note: null } : NOT_KNOWN,
    everify: value(f.everify), sponsorship: value(f.sponsorship), lca: value(f.lca),
    roles: roles.slice(0, 5),
    people: peopleValue(p.onRecord, p.contacted, p.standing),
    history: { applied, passes: f.passes ?? [] },
  };
}
