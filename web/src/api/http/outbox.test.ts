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

const o = { requestId: 'req-12345678' };
const sender = () => {
  const send = vi.fn(async () => ({ item: {} }));
  return { t: { get: vi.fn(), send } as unknown as Transport, send };
};

describe('outboxMethods writes', () => {
  it('save, approve, unapprove and skip post to the message with its revision', async () => {
    const { t, send } = sender();
    const m = outboxMethods(t);
    await m.saveMessage!(12, { subject: 'Hi', body: 'Body', revision: 3 }, o);
    expect(send).toHaveBeenLastCalledWith('POST', '/api/messages/12/save', { subject: 'Hi', body: 'Body', revision: 3 }, o);
    await m.approve!(12, 4, o);
    expect(send).toHaveBeenLastCalledWith('POST', '/api/messages/12/approve', { revision: 4 }, o);
    await m.unapprove!(12, 5, o);
    expect(send).toHaveBeenLastCalledWith('POST', '/api/messages/12/unapprove', { revision: 5 }, o);
    await m.skip!(12, 6, o);
    expect(send).toHaveBeenLastCalledWith('POST', '/api/messages/12/skip', { revision: 6 }, o);
  });

  it('the reply kind posts to the thread with the touch revision', async () => {
    const { t, send } = sender();
    await outboxMethods(t).setReplyKind!(41, 'auto_reply', 2, o);
    expect(send).toHaveBeenCalledWith('POST', '/api/touches/41/reply-kind', { kind: 'auto_reply', revision: 2 }, o);
  });

  it('a blocked approve reaches the caller', async () => {
    const send = vi.fn(async () => { throw new Error('blocked'); });
    const t = { get: vi.fn(), send } as unknown as Transport;
    await expect(outboxMethods(t).approve!(12, 1, o)).rejects.toThrow('blocked');
  });
});

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
