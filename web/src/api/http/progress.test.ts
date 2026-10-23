import { progressMethods } from './progress';
import type { Transport } from './transport';
import type { FindingsView, ProgressView, WinsView } from '../types';

const PERIOD = {
  label: 'This week', from: '2026-11-09', to: '2026-11-15', enough: false, quiet: false,
  stages: [
    { stage: 'requests' as const, label: 'Requests', count: 9, from: null },
    { stage: 'accepts' as const, label: 'Accepts', count: 3, from: { stage: 'requests' as const, count: 9 } },
  ],
  effort: [{ label: 'Requests sent', count: 9 }],
};
const PROGRESS: ProgressView = {
  asOf: '2026-11-10T15:14:00Z',
  today: { planned: 7, done: 2, requests: 2, applications: 0, accepted: 1, replied: 0 },
  week: PERIOD, month: { ...PERIOD, label: 'This month', from: '2026-11-01', to: '2026-11-30' },
  base: { since: '2026-11-03', requests: 9, accepts: 3, replies: 0, medianDaysToReply: null, enough: false, minRequests: 30 },
};
const WINS: WinsView = {
  asOf: '2026-11-10T15:14:00Z', wins: [], unseen: 0, since: '2026-11-10',
  counts: { accepted: 0, replied: 0, referred: 0, interview: 0, offer: 0 },
};
const FINDINGS: FindingsView = { asOf: '2026-11-10T15:14:00Z', month: '2026-11-01', reportedOn: null, findings: [], changes: [] };
const o = { requestId: 'req-12345678' };

describe('progressMethods', () => {
  it('reads each view from its path and hands it back as it came', async () => {
    const get = vi.fn(async (path: string) => (path === '/api/progress' ? PROGRESS : path === '/api/wins' ? WINS : FINDINGS));
    const t = { get, send: vi.fn() } as unknown as Transport;
    const m = progressMethods(t);
    expect(await m.progress!()).toEqual(PROGRESS);
    expect(await m.wins!()).toEqual(WINS);
    expect(await m.findings!()).toEqual(FINDINGS);
    expect(get.mock.calls.map((c) => c[0])).toEqual(['/api/progress', '/api/wins', '/api/findings']);
  });

  it('marking wins seen posts the ids and resolves to nothing', async () => {
    const send = vi.fn(async () => ({ marked: 2 }));
    const t = { get: vi.fn(), send } as unknown as Transport;
    expect(await progressMethods(t).markWinsSeen!([8, 17], o)).toBeUndefined();
    expect(send).toHaveBeenCalledWith('POST', '/api/wins/seen', { winIds: [8, 17] }, o);
  });

  it('an unknown win id reaches the caller as the refusal it was', async () => {
    const send = vi.fn(async () => { throw new Error('not_found'); });
    const t = { get: vi.fn(), send } as unknown as Transport;
    await expect(progressMethods(t).markWinsSeen!([3], o)).rejects.toThrow('not_found');
  });
});
