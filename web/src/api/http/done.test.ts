import { doneMethods } from './done';
import type { Transport } from './transport';
import type { DoneView } from '../types';

const EMPTY: DoneView = {
  asOf: '2026-11-10T15:14:00Z', companies: [],
  metrics: { repliesThisMonth: 0, repliesPerWeek: [], replyRate: { replies: 0, firstEmails: 0 }, referrals: 0, interviews: 0 },
};

describe('doneMethods', () => {
  it('reads /api/done and hands back the view as it came', async () => {
    const get = vi.fn(async () => EMPTY);
    const t = { get, send: vi.fn() } as unknown as Transport;
    expect(await doneMethods(t).done!()).toEqual(EMPTY);
    expect(get).toHaveBeenCalledWith('/api/done', undefined);
  });
});
