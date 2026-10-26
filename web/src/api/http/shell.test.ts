import { shellMethods } from './shell';
import type { Transport } from './transport';
import type { ShellSummary } from '../types';

const EMPTY: ShellSummary = {
  asOf: '2026-11-10T15:14:00Z', pickedToday: 0, capacityToday: 0, unhandledReplies: 0, draftsToReview: 0, linkedinToReach: 0,
  openReviewItems: 0, noGoCount: 0, doneCount: 0, sending: { state: 'running', sentToday: 0, limitToday: 10 },
  repliesThisMonth: 0, jobs: [], plan: { done: 0, total: 0 }, winsUnseen: 0,
};

describe('shellMethods', () => {
  it('reads /api/shell and hands back the summary as it came', async () => {
    const get = vi.fn(async () => EMPTY);
    const t = { get, send: vi.fn() } as unknown as Transport;
    expect(await shellMethods(t).shell!()).toEqual(EMPTY);
    expect(get).toHaveBeenCalledWith('/api/shell', undefined);
  });

  it('reads /api/pulse: one number', async () => {
    const get = vi.fn(async () => ({ revision: 42 }));
    const t = { get, send: vi.fn() } as unknown as Transport;
    expect(await shellMethods(t).pulse!()).toEqual({ revision: 42 });
    expect(get).toHaveBeenCalledWith('/api/pulse', undefined);
  });
});
