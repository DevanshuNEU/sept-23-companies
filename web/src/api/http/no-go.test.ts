import { noGoMethods } from './no-go';
import type { Transport } from './transport';
import type { NoGoView } from '../types';

const EMPTY: NoGoView = { asOf: '2026-11-10T15:14:00Z', companies: [], people: [], countsByReason: [], hiddenCompanies: [] };
const o = { requestId: 'req-12345678' };

describe('noGoMethods', () => {
  it('reads /api/no-go and hands back the view as it came', async () => {
    const get = vi.fn(async () => EMPTY);
    const t = { get, send: vi.fn() } as unknown as Transport;
    expect(await noGoMethods(t).noGo!()).toEqual(EMPTY);
    expect(get).toHaveBeenCalledWith('/api/no-go', undefined);
  });

  it('a lift posts the reason to the entry and resolves to nothing', async () => {
    const send = vi.fn(async () => ({}));
    const t = { get: vi.fn(), send } as unknown as Transport;
    expect(await noGoMethods(t).liftNoGo!(7, 'They now sponsor', o)).toBeUndefined();
    expect(send).toHaveBeenCalledWith('POST', '/api/no-go/7/lift', { reason: 'They now sponsor' }, o);
  });

  it('a refused lift reaches the caller', async () => {
    const send = vi.fn(async () => { throw new Error('not_allowed'); });
    const t = { get: vi.fn(), send } as unknown as Transport;
    await expect(noGoMethods(t).liftNoGo!(7, 'Again', o)).rejects.toThrow('not_allowed');
  });
});
