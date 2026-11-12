/**
 * Sample data for the job list (#156, #193), as the API answers it: open engineering postings, newest first by their
 * list time, then the smaller company, then the posting id; the Date unknown group last; 50 a page by a cursor. 82
 * postings from 11 companies: every fourth is at a higher level; most carry the board's own posting time, every fifth
 * (from the third) only a date, and every fifth (from the fifth) is dated by when Knock first saw it. The newest went
 * up 6 minutes before the sample's clock and the second is a repost; the fifth was found after a 3 day gap; the oldest
 * 20 are from before list times (their day only), the oldest is 94 days old; and 2 at your level have no date at all,
 * so each tab, window and size has something to show. Three more are not software roles (#250): they are only on
 * the Other roles tab.
 */
import type { IsoDate, IsoTime, JobLevel, JobRow, JobsParams, JobsView } from '../types';

/** The API's `name_key` in the small: lower case, letters and digits only. A search with none of either finds nothing. */
const nameKey = (text: string): string => text.toLowerCase().replace(/[^a-z0-9]/g, '');

interface Company { id: number; name: string; size: number | null; guessedBig?: boolean; yc?: boolean; everify?: boolean; noSponsor?: boolean; contactedOn?: IsoDate }

const COMPANIES: Company[] = [
  { id: 7001, name: 'Retell AI', size: 45, yc: true, everify: true },
  { id: 7002, name: 'Pylon', size: 120 },
  { id: 7003, name: 'Cerebrium', size: 18, contactedOn: '2026-11-03' },
  { id: 7004, name: 'Resend', size: 300, everify: true },
  { id: 7005, name: 'Baseten', size: 640 },
  { id: 7006, name: 'Helix', size: 2400, noSponsor: true },
  { id: 7007, name: 'Axiom', size: null },
  { id: 7008, name: 'Juniper', size: 75, noSponsor: true },
  { id: 7009, name: 'Kestrel', size: 1200 },
  { id: 7010, name: 'Lumen', size: 30, yc: true },
  { id: 7011, name: 'Granite Systems', size: null, guessedBig: true },
];

const TITLES = ['Software Engineer, New Grad', 'Software Engineer, Early Career', 'Software Engineer I', 'Software Engineer, Backend'];
const HIGHER = ['Software Engineer II', 'Senior Software Engineer', 'Software Engineer, Infrastructure', 'Staff Software Engineer'];
const TAGS = ['new grad', 'early career', 'SDE 1', 'level not stated'];
const PLACES = ['San Francisco, CA', 'New York, NY', 'Remote (US)', 'Austin, TX', 'Seattle, WA'];

const dayNumber = (on: IsoDate): number => {
  const [y, m, d] = on.split('-').map(Number) as [number, number, number];
  return Date.UTC(y, m - 1, d) / 86_400_000;
};
const dayBefore = (on: IsoDate, days: number): IsoDate => new Date((dayNumber(on) - days) * 86_400_000).toISOString().slice(0, 10);

const two = (n: number) => String(n).padStart(2, '0');
const at = (on: IsoDate, h: number, m: number): IsoTime => `${on}T${two(h)}:${two(m)}:00Z`;
/** The postings that are not a software role (#250), by id: only the Other roles tab lists them. */
export const OTHER_ROLE_IDS: ReadonlySet<number> = new Set([9082, 9083, 9084]);
const OLD_FROM = 60; // rows from here on are from before list times: no seen gap, their day only

/**
 * The whole sample, already in list order. `today` is the plan's date and `asOf` its clock; `ages` is each dated row's
 * age in days, for the windows.
 */
export function buildJobs(today: IsoDate, asOf: IsoTime): { rows: JobRow[]; higher: ReadonlySet<number>; ages: ReadonlyMap<number, number> } {
  const higher = new Set<number>();
  const ages = new Map<number, number>();
  const company = (c: Company) => ({
    companyId: c.id, companyName: c.name, size: c.size, big: c.guessedBig === true || (c.size !== null && c.size > 300),
    saysNoSponsorship: c.noSponsor === true, everify: c.everify === true, yc: c.yc === true, contactedOn: c.contactedOn ?? null,
  });
  const rows = Array.from({ length: 80 }, (_, i): JobRow => {
    const c = COMPANIES[i % COMPANIES.length] as Company;
    const up = i % 4 === 3;
    const age = Math.floor(i * 1.2);
    const id = 9000 + i;
    if (up) higher.add(id);
    ages.set(id, age);
    const date = dayBefore(today, age);
    const old = i >= OLD_FROM;
    const kind = i % 5 === 4 ? 'seen' : i % 5 === 2 || old ? 'date' : 'time';
    // The newest went up 6 minutes before the clock; the other board times vary through the day.
    const posted = i === 0 ? new Date(Date.parse(asOf) - 6 * 60_000).toISOString().replace('.000Z', 'Z') : at(date, 13 + (i % 9), (i * 13) % 60);
    const firstSeenAt = i === 0 ? new Date(Date.parse(asOf) - 4 * 60_000).toISOString().replace('.000Z', 'Z') : at(date, 15, 10);
    return {
      postingId: id,
      title: up ? (HIGHER[(i >> 2) % HIGHER.length] as string) : (TITLES[i % 4] as string),
      url: `https://jobs.example/${nameKey(c.name)}/${id}`,
      location: PLACES[i % PLACES.length] as string,
      levelTag: up ? 'SDE 2' : (TAGS[i % 4] as string),
      listAt: kind === 'time' ? posted : kind === 'date' ? at(date, 5, 0) : firstSeenAt,
      postedAt: kind === 'time' ? posted : null, firstSeenAt,
      seenGapS: old ? null : i === 4 ? 3 * 86_400 + 3600 : 1800, firstRead: old,
      repostedFrom: i === 1 ? '2026-10-02T14:30:00Z' : null,
      ...company(c),
    };
  });
  // No date: a board with no posting time, found on its first read.
  const unknown = (id: number, c: Company, title: string, firstSeenAt: IsoTime, place: string): JobRow => ({
    postingId: id, title, url: `https://jobs.example/${nameKey(c.name)}/${id}`, location: place, levelTag: 'new grad',
    listAt: null, postedAt: null, firstSeenAt, seenGapS: null, firstRead: true, repostedFrom: null, ...company(c),
  });
  rows.push(
    unknown(9080, COMPANIES[9] as Company, 'Software Engineer, Platform', at(dayBefore(today, 1), 14, 0), 'New York, NY'),
    unknown(9081, COMPANIES[1] as Company, 'Software Engineer, New Grad', at(dayBefore(today, 2), 14, 0), 'Remote (US)'),
  );
  // Not software roles (#250): dated, at his level by their tags, and on the Other roles tab only.
  (['Data Scientist I', 'Design Engineer', 'DevOps Engineer'] as const).forEach((title, n) => {
    const id = 9082 + n;
    const c = COMPANIES[(n * 3) % COMPANIES.length] as Company;
    const listAt = at(dayBefore(today, 1 + n * 4), 16, 20);
    ages.set(id, 1 + n * 4);
    rows.push({
      postingId: id, title, url: `https://jobs.example/${nameKey(c.name)}/${id}`, location: 'Austin, TX', levelTag: 'early career',
      listAt, postedAt: listAt, firstSeenAt: listAt, seenGapS: 1800, firstRead: false, repostedFrom: null, ...company(c),
    });
  });
  sortJobs(rows);
  return { rows, higher, ages };
}

/** The list order, in place: dated rows newest first, then the smaller company, then the posting id; Date unknown last. */
export function sortJobs(rows: JobRow[]): JobRow[] {
  const rank = (r: JobRow) => (r.size === null ? (r.big ? 20_000_000 : 10_000_000) : r.big ? 20_000_000 + r.size : r.size);
  const key = (r: JobRow) => r.listAt ?? r.firstSeenAt;
  return rows.sort((a, b) => Number(a.listAt === null) - Number(b.listAt === null) || key(b).localeCompare(key(a)) || rank(a) - rank(b) || a.postingId - b.postingId);
}

/**
 * Check now on the sample (#194): after a click the check moves one step of this walk a second (`CHECK_STEP_MS`),
 * queued, then running, then done, so it ends about four seconds later; reading the status never moves it. `found` is
 * how many new postings the check has found by then; only the first check of a session finds any.
 */
export const CHECK_BOARDS = 4013;
export const CHECK_STEP_MS = 1000;
export const CHECK_WALK: readonly { state: 'queued' | 'running' | 'done'; checked: number; found: number }[] = [
  { state: 'queued', checked: 0, found: 0 },
  { state: 'running', checked: 1240, found: 3 },
  { state: 'running', checked: 2710, found: 4 },
  { state: 'running', checked: 3655, found: 6 },
  { state: 'done', checked: CHECK_BOARDS, found: 6 },
];
/** The sample check took 3 min 10 s. */
export const CHECK_TOOK_S = 190;

/** The postings the sample check finds, newest first: posted in the minutes before `asOf` and first seen at it, all at his level. */
export function foundJobs(asOf: IsoTime): JobRow[] {
  const picks: [Company, string, string][] = [
    [COMPANIES[1] as Company, 'Software Engineer, New Grad', 'New York, NY'],
    [COMPANIES[9] as Company, 'Software Engineer, Early Career', 'San Francisco, CA'],
    [COMPANIES[3] as Company, 'Software Engineer I', 'Remote (US)'],
    [COMPANIES[6] as Company, 'Software Engineer, Backend', 'Seattle, WA'],
    [COMPANIES[0] as Company, 'Software Engineer, Product', 'San Francisco, CA'],
    [COMPANIES[7] as Company, 'Software Engineer, New Grad', 'Austin, TX'],
  ];
  const minus = (s: number) => new Date(Date.parse(asOf) - s * 1000).toISOString().replace('.000Z', 'Z');
  return picks.map(([c, title, location], i): JobRow => ({
    postingId: 9100 + i, title, url: `https://jobs.example/${nameKey(c.name)}/${9100 + i}`, location, levelTag: i % 2 ? 'early career' : 'new grad',
    listAt: minus(30 * (i + 1)), postedAt: minus(30 * (i + 1)), firstSeenAt: asOf, seenGapS: 1800, firstRead: false, repostedFrom: null,
    companyId: c.id, companyName: c.name, size: c.size, big: c.guessedBig === true || (c.size !== null && c.size > 300),
    saysNoSponsorship: c.noSponsor === true, everify: c.everify === true, yc: c.yc === true, contactedOn: c.contactedOn ?? null,
  }));
}

/** The API's job list: the tab, the window (a row with no date is in every one), the size, the contacted filter and the company search, then one page after the cursor. */
/** A row's level tag to its chip key, as the API splits them (#213). */
const LEVEL_OF_TAG: Record<string, JobLevel> = {
  'new grad': 'new_grad', 'early career': 'early_career', 'SDE 1': 'swe1', 'SDE 2': 'swe2', 'entry level': 'entry', 'level not stated': 'not_stated',
};

export function jobsView(all: JobRow[], higher: ReadonlySet<number>, ages: ReadonlyMap<number, number>, p: JobsParams, asOf: IsoTime, limit = 50): JobsView {
  const key = nameKey(p.q);
  const hours = p.window.endsWith('h') ? Number(p.window.slice(0, -1)) : null;
  const days = hours !== null || p.window === 'any' ? Infinity : Number(p.window);
  const now = Date.parse(asOf);
  // As the API: an hour window reads the list time and leaves out Date unknown rows (#213).
  const inWindow = (r: JobRow) =>
    hours !== null ? r.listAt !== null && now - Date.parse(r.listAt) <= hours * 3_600_000 : r.listAt === null || (ages.get(r.postingId) ?? Infinity) <= days;
  const levelOk = (r: JobRow) => !p.levels?.length || p.levels.includes(LEVEL_OF_TAG[r.levelTag] ?? 'not_stated');
  const sizeOk = (r: JobRow) => p.size === 'any' || (p.size === 'unknown' ? r.size === null && !r.big : p.size === 'big' ? r.big : r.size !== null && !r.big);
  const base = all
    .filter(inWindow)
    .filter(levelOk)
    .filter(sizeOk)
    .filter((r) => !p.notContacted || r.contactedOn === null)
    .filter((r) => !p.q.trim() || (key !== '' && nameKey(r.companyName).includes(key)));
  const group = (r: JobRow) => (OTHER_ROLE_IDS.has(r.postingId) ? 'other' : higher.has(r.postingId) ? 'higher' : 'your_level');
  const mine = base.filter((r) => group(r) === p.view);
  const at = p.after ? mine.findIndex((r) => String(r.postingId) === p.after) + 1 : 0;
  const page = mine.slice(at, at + limit);
  const last = page[page.length - 1];
  return {
    asOf, rows: page, total: mine.length,
    nextAfter: at + limit < mine.length && last ? String(last.postingId) : null,
    counts: {
      yourLevel: base.filter((r) => group(r) === 'your_level').length,
      higher: base.filter((r) => group(r) === 'higher').length,
      other: base.filter((r) => group(r) === 'other').length,
    },
  };
}
