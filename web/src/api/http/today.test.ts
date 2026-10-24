import { todayMethods } from './today';
import type { Transport } from './transport';
import type { TodayView } from '../types';

const EMPTY: TodayView = {
  asOf: '2026-11-10T15:14:00Z', date: '2026-11-10', builtAt: null, capacity: 7, picked: 0,
  notReady: { reason: 'not_built', text: 'No ranking has run yet.' }, proposed: [], backlog: [], stillEmailing: [], listJob: null,
};
const o = { requestId: 'req-12345678' };

describe('todayMethods', () => {
  it('reads /api/today and hands back the view as it came', async () => {
    const get = vi.fn(async () => EMPTY);
    const t = { get, send: vi.fn() } as unknown as Transport;
    expect(await todayMethods(t).today!()).toEqual(EMPTY);
    expect(get).toHaveBeenCalledWith('/api/today', undefined);
  });

  it('pick, unpick and backlog post the revision to the company and answer with the row', async () => {
    const row = { state: 'picked', revision: 3 };
    const send = vi.fn(async () => row);
    const t = { get: vi.fn(), send } as unknown as Transport;
    const m = todayMethods(t);
    expect(await m.pick!(12, 2, o)).toBe(row);
    expect(send).toHaveBeenLastCalledWith('POST', '/api/companies/12/pick', { revision: 2 }, o);
    await m.unpick!(12, 3, o);
    expect(send).toHaveBeenLastCalledWith('POST', '/api/companies/12/unpick', { revision: 3 }, o);
    await m.backlog!(12, 4, o);
    expect(send).toHaveBeenLastCalledWith('POST', '/api/companies/12/backlog', { revision: 4 }, o);
  });

  it('remove and restore post their reason and resolve to nothing', async () => {
    const send = vi.fn(async () => ({}));
    const t = { get: vi.fn(), send } as unknown as Transport;
    const m = todayMethods(t);
    expect(await m.removeCompany!(12, 'not_a_fit', 'Too big', 2, o)).toBeUndefined();
    expect(send).toHaveBeenLastCalledWith('POST', '/api/companies/12/remove', { reason: 'not_a_fit', note: 'Too big', revision: 2 }, o);
    expect(await m.restoreCompany!(12, 'A friend works there', 3, o)).toBeUndefined();
    expect(send).toHaveBeenLastCalledWith('POST', '/api/companies/12/restore', { reason: 'A friend works there', revision: 3 }, o);
  });

  it('a capacity refusal reaches the caller', async () => {
    const send = vi.fn(async () => { throw new Error('capacity'); });
    const t = { get: vi.fn(), send } as unknown as Transport;
    await expect(todayMethods(t).pick!(12, 2, o)).rejects.toThrow('capacity');
  });
});
