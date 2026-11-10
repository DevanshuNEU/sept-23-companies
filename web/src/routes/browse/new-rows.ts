import type { Id, IsoTime, JobRow } from '@/api';

/**
 * New rows on the job list (#194, design 4.5): which rows are new since his last visit, and which incoming rows wait
 * behind the "N new" button so the list never moves under him.
 */

/** When he last opened the job list: a moment kept in the browser, so no write is needed (design 4.4). */
export const LAST_OPENED_KEY = 'knock.jobs.lastOpened';

/** The stored moment, or null on a first visit (or a value that is not a time). */
export function readLastOpened(): IsoTime | null {
  try {
    const raw = window.localStorage.getItem(LAST_OPENED_KEY);
    return raw !== null && Number.isFinite(Date.parse(raw)) ? raw : null;
  } catch {
    return null;
  }
}

/** Keeps `at` as the moment he last opened the list: when the tab closes or reloads, and when he clicks "N new". */
export function markOpened(at: Date = new Date()): void {
  try {
    window.localStorage.setItem(LAST_OPENED_KEY, at.toISOString());
  } catch {
    // A browser that keeps nothing just marks nothing new next time.
  }
}

/**
 * A row first seen after `since` (his last visit), that is not from a board's first read and not a repost. Nothing is
 * new on a first visit (`since` null).
 */
export function isNew(job: Pick<JobRow, 'firstSeenAt' | 'firstRead' | 'repostedFrom'>, since: IsoTime | null): boolean {
  if (since === null || job.firstRead || job.repostedFrom !== null) return false;
  return Date.parse(job.firstSeenAt) > Date.parse(since);
}

/**
 * What the list shows when the server's rows (`incoming`) differ from the ones on screen (`shown`). At the top with no
 * row open, everything shows at once. Otherwise the shown order stays: each shown row takes the server's copy, a row
 * the server no longer lists leaves (a pass, a closed posting), and `waiting` counts the incoming rows not shown. Rows
 * after the last shown one are the next page he asked for (Show more), not new ones: they join at the end, uncounted,
 * since nothing above them moves.
 *
 * `more`: the server has more pages than are loaded. New rows above then push the last shown rows out of the loaded
 * window; when `incoming` ends on a shown row, the shown rows after it are past the window, not gone, so they stay (Show
 * more brings them back, once each).
 */
export function heldRows(shown: JobRow[], incoming: JobRow[], atTop: boolean, rowOpen: boolean, more = false): { rows: JobRow[]; waiting: number } {
  if (atTop && !rowOpen) return { rows: incoming, waiting: 0 };
  const onScreen = new Set<Id>(shown.map((r) => r.postingId));
  const byId = new Map<Id, JobRow>(incoming.map((r) => [r.postingId, r]));
  const kept = shown.flatMap((r) => byId.get(r.postingId) ?? []);
  let lastShown = incoming.length - 1;
  while (lastShown >= 0 && !onScreen.has((incoming[lastShown] as JobRow).postingId)) lastShown -= 1;
  const waiting = incoming.slice(0, lastShown + 1).filter((r) => !onScreen.has(r.postingId)).length;
  if (more && lastShown >= 0 && lastShown === incoming.length - 1) {
    const cut = shown.findIndex((r) => r.postingId === (incoming[lastShown] as JobRow).postingId);
    return { rows: [...kept, ...shown.slice(cut + 1).filter((r) => !byId.has(r.postingId))], waiting };
  }
  return { rows: [...kept, ...incoming.slice(lastShown + 1)], waiting };
}
