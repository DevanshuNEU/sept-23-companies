import type { CompanyEvent, IsoTime } from '@/api';

/** The event kinds of his Done mark on the job list (#195) and of its undo, as the API writes them. */
export const REACHED = 'company_reached';
export const REACHED_CLEARED = 'company_reached_cleared';

/**
 * When his standing Done mark on a company was made, or null when there is none. The company page's events come
 * newest first, so the newest of his marks and clears decides, as the API's own reading does: a mark after a clear
 * counts again. Only his own events count.
 */
export function standingMark(events: readonly CompanyEvent[]): IsoTime | null {
  const last = events.find((e) => e.actor === 'devanshu' && (e.kind === REACHED || e.kind === REACHED_CLEARED));
  return last?.kind === REACHED ? last.at : null;
}
