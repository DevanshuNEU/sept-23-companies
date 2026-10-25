import { healthMethods } from './health';
import type { Transport } from './transport';
import type { HealthView } from '../types';

const EMPTY: HealthView = {
  asOf: '2026-11-10T15:14:00Z',
  store: { version: 2, rulesetVersion: null },
  jobs: [],
  readQueue: { queued: 0, leased: 0, leaseExpired: 0, done: 0, refused: 0, oldestQueuedAgeHours: null, lastNight: null },
  boards: { active: 0, dueNow: 0, erroring: 0 },
  watchedPages: { total: 0, changedLast7Days: 0 },
  sender: { state: 'running', sentToday: 0, limitToday: 20 },
};

describe('healthMethods', () => {
  it('reads /api/health and hands back the view as it came', async () => {
    const get = vi.fn(async () => EMPTY);
    const t = { get, send: vi.fn() } as unknown as Transport;
    const view = await healthMethods(t).health!();
    expect(get).toHaveBeenCalledWith('/api/health', undefined);
    expect(view).toEqual(EMPTY);
  });

  it('an empty store is a valid view: no jobs, an idle queue and no age', async () => {
    const t = { get: vi.fn(async () => EMPTY), send: vi.fn() } as unknown as Transport;
    const view = await healthMethods(t).health!();
    expect(view.jobs).toEqual([]);
    expect(view.readQueue.oldestQueuedAgeHours).toBeNull();
    expect(view.readQueue.lastNight).toBeNull();
  });
});
