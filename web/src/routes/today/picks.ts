import type { BenchCompany, PassReason, PlanRow } from '@/api';

/** Not now's reasons, in chip order: keys 1 to 8. None of them is a no-go; each is a 14 day cooldown. */
export const PASS_REASONS: readonly { reason: PassReason; label: string; key: string }[] = [
  { reason: 'visa', label: 'Visa worry', key: '1' },
  { reason: 'too_big', label: 'Too big', key: '2' },
  { reason: 'too_early', label: 'Too early', key: '3' },
  { reason: 'role', label: 'Role not for me', key: '4' },
  { reason: 'product', label: 'Not into the product', key: '5' },
  { reason: 'know_it', label: 'Already know it', key: '6' },
  { reason: 'not_a_fit', label: 'Not a fit right now', key: '7' },
  { reason: 'other', label: 'Other', key: '8' },
];

/** A company coming in from the bench while its Not now waits in the undo window: it has no row on the API yet. */
export const isProvisional = (row: PlanRow): boolean => row.entryId < 0;

/**
 * The row a bench company shows as on the rail: the API writes the real one when the Not now or Add lands (a new id
 * above every row), so until then it carries a negative id and every key on it says it is still coming in.
 */
export function benchToPick(b: BenchCompany, position: number): PlanRow {
  return {
    entryId: -b.company.id, position, channel: 'linkedin_request', lane: b.lane, company: { ...b.company }, person: null,
    role: b.role, applyUrl: b.role?.url ?? null, reason: b.queue.reasons[0] ?? b.queue.whyToday, reasonSources: [], note: null,
    findSomeone: { searchUrl: b.queue.peopleSearchUrl, handoff: 'Open the search and reach out to the people you pick. Then log who you asked on this row.' },
    emailTouchId: null,
    steps: [{ kind: 'request', touchId: null, at: null }, ...(b.role ? [{ kind: 'apply' as const, touchId: null, at: null }] : [])],
    state: 'todo', carriedFrom: null, revision: 1, queue: b.queue,
  };
}

/** The picks a swap may replace: still to do, nothing logged, not still coming in. */
export const replaceable = (rows: PlanRow[]): PlanRow[] =>
  rows.filter((r) => r.state === 'todo' && r.channel === 'linkedin_request' && !isProvisional(r) && !r.steps.some((s) => s.at !== null));
