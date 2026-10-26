import type { BaseRates, PlanRow, ProgressPeriod } from '@/api';

// Honest numbers (spec section 16). Every count sits beside its denominator, no rate shows under the
// minimum sample, and nothing here reads the clock. `enough` comes from the API; the UI never decides it.

/** "9 of 31 (29%)" with enough data, "2 of 9" without (no percent, ever), "None yet" with nothing to count against. */
export function formatRate(count: number, of: number, enough: boolean): string {
  if (of <= 0) return 'None yet';
  if (!enough) return `${count} of ${of}`;
  return `${count} of ${of} (${Math.round((100 * count) / of)}%)`;
}

/** The N in "1 in N": null when there is nothing to divide by. */
export function oneIn(of: number, count: number): number | null {
  if (count <= 0) return null;
  return Math.round(of / count);
}

/** His own base rate in a sentence, only with enough data and at least one reply. */
export function baseRateLine(b: BaseRates): string | null {
  if (!b.enough || b.replies <= 0 || b.requests <= 0) return null;
  const n = oneIn(b.requests, b.replies);
  if (n === null) return null;
  if (n < 2) return 'Most requests get a reply.';
  return `About 1 in ${n} requests gets a reply.`;
}

/** Why no rate shows. Null once the period has enough requests. */
export function smallSampleNote(p: Pick<ProgressPeriod, 'enough'>, requests: number, minRequests: number): string | null {
  if (p.enough) return null;
  // minRequests is the API's minimum sample. It stays in the signature so a caller can pass the whole
  // context; the sentence names what he has done, not the bar.
  void minRequests;
  const noun = requests === 1 ? 'request' : 'requests';
  return `Only ${requests} ${noun} so far. Rates this small swing a lot, so none are shown.`;
}

/** What today's plan has done so far, in a fixed order, zero counts left out. */
export function didToday(rows: PlanRow[]): { label: string; count: number }[] {
  const doneSteps = (kind: 'request' | 'apply') =>
    rows.reduce((n, r) => n + r.steps.filter((s) => s.kind === kind && s.at !== null).length, 0);
  const emails = rows.filter((r) => r.channel === 'email' && r.state === 'done').length;
  return [
    { label: 'Requests sent', count: doneSteps('request') },
    { label: 'Applications sent', count: doneSteps('apply') },
    { label: 'Emails reviewed', count: emails },
  ].filter((d) => d.count > 0);
}

/** The smallest group a rate may be drawn from (the findings rule, ruling 13). */
export const MIN_GROUP = 20;

/**
 * Whether a stage may show a percent: the API says the period has enough requests AND the stage's own denominator
 * has at least MIN_GROUP in it, so a stage measured against 2 replies shows "0 of 2" and never "0%".
 */
export function rateEnough(periodEnough: boolean, of: number): boolean {
  return periodEnough && of >= MIN_GROUP;
}
