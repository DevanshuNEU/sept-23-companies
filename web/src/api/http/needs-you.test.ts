import { needsYouMethods } from './needs-you';
import type { Transport } from './transport';
import type { NeedsYouView } from '../types';

const EMPTY: NeedsYouView = { asOf: '2026-11-10T15:14:00Z', open: [], resolvedThisWeek: [], boardsJob: null, settledToday: 0 };
const o = { requestId: 'req-12345678' };

describe('needsYouMethods', () => {
  it('reads /api/needs-you and hands back the view as it came', async () => {
    const get = vi.fn(async () => EMPTY);
    const t = { get, send: vi.fn() } as unknown as Transport;
    expect(await needsYouMethods(t).needsYou!()).toEqual(EMPTY);
    expect(get).toHaveBeenCalledWith('/api/needs-you', undefined);
  });

  it('a resolve posts the fix and the revision to the item and resolves to nothing', async () => {
    const send = vi.fn(async () => ({ id: 7, revision: 2 }));
    const t = { get: vi.fn(), send } as unknown as Transport;
    const m = needsYouMethods(t);
    expect(await m.resolveReview!(7, { kind: 'merge', keep: 'b' }, 1, o)).toBeUndefined();
    expect(send).toHaveBeenLastCalledWith('POST', '/api/review/7/resolve', { fix: { kind: 'merge', keep: 'b' }, revision: 1 }, o);
    await m.resolveReview!(7, { kind: 'edit', field: 'size', value: '45', source: 'LinkedIn' }, 3, o);
    expect(send).toHaveBeenLastCalledWith('POST', '/api/review/7/resolve', { fix: { kind: 'edit', field: 'size', value: '45', source: 'LinkedIn' }, revision: 3 }, o);
  });

  it('an unmerge posts the revision to the merged company and answers with its new revision', async () => {
    const send = vi.fn(async () => ({ revision: 3 }));
    const t = { get: vi.fn(), send } as unknown as Transport;
    expect(await needsYouMethods(t).unmergeCompany!(12, 2, o)).toEqual({ revision: 3 });
    expect(send).toHaveBeenCalledWith('POST', '/api/companies/12/unmerge', { revision: 2 }, o);
  });

  it('a refused resolve reaches the caller', async () => {
    const send = vi.fn(async () => { throw new Error('conflict'); });
    const t = { get: vi.fn(), send } as unknown as Transport;
    await expect(needsYouMethods(t).resolveReview!(7, { kind: 'ok' }, 1, o)).rejects.toThrow('conflict');
  });
});
