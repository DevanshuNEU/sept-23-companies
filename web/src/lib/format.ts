import type { IsoDate, IsoTime } from '@/api';

// The app's zone. Views never read the real clock: every ask takes an explicit instant and zone.
const APP_ZONE = 'America/New_York';
const DAY_MS = 24 * 60 * 60 * 1000;

const ZONE_ABBR: Record<string, string> = {
  'America/New_York': 'ET',
  'America/Chicago': 'CT',
  'America/Denver': 'MT',
  'America/Phoenix': 'MT',
  'America/Los_Angeles': 'PT',
};

type Parts = Partial<Record<Intl.DateTimeFormatPartTypes, string>>;

function partsOf(at: IsoTime, options: Intl.DateTimeFormatOptions): Parts {
  const out: Parts = {};
  for (const p of new Intl.DateTimeFormat('en-US', options).formatToParts(new Date(at))) out[p.type] = p.value;
  return out;
}

function zoneAbbr(at: IsoTime, tz: string): string {
  return ZONE_ABBR[tz] ?? partsOf(at, { timeZone: tz, timeZoneName: 'short' }).timeZoneName ?? tz;
}

/** "10:42 ET", or "Fri Oct 30, 10:02 PT" with `day`. The clock reads in the recipient's zone. */
export function formatRecipientTime(at: IsoTime, tz: string, opts?: { day?: boolean }): string {
  const p = partsOf(at, {
    timeZone: tz,
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  });
  const clock = `${p.hour}:${p.minute} ${zoneAbbr(at, tz)}`;
  return opts?.day ? `${p.weekday} ${p.month} ${p.day}, ${clock}` : clock;
}

/** "10:14", the time of day in the app's zone with no zone suffix. */
export function formatLocalTime(at: IsoTime): string {
  const p = partsOf(at, { timeZone: APP_ZONE, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  return `${p.hour}:${p.minute}`;
}

/** "9:02": the time of day in the app's zone with no leading zero, as the plan, finish and win views write it. */
export function formatClockTime(at: IsoTime): string {
  return formatLocalTime(at).replace(/^0(?=\d:)/, '');
}

export function parseIsoDate(d: IsoDate): { y: number; m: number; d: number } {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d);
  if (!match) throw new Error(`Not a calendar date: ${d}`);
  const y = Number(match[1]);
  const m = Number(match[2]);
  const day = Number(match[3]);
  const check = new Date(Date.UTC(y, m - 1, day));
  if (check.getUTCFullYear() !== y || check.getUTCMonth() !== m - 1 || check.getUTCDate() !== day) {
    throw new Error(`Not a calendar date: ${d}`);
  }
  return { y, m, d: day };
}

/** The calendar date of an instant in the app's zone. */
function appDate(at: IsoTime): { y: number; m: number; d: number } {
  const p = partsOf(at, { timeZone: APP_ZONE, year: 'numeric', month: 'numeric', day: 'numeric' });
  return { y: Number(p.year), m: Number(p.month), d: Number(p.day) };
}

const dayNumber = ({ y, m, d }: { y: number; m: number; d: number }) => Date.UTC(y, m - 1, d) / DAY_MS;

/** "Tue Nov 10" for a calendar date. A date is a calendar date: no zone shifts it. */
export function formatDueDay(on: IsoDate): string {
  const date = parseIsoDate(on);
  // Noon UTC, read in UTC, so the weekday and day never slip.
  const p = new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', weekday: 'short', month: 'short', day: 'numeric' })
    .formatToParts(new Date(Date.UTC(date.y, date.m - 1, date.d, 12)));
  const get = (t: Intl.DateTimeFormatPartTypes) => p.find((x) => x.type === t)?.value ?? '';
  return `${get('weekday')} ${get('month')} ${get('day')}`;
}

/** "Due today", or "Due Fri Nov 13". */
export function formatDue(on: IsoDate, asOf: IsoTime): string {
  if (dayNumber(parseIsoDate(on)) === dayNumber(appDate(asOf))) return 'Due today';
  return `Due ${formatDueDay(on)}`;
}

/** "Fri Nov 6": the day of an instant, read in the app's zone. */
export function formatDay(at: IsoTime): string {
  const p = partsOf(at, { timeZone: APP_ZONE, weekday: 'short', month: 'short', day: 'numeric' });
  return `${p.weekday} ${p.month} ${p.day}`;
}

/** "Mon 08:04": the weekday and time of an instant in the app's zone. */
export function formatWeekdayTime(at: IsoTime): string {
  const p = partsOf(at, { timeZone: APP_ZONE, weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  return `${p.weekday} ${p.hour}:${p.minute}`;
}

/** "Mon Nov 9, 08:04": the day and time of an instant in the app's zone. */
export function formatDayTime(at: IsoTime): string {
  return `${formatDay(at)}, ${formatLocalTime(at)}`;
}

/** "November": the month of an instant in the app's zone. */
export function formatMonth(at: IsoTime): string {
  return partsOf(at, { timeZone: APP_ZONE, month: 'long' }).month ?? '';
}

/** Calendar days between two instants, counted in the app's zone. */
export function ageDays(at: IsoTime, asOf: IsoTime): number {
  return dayNumber(appDate(asOf)) - dayNumber(appDate(at));
}

/** "today", "1 day ago", "21 days ago". */
export function formatAge(at: IsoTime, asOf: IsoTime): string {
  const n = ageDays(at, asOf);
  if (n <= 0) return 'today';
  return n === 1 ? '1 day ago' : `${n} days ago`;
}

const LONG_DAY = new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', weekday: 'long', month: 'long', day: 'numeric' });
const WEEKDAY = new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', weekday: 'long' });

/** Noon UTC on a calendar date, read in UTC, so no zone moves the day. */
const noonOf = (on: IsoDate) => {
  const { y, m, d } = parseIsoDate(on);
  return new Date(Date.UTC(y, m - 1, d, 12));
};

/** "Tuesday, November 10" for a calendar date (the plan's day). */
export function formatLongDate(on: IsoDate): string {
  return LONG_DAY.format(noonOf(on));
}

/**
 * The day a row was carried from: "Monday" within the last week, "Mon Nov 2" before that. The distance comes from
 * `asOf`, never the real clock.
 */
export function formatCarriedDay(from: IsoDate, asOf: IsoTime): string {
  const noon = noonOf(from);
  const days = ageDays(noon.toISOString(), asOf);
  return days >= 1 && days <= 6 ? WEEKDAY.format(noon) : formatDueDay(from);
}

/** "Tuesday": the weekday of an instant, read in the app's zone. */
export function formatWeekday(at: IsoTime): string {
  return partsOf(at, { timeZone: APP_ZONE, weekday: 'long' }).weekday ?? '';
}
