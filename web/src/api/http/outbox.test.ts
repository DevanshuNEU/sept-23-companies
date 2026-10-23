import { outboxMethods } from './outbox';
import type { Transport } from './transport';
import type { OutboxView } from '../types';

const EMPTY: OutboxView = {
  asOf: '2026-11-10T15:14:00Z',
  sending: {
    state: 'running', pausedReason: '', pausedAt: null, gmailMessage: null, sentToday: 0, limitToday: 20, rampLevel: 1,
    rampLevels: [20, 30, 40, 50, 60, 70, 80, 90, 100], nextLimitIfClean: 30, lastStepDown: null,
    signals: { bounceRate3d: 0, gmailErrorsToday: 0, stopRepliesToday: 0 }, window: { start: '08:30', end: '16:30' },
    nextSend: null, noneReason: 'Nothing is approved and waiting to send.', revision: 1,
  },
  items: [], counts: { replied: 0, review: 0, approved: 0, sending: 0, checking: 0, sent: 0, paused: 0, closed: 0 },
  closedBreakdown: { bounced: 0, skipped: 0, companyClosed: 0 },
};

const reader = (answer: unknown) => {
  const get = vi.fn(async () => answer);
  return { t: { get, send: vi.fn() } as unknown as Transport, get };
};

describe('outboxMethods', () => {
  it('reads /api/outbox and hands back the view as it came', async () => {
    const { t, get } = reader(EMPTY);
    expect(await outboxMethods(t).outbox!()).toEqual(EMPTY);
    expect(get).toHaveBeenCalledWith('/api/outbox', undefined);
  });

  it('reads one thread by its touch id', async () => {
    const { t, get } = reader({});
    await outboxMethods(t).outboxDetail!(41);
    expect(get).toHaveBeenCalledWith('/api/outbox/41', undefined);
  });

  it('compares two drafts by their message ids', async () => {
    const { t, get } = reader({});
    await outboxMethods(t).compareDrafts!(7, 9);
    expect(get).toHaveBeenCalledWith('/api/outbox/compare?messageId=7&otherMessageId=9', undefined);
  });
});
