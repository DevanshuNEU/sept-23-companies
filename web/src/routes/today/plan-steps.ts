import type { Id, IsoTime, PlanRow, PlanView, StepKind } from '@/api';

/**
 * What the one primary button does for this row. `find_someone` comes before any step: a row with nobody on it
 * has nothing to log. A done or skipped row has no next step.
 */
export function nextStep(row: PlanRow): StepKind | 'review_email' | 'find_someone' | null {
  if (row.state !== 'todo') return null;
  if (row.person === null && row.channel !== 'email') return 'find_someone';
  if (row.channel === 'email') return 'review_email';
  return row.steps.find((s) => s.at === null)?.kind ?? null;
}

/** Lays one logged step over the plan. A step already logged, or a row or step that is not there, changes nothing. */
export function withStepLogged(plan: PlanView, entryId: Id, kind: StepKind, at: IsoTime): PlanView {
  const row = plan.rows.find((r) => r.entryId === entryId);
  const step = row?.steps.find((s) => s.kind === kind);
  if (!row || !step || step.at !== null) return plan;
  const steps = row.steps.map((s) => (s.kind === kind ? { ...s, at } : s));
  const state = row.state === 'todo' && steps.every((s) => s.at !== null) ? 'done' : row.state;
  const rows = plan.rows.map((r) => (r.entryId === entryId ? { ...r, steps, state } : r));
  return { ...plan, rows, done: rows.filter((r) => r.state === 'done').length };
}

/** After he finishes or skips a row, the next one waiting; with none after it, the nearest one before it. */
export function nextTodoAfter(rows: PlanRow[], entryId: Id): Id | null {
  const at = rows.findIndex((r) => r.entryId === entryId);
  if (at === -1) return null;
  const after = rows.slice(at + 1).find((r) => r.state === 'todo');
  if (after) return after.entryId;
  return rows.slice(0, at).reverse().find((r) => r.state === 'todo')?.entryId ?? null;
}
