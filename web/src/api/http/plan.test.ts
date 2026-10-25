import { planMethods } from './plan';
import type { Transport } from './transport';
import type { PlanView } from '../types';

const EMPTY: PlanView = {
  asOf: '2026-11-10T15:14:00Z', date: '2026-11-10', builtAt: null, listJob: null,
  notReady: { reason: 'not_built', text: "Today's plan is not built yet. It builds at 8:00 on weekdays." },
  rows: [], done: 0, total: 0,
  linkedinMonth: { used: 0, limit: 150, hardUsed: 0, hardLimit: 200 },
  tomorrow: { total: 7, text: 'Tomorrow: 7 people.', preview: [] },
  week: { requests: 0, accepts: 0, replies: 0 },
};

describe('planMethods', () => {
  it('reads /api/plan and hands back the view as it came', async () => {
    const get = vi.fn(async () => EMPTY);
    const t = { get, send: vi.fn() } as unknown as Transport;
    expect(await planMethods(t).plan!()).toEqual(EMPTY);
    expect(get).toHaveBeenCalledWith('/api/plan', undefined);
  });
});
