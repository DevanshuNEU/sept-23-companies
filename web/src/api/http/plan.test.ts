import { planMethods } from './plan';
import type { Transport } from './transport';
import type { PlanView } from '../types';

const EMPTY: PlanView = {
  asOf: '2026-11-10T15:14:00Z', date: '2026-11-10', builtAt: null, listJob: null,
  notReady: { reason: 'not_built', text: "Today's plan is not built yet. It builds at 8:00 on weekdays." },
  rows: [], done: 0, total: 0,
  linkedinWeek: { used: 0, limit: 150, hardUsed: 0, hardLimit: 200 },
  tomorrow: { total: 7, text: 'Tomorrow: 7 people.', preview: [] },
  week: { requests: 0, accepts: 0, replies: 0 },
  inFlight: [],
};

describe('planMethods', () => {
  it('reads /api/plan and hands back the view as it came', async () => {
    const get = vi.fn(async () => EMPTY);
    const t = { get, send: vi.fn() } as unknown as Transport;
    expect(await planMethods(t).plan!()).toEqual(EMPTY);
    expect(get).toHaveBeenCalledWith('/api/plan', undefined);
  });
});

describe('plan writes', () => {
  const o = { requestId: 'req-12345678' };

  it('log request and log applied post the input with the row revision to the entry', async () => {
    const row = { state: 'todo', revision: 2 };
    const send = vi.fn(async () => row);
    const t = { get: vi.fn(), send } as unknown as Transport;
    const m = planMethods(t);
    expect(await m.logRequest!(31, { personId: 4, idempotencyKey: 'plan:31:request' }, 1, o)).toBe(row);
    expect(send).toHaveBeenLastCalledWith('POST', '/api/plan/31/request', { personId: 4, idempotencyKey: 'plan:31:request', revision: 1 }, o);
    await m.logApplied!(31, { postingId: 9, idempotencyKey: 'plan:31:apply' }, 2, o);
    expect(send).toHaveBeenLastCalledWith('POST', '/api/plan/31/apply', { postingId: 9, idempotencyKey: 'plan:31:apply', revision: 2 }, o);
    await m.skipRow!(31, 3, o);
    expect(send).toHaveBeenLastCalledWith('POST', '/api/plan/31/skip', { revision: 3 }, o);
  });

  it('a note save puts the edit and a confirm posts the revision', async () => {
    const note = { noteId: 7, reasonState: 'written' };
    const send = vi.fn(async () => note);
    const t = { get: vi.fn(), send } as unknown as Transport;
    const m = planMethods(t);
    expect(await m.saveNote!(7, { text: 'Hi Lena.', reasonLine: 'your post', revision: 2 }, o)).toBe(note);
    expect(send).toHaveBeenLastCalledWith('PUT', '/api/notes/7', { text: 'Hi Lena.', reasonLine: 'your post', revision: 2 }, o);
    await m.confirmReason!(7, 3, o);
    expect(send).toHaveBeenLastCalledWith('POST', '/api/notes/7/confirm', { revision: 3 }, o);
  });

  it('a refusal reaches the caller', async () => {
    const send = vi.fn(async () => { throw new Error('not_allowed'); });
    const t = { get: vi.fn(), send } as unknown as Transport;
    await expect(planMethods(t).skipRow!(31, 1, o)).rejects.toThrow('not_allowed');
  });
});
