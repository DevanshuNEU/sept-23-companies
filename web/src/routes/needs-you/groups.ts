import type { IsoTime, ResolvedReview, ReviewItem, ReviewKind } from '@/api';
import { ageDays, formatDay } from '@/lib/format';

const WEEKDAY = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', weekday: 'short' });
const FROM_MONDAY: Record<string, number> = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 };

/**
 * Resolved rows under their real day, in their order (newest first): "Today", "Earlier this week" for the rest
 * of the New York week that began on Monday, and any older row under its own date ("Fri Nov 6").
 */
export function resolvedGroups(rows: ResolvedReview[], asOf: IsoTime): { title: string; rows: ResolvedReview[] }[] {
  const intoWeek = FROM_MONDAY[WEEKDAY.format(new Date(asOf))] ?? 0;
  const groups: { title: string; rows: ResolvedReview[] }[] = [];
  for (const r of rows) {
    const days = ageDays(r.at, asOf);
    const title = days <= 0 ? 'Today' : days <= intoWeek ? 'Earlier this week' : formatDay(r.at);
    const last = groups.find((g) => g.title === title);
    if (last) last.rows.push(r);
    else groups.push({ title, rows: [r] });
  }
  return groups;
}

/** The kinds that ask for a company's size or email domain. Two of them open for one company share a row (#288). */
export const DETAILS: readonly ReviewKind[] = ['size_unknown', 'size_guess', 'size_check', 'domain_missing'];

/** What one row on the page settles: the item that stands for it in the list, and every item it holds. */
export type CompanyRow = { lead: ReviewItem; members: ReviewItem[] };

/**
 * Rows for a list of open items, in the list's order. Items that ask for a size or an email domain and share a
 * company become one row at the place of the first of them; every other item is a row of its own.
 */
export function companyRows(items: ReviewItem[]): CompanyRow[] {
  const byCompany = new Map<number, ReviewItem[]>();
  for (const r of items) {
    if (!DETAILS.includes(r.kind)) continue;
    byCompany.set(r.company.id, [...(byCompany.get(r.company.id) ?? []), r]);
  }
  const rows: CompanyRow[] = [];
  for (const r of items) {
    const group = DETAILS.includes(r.kind) ? byCompany.get(r.company.id) : undefined;
    if (group && group.length > 1) {
      if (group[0] === r) rows.push({ lead: r, members: group });
    } else {
      rows.push({ lead: r, members: [r] });
    }
  }
  return rows;
}

const tier = (r: CompanyRow): number =>
  r.members.some((m) => m.company.picked) ? 0 : r.members.some((m) => m.holdsEmail) ? 1 : r.members.some((m) => m.inPlay) ? 2 : 3;

/**
 * Rows in the order he should meet them: what he picked today, then what holds up an email, then what is in play,
 * then the rest. Inside a tier the newer question comes first (a row that has waited nearly two weeks has not been
 * missed by accident), and equal rows keep the API's order.
 */
export function byPriority(rows: CompanyRow[]): CompanyRow[] {
  const age = (r: CompanyRow) => Math.min(...r.members.map((m) => m.ageDays));
  return [...rows].sort((a, b) => tier(a) - tier(b) || age(a) - age(b));
}
