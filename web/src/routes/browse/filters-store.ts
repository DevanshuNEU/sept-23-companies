import type { JobsH1b } from '@/api';

/**
 * The two Jobs filters he keeps between visits (#309): the H-1B chip and One per company. They live in the browser, so
 * no write is needed. A first visit, an unreadable value or a browser that keeps nothing means both are off (Any H-1B,
 * every posting), so the list is never narrowed by something he did not choose.
 */
export const FILTERS_KEY = 'knock.jobs.filters';

export interface KeptFilters { h1b: JobsH1b; onePerCompany: boolean }
export const NO_FILTERS: KeptFilters = { h1b: 'any', onePerCompany: false };

export function readFilters(): KeptFilters {
  try {
    const raw: unknown = JSON.parse(window.localStorage.getItem(FILTERS_KEY) ?? 'null');
    if (typeof raw !== 'object' || raw === null) return NO_FILTERS;
    const { h1b, onePerCompany } = raw as Record<string, unknown>;
    return {
      h1b: h1b === 'has' || h1b === 'none' ? h1b : 'any',
      onePerCompany: onePerCompany === true,
    };
  } catch {
    return NO_FILTERS;
  }
}

export function writeFilters(filters: KeptFilters): void {
  try {
    window.localStorage.setItem(FILTERS_KEY, JSON.stringify(filters));
  } catch {
    // A browser that keeps nothing just starts from Any next time.
  }
}
