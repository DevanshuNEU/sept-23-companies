/**
 * Sample data for Browse companies (#120), as the API answers it: every company the plan could pick, in stored rank
 * order, paged by a cursor (`after` is the stored position of the last row seen). The pool is today's picks, the
 * bench and 30 more companies; the page lists the ones not on today's list. A no-go company and one in flight are not
 * in the pool at all (the API never lists them); a company held back is, with its reason in `blocked`.
 */
import type { BenchCompany, BrowseCompany, BrowseParams, BrowseView, Id, PlanRow } from '../types';

/** The API's `name_key` in the small: lower case, letters and digits only. A query with none of either finds nothing. */
export const nameKey = (text: string): string => text.toLowerCase().replace(/[^a-z0-9]/g, '');

/** A plan row in the shape of a bench company: the company, its role and the facts the row already shows. */
export function rowToBench(row: PlanRow): BenchCompany | null {
  if (!row.queue) return null;
  const opening = row.queue.opening;
  return {
    company: { ...row.company }, position: row.position, lane: row.lane, role: row.role, queue: structuredClone(row.queue),
    fresh: opening?.fresh ?? false, earlyFit: opening !== null && opening.fresh && opening.earlyFit,
  };
}

/** The bench's own order: new early career fits first, then other new openings, then by stored rank. */
export function benchOrder(companies: BenchCompany[], rank: (id: Id) => number): BenchCompany[] {
  const group = (b: BenchCompany) => (b.earlyFit ? 0 : b.fresh ? 1 : 2);
  return [...companies].sort((a, b) => group(a) - group(b) || rank(a.company.id) - rank(b.company.id));
}

const EXTRA_NAMES = [
  'Axiom', 'Basalt', 'Cobalt Labs', 'Dovetail', 'Elastic Bits', 'Fathom', 'Grove', 'Helix', 'Ironwood', 'Juniper',
  'Kestrel', 'Lumen', 'Mosaic', 'Nimbus', 'Orbit', 'Prism', 'Quill', 'Relay', 'Sable', 'Tidal',
  'Umbra', 'Vector', 'Willow', 'Xenon', 'Yarrow', 'Zephyr', 'Atlas Works', 'Bramble', 'Cinder', 'Delta Forge',
];

/**
 * 30 companies below the bench, cloned from `template` (a bench company with a role). Every third has no role (the
 * `reason_no_role` lane), every seventh says it does not sponsor, every ninth was worked before, and one is held
 * back by a Not now, so each filter and the blocked line have something to show.
 */
export function buildExtras(template: BenchCompany, firstId: Id): BenchCompany[] {
  return EXTRA_NAMES.map((name, i): BenchCompany => {
    const slug = nameKey(name);
    const b = structuredClone(template);
    const noRole = i % 3 === 2;
    const id = firstId + i;
    b.company = { ...b.company, id, name, domain: `${slug}.example` };
    b.lane = noRole ? 'reason_no_role' : 'level_role';
    b.fresh = false;
    b.earlyFit = false;
    const q = b.queue;
    q.reasons = noRole ? ['Small company'] : ['Open role at your level', 'Small company'];
    q.whyToday = noRole ? 'Small company' : q.reasons[0] ?? 'Ranked for today.';
    q.sizeLine = `${20 + i * 4} people (estimate)`;
    q.sponsorshipLine = i % 7 === 0 ? 'Says it does not sponsor' : 'Sponsorship not stated';
    q.websiteUrl = `https://${slug}.example`;
    q.careersUrl = `https://${slug}.example/careers`;
    q.linkedinCompanyUrl = `https://www.linkedin.com/search/results/companies/?keywords=${encodeURIComponent(name)}`;
    q.peopleSearchUrl = `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(name)}`;
    q.terminalLine = `${name} | ${id}`;
    if (noRole) {
      b.role = null;
      q.opening = null;
      q.roleLine = null;
    } else if (b.role) {
      b.role = { ...b.role, url: `https://${slug}.example/careers/1` };
      if (q.opening) q.opening = { ...q.opening, url: `https://${slug}.example/careers/1`, fresh: false, earlyFit: false, freshTag: null, appliedNote: null };
    }
    return b;
  });
}

/** Each pool company as a Browse row: `blocked` and `worked` are fixture facts, keyed by company name. */
const BLOCKED: Record<string, string> = {
  Cinder: 'You said Not now to Cinder on Nov 2. It comes back on Nov 16, or sooner with a new opening.',
};
const WORKED_EVERY = 9;

export function toBrowse(b: BenchCompany, position: number, index: number): BrowseCompany {
  return { ...structuredClone(b), position, blocked: BLOCKED[b.company.name] ?? null, worked: index % WORKED_EVERY === 0 && index > 0 };
}

/** The API's browse: stored order, the search and the three filters, then one page after the cursor. */
export function browseView(pool: BrowseCompany[], listed: ReadonlySet<Id>, p: BrowseParams, asOf: string, date: string, limit = 25): BrowseView {
  const key = nameKey(p.q);
  const fits = pool
    .filter((c) => !listed.has(c.company.id))
    .filter((c) => !p.q.trim() || (key !== '' && nameKey(c.company.name).includes(key)))
    .filter((c) => !p.levelRole || c.lane === 'level_role')
    .filter((c) => !p.visaOk || !/does not sponsor|no sponsorship/i.test(c.queue.sponsorshipLine))
    .filter((c) => !p.unworked || !c.worked)
    .sort((a, b) => a.position - b.position);
  const ahead = fits.filter((c) => c.position > p.after);
  const page = ahead.slice(0, limit);
  const last = page[page.length - 1];
  return { asOf, date, companies: page, total: fits.length, nextAfter: ahead.length > page.length && last ? last.position : null };
}
