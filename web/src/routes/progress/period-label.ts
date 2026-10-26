import type { ProgressPeriod } from '@/api';
import { formatDateMonthDay } from '@/routes/today/short-dates';

/** "Nov 4 to 10", or "Oct 28 to Nov 3" across a month. Calendar dates: no zone moves them. */
export function periodRange(p: Pick<ProgressPeriod, 'from' | 'to'>): string {
  const from = formatDateMonthDay(p.from);
  const to = formatDateMonthDay(p.to);
  const sameMonth = from.split(' ')[0] === to.split(' ')[0];
  return `${from} to ${sameMonth ? to.split(' ')[1] : to}`;
}

/** The period's heading: the API's label and the dates it covers. */
export const periodHeading = (p: ProgressPeriod) => `${p.label}, ${periodRange(p)}`;
