import type { PlanView } from '@/api';
import { FixtureClient } from '@/api/fixture/client';
import { nextStep, nextTodoAfter, withStepLogged } from './plan-steps';

const planOf = (): Promise<PlanView> => new FixtureClient().plan();
const entry = (plan: PlanView, n: number) => {
  const row = plan.rows[n - 1];
  if (!row) throw new Error(`No row ${n}`);
  return row;
};

describe('nextStep', () => {
  it('is request for row 3, apply once its request is logged, null for a done row', async () => {
    const plan = await planOf();
    expect(nextStep(entry(plan, 3))).toBe('request');
    const after = withStepLogged(plan, entry(plan, 3).entryId, 'request', plan.asOf);
    expect(nextStep(entry(after, 3))).toBe('apply');
    expect(nextStep(entry(plan, 1))).toBeNull();
  });

  it('is find_someone with nobody on the row and review_email for an email row', async () => {
    const plan = await planOf();
    expect(entry(plan, 5).person).toBeNull();
    expect(nextStep(entry(plan, 5))).toBe('find_someone');
    expect(nextStep(entry(plan, 7))).toBe('review_email');
  });

  it('is null for a skipped row, and for a row whose steps are all logged', async () => {
    const plan = await planOf();
    const skipped = { ...entry(plan, 5), state: 'skipped' as const };
    expect(nextStep(skipped)).toBeNull();
    const row = entry(plan, 3);
    const both = withStepLogged(withStepLogged(plan, row.entryId, 'request', plan.asOf), row.entryId, 'apply', plan.asOf);
    expect(nextStep(entry(both, 3))).toBeNull();
  });
});

describe('withStepLogged', () => {
  it('a request on row 3 leaves done at 2 and the row todo; the apply makes it done and done 3', async () => {
    const plan = await planOf();
    const id = entry(plan, 3).entryId;
    expect(plan.done).toBe(2);
    const first = withStepLogged(plan, id, 'request', plan.asOf);
    expect(first.done).toBe(2);
    expect(entry(first, 3).state).toBe('todo');
    expect(entry(first, 3).steps.find((s) => s.kind === 'request')?.at).toBe(plan.asOf);
    const second = withStepLogged(first, id, 'apply', plan.asOf);
    expect(entry(second, 3).state).toBe('done');
    expect(second.done).toBe(3);
  });

  it('calling it twice is the same as once, and it never changes the input', async () => {
    const plan = await planOf();
    const id = entry(plan, 3).entryId;
    const once = withStepLogged(plan, id, 'request', plan.asOf);
    expect(withStepLogged(once, id, 'request', '2026-11-10T16:00:00Z')).toEqual(once);
    expect(entry(plan, 3).steps.every((s) => s.at === null)).toBe(true);
  });

  it('changes nothing for an unknown row or a step the row does not have', async () => {
    const plan = await planOf();
    expect(withStepLogged(plan, 1, 'request', plan.asOf)).toBe(plan);
    expect(withStepLogged(plan, entry(plan, 7).entryId, 'request', plan.asOf)).toBe(plan);
  });
});

describe('nextTodoAfter', () => {
  it('is the next todo row after it, else the nearest todo before it, else null', async () => {
    const plan = await planOf();
    const ids = plan.rows.map((r) => r.entryId);
    const id = (n: number) => ids[n - 1] as number;
    expect(nextTodoAfter(plan.rows, id(3))).toBe(id(4));
    expect(nextTodoAfter(plan.rows, id(7))).toBe(id(6));
    expect(nextTodoAfter(plan.rows, id(1))).toBe(id(3));
    const skippedAfter = plan.rows.map((r, i) => (i >= 3 ? { ...r, state: 'skipped' as const } : r));
    expect(nextTodoAfter(skippedAfter, id(3))).toBeNull();
    expect(nextTodoAfter(skippedAfter, id(5))).toBe(id(3));
    expect(nextTodoAfter(plan.rows, 999)).toBeNull();
  });
});
