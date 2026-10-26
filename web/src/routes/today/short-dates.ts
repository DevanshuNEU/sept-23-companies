import type { IsoDate, IsoTime } from '@/api';
import { parseIsoDate } from '@/lib/format';

// The app's zone, as in lib/format: a view never reads the real clock or the browser's zone.
const APP_ZONE = 'America/New_York';
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'] as const;

/** "Nov 2": the day of an instant, read in the app's zone. */
export function formatMonthDay(at: IsoTime): string {
  const p = new Intl.DateTimeFormat('en-US', { timeZone: APP_ZONE, month: 'short', day: 'numeric' }).formatToParts(new Date(at));
  const get = (t: Intl.DateTimeFormatPartTypes) => p.find((x) => x.type === t)?.value ?? '';
  return `${get('month')} ${get('day')}`;
}

/** "Oct 12": a calendar date. No zone moves it. */
export function formatDateMonthDay(on: IsoDate): string {
  const { m, d } = parseIsoDate(on);
  return `${MONTHS[m - 1] ?? ''} ${d}`;
}
