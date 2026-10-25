import type { IsoTime, ResolvedReview } from '@/api';
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
