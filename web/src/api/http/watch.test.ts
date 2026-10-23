import { watchMethods } from './watch';
import type { Transport } from './transport';
import type { WatchView } from '../types';

const EMPTY: WatchView = { asOf: '2026-11-10T15:14:00Z', items: [] };
const ITEM: WatchView = {
  asOf: '2026-11-10T15:14:00Z',
  items: [{
    id: 8, kind: 'careers_changed',
    company: { id: 3, name: 'Acme', status: 'working', pausedAt: null, heldByTouchId: null },
    text: 'The careers page changed.', at: '2026-11-08T12:00:00Z',
    link: { label: 'Careers page', url: 'https://acme.example/jobs' }, reviewId: 2,
  }],
};

describe('watchMethods', () => {
  it('reads /api/watch and hands back the view as it came', async () => {
    const get = vi.fn(async () => ITEM);
    const t = { get, send: vi.fn() } as unknown as Transport;
    expect(await watchMethods(t).watchList!()).toEqual(ITEM);
    expect(get).toHaveBeenCalledWith('/api/watch', undefined);
  });

  it('an empty store is an empty list', async () => {
    const t = { get: vi.fn(async () => EMPTY), send: vi.fn() } as unknown as Transport;
    expect((await watchMethods(t).watchList!()).items).toEqual([]);
  });

  it('a dismiss posts to the item with the write options and resolves to nothing', async () => {
    const send = vi.fn(async () => ({ id: 8 }));
    const t = { get: vi.fn(), send } as unknown as Transport;
    const o = { requestId: 'req-12345678' };
    expect(await watchMethods(t).dismissWatch!(8, o)).toBeUndefined();
    expect(send).toHaveBeenCalledWith('POST', '/api/watch/8/dismiss', {}, o);
  });

  it('a refused dismiss reaches the caller', async () => {
    const send = vi.fn(async () => { throw new Error('not_allowed'); });
    const t = { get: vi.fn(), send } as unknown as Transport;
    await expect(watchMethods(t).dismissWatch!(10, { requestId: 'req-12345678' })).rejects.toThrow('not_allowed');
  });
});
