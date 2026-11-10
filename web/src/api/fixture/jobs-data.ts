/**
 * Sample data for the job list (#156), as the API answers it: open engineering postings, newest first, then the
 * smaller company, then the posting id; 50 a page by a cursor. 80 postings from 11 companies: every fourth is at a
 * higher level, every fifth is dated by when Knock saw it, and the oldest is 94 days old, so each tab, window and size
 * has something to show.
 */
import type { IsoDate, IsoTime, JobRow, JobsParams, JobsView } from '../types';

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

/** The whole sample, already in list order. `today` is the plan's date. */
export function buildJobs(today: IsoDate): { rows: JobRow[]; higher: ReadonlySet<number> } {
  const higher = new Set<number>();
  const rows = Array.from({ length: 80 }, (_, i): JobRow => {
    const c = COMPANIES[i % COMPANIES.length] as Company;
    const up = i % 4 === 3;
    const ageDays = Math.floor(i * 1.2);
    const id = 9000 + i;
    if (up) higher.add(id);
    const date = dayBefore(today, ageDays);
    const seen = i % 5 === 4;
    return {
      postingId: id,
      title: up ? (HIGHER[(i >> 2) % HIGHER.length] as string) : (TITLES[i % 4] as string),
      url: `https://jobs.example/${nameKey(c.name)}/${id}`,
      location: PLACES[i % PLACES.length] as string,
      levelTag: up ? 'SDE 2' : (TAGS[i % 4] as string),
      date, dateKind: seen ? 'seen' : 'posted', ageDays,
      listAt: `${date}T15:00:00Z`, postedAt: seen ? null : `${date}T15:00:00Z`, firstSeenAt: `${date}T15:10:00Z`,
      seenGapS: 1800, firstRead: false, repostedFrom: null,
      companyId: c.id, companyName: c.name, size: c.size, big: c.guessedBig === true || (c.size !== null && c.size > 300),
      saysNoSponsorship: c.noSponsor === true, everify: c.everify === true, yc: c.yc === true, contactedOn: c.contactedOn ?? null,
    };
  });
  const rank = (r: JobRow) => (r.size === null ? (r.big ? 20_000_000 : 10_000_000) : r.big ? 20_000_000 + r.size : r.size);
  rows.sort((a, b) => b.date.localeCompare(a.date) || rank(a) - rank(b) || a.postingId - b.postingId);
  return { rows, higher };
}

/** The API's job list: the tab, the window, the size, the contacted filter and the company search, then one page after the cursor. */
export function jobsView(all: JobRow[], higher: ReadonlySet<number>, p: JobsParams, asOf: IsoTime, limit = 50): JobsView {
  const key = nameKey(p.q);
  const days = p.window === 'any' ? Infinity : Number(p.window);
  const sizeOk = (r: JobRow) => p.size === 'any' || (p.size === 'unknown' ? r.size === null && !r.big : p.size === 'big' ? r.big : r.size !== null && !r.big);
  const base = all
    .filter((r) => r.ageDays <= days)
    .filter(sizeOk)
    .filter((r) => !p.notContacted || r.contactedOn === null)
    .filter((r) => !p.q.trim() || (key !== '' && nameKey(r.companyName).includes(key)));
  const mine = base.filter((r) => higher.has(r.postingId) === (p.view === 'higher'));
  const at = p.after ? mine.findIndex((r) => String(r.postingId) === p.after) + 1 : 0;
  const page = mine.slice(at, at + limit);
  const last = page[page.length - 1];
  return {
    asOf, rows: page, total: mine.length,
    nextAfter: at + limit < mine.length && last ? String(last.postingId) : null,
    counts: { yourLevel: base.filter((r) => !higher.has(r.postingId)).length, higher: base.filter((r) => higher.has(r.postingId)).length },
  };
}
