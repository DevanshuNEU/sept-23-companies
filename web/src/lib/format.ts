import type { IsoDate, IsoTime, JobRow } from '@/api';

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

/** "Today", "Tomorrow" or "Mon Nov 9" for the New York day an email is expected to send, counted from `asOf`. */
export function formatSendDay(on: IsoDate, asOf: IsoTime): string {
  const n = dayNumber(parseIsoDate(on)) - dayNumber(appDate(asOf));
  if (n <= 0) return 'Today';
  return n === 1 ? 'Tomorrow' : formatDueDay(on);
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

/** Noon UTC on a calendar date, read in UTC, so no zone moves the day. */
const noonOf = (on: IsoDate) => {
  const { y, m, d } = parseIsoDate(on);
  return new Date(Date.UTC(y, m - 1, d, 12));
};

/** "Tuesday": the weekday of an instant, read in the app's zone. */
export function formatWeekday(at: IsoTime): string {
  return partsOf(at, { timeZone: APP_ZONE, weekday: 'long' }).weekday ?? '';
}

const MONTH_NAME = new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', month: 'long' });

/** "October" for a calendar date. A date is a calendar date: no zone moves it. */
export function formatMonthName(on: IsoDate): string {
  return MONTH_NAME.format(noonOf(on));
}

/** "Nov 8": the day of an instant in the app's zone, with ", 2025" when it is not `asOf`'s year. */
function monthDay(at: IsoTime, asOf: IsoTime): string {
  const p = partsOf(at, { timeZone: APP_ZONE, year: 'numeric', month: 'short', day: 'numeric' });
  return appDate(at).y === appDate(asOf).y ? `${p.month} ${p.day}` : `${p.month} ${p.day}, ${p.year}`;
}

/** "just now", "6 min ago", "1 hour ago", "3 hours ago": how long before `asOf`, for a time earlier today. */
function sinceToday(at: IsoTime, asOf: IsoTime): string {
  const min = Math.floor((Date.parse(asOf) - Date.parse(at)) / 60_000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min} min ago`;
  const h = Math.floor(min / 60);
  return h === 1 ? '1 hour ago' : `${h} hours ago`;
}

type JobTime = Pick<JobRow, 'listAt' | 'postedAt' | 'firstSeenAt' | 'firstRead'>;

const same = (a: IsoTime, b: IsoTime) => Date.parse(a) === Date.parse(b);

/**
 * Where a job's list time came from: the board's own time, a board that gives only the date, or Knock's first sighting
 * ('seen'; also a row with no date, and a row from before list times whose date was its first-seen day).
 */
function timeKind(j: JobTime): 'board_time' | 'board_date' | 'seen' {
  if (j.listAt === null) return 'seen';
  if (j.postedAt !== null) return same(j.listAt, j.postedAt) ? 'board_time' : 'seen';
  return same(j.listAt, j.firstSeenAt) ? 'seen' : 'board_date';
}

/**
 * A job row's time, in New York: "posted 10:08, 6 min ago" (the board's time, today), "posted Nov 8, 21:30" (the
 * board's time, an earlier day), "posted Nov 8" (a board that gives only the date), "first seen 10:42" or "first seen
 * Nov 6, 10:10" (no board time). A row with no date, or from before list times, has only its first-seen day.
 */
export function postedLabel(j: JobTime, asOf: IsoTime): string {
  const kind = timeKind(j);
  if (kind === 'board_time' && j.listAt !== null) {
    const at = j.listAt;
    return ageDays(at, asOf) <= 0 ? `posted ${formatClockTime(at)}, ${sinceToday(at, asOf)}` : `posted ${monthDay(at, asOf)}, ${formatClockTime(at)}`;
  }
  if (kind === 'board_date' && j.listAt !== null) return ageDays(j.listAt, asOf) <= 0 ? 'posted today' : `posted ${monthDay(j.listAt, asOf)}`;
  const at = j.firstSeenAt;
  const today = ageDays(at, asOf) <= 0;
  if (j.listAt === null || j.firstRead) return today ? 'first seen today' : `first seen ${monthDay(at, asOf)}`;
  return today ? `first seen ${formatClockTime(at)}` : `first seen ${monthDay(at, asOf)}, ${formatClockTime(at)}`;
}

/** Over this many seconds between the board's last read and the first sighting, the row says how long it was. */
// Keep in step with GAP_TAG_S in outreach/crawl/row_clock.py.
const GAP_TAG_S = 7200;

/**
 * "Found after a 3 day gap": on a row dated by its first sighting, how long the board had gone unread before it, when
 * that is over 2 hours. A row with the board's own time or date gets none: the gap does not move its time.
 */
export function gapTag(j: JobTime & Pick<JobRow, 'seenGapS'>): string | null {
  if (j.seenGapS === null || j.seenGapS <= GAP_TAG_S || j.listAt === null || timeKind(j) !== 'seen') return null;
  const days = Math.floor(j.seenGapS / 86_400);
  return days >= 1 ? `Found after a ${days} day gap` : `Found after a ${Math.floor(j.seenGapS / 3600)} hour gap`;
}

/** "Reposted, first seen Oct 2": a repost, with the day Knock first saw the posting it repeats. */
export function repostTag(repostedFrom: IsoTime | null, asOf: IsoTime): string | null {
  return repostedFrom === null ? null : `Reposted, first seen ${monthDay(repostedFrom, asOf)}`;
}
