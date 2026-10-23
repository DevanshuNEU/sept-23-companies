import { outcomeMethods } from './outcomes';
import type { Transport } from './transport';
import type { OutcomePromptView } from '../types';

const EMPTY: OutcomePromptView = { asOf: '2026-11-10T15:14:00Z', touches: [] };
const ONE: OutcomePromptView = {
  asOf: '2026-11-10T15:14:00Z',
  touches: [{
    touchId: 7, channel: 'linkedin_request', sentAt: '2026-11-02T15:00:00Z', ageDays: 8, revision: 1,
    context: 'LinkedIn request from the plan, for a role at your level, naming the role.',
    person: { id: 4, name: 'Lena Ortiz', title: 'Software Engineer', roleKind: 'engineer', email: null, linkedinUrl: 'https://www.linkedin.com/in/lena', revision: 1 },
    company: { id: 3, name: 'Acme', status: 'working', pausedAt: null, heldByTouchId: null },
  }],
};
const o = { requestId: 'req-12345678' };

describe('outcomeMethods', () => {
  it('reads the prompt and hands it back as it came', async () => {
    const get = vi.fn(async () => ONE);
    const t = { get, send: vi.fn() } as unknown as Transport;
    expect(await outcomeMethods(t).outcomePrompt!()).toEqual(ONE);
    expect(get).toHaveBeenCalledWith('/api/outcomes/prompt', undefined);
  });

  it('an empty store asks about nothing', async () => {
    const t = { get: vi.fn(async () => EMPTY), send: vi.fn() } as unknown as Transport;
    expect((await outcomeMethods(t).outcomePrompt!()).touches).toEqual([]);
  });

  it('an answer posts the kind and revision to the touch and returns the new revision', async () => {
    const send = vi.fn(async () => ({ revision: 2 }));
    const t = { get: vi.fn(), send } as unknown as Transport;
    expect(await outcomeMethods(t).logOutcome!(7, 'accepted', 1, o)).toEqual({ revision: 2 });
    expect(send).toHaveBeenCalledWith('POST', '/api/touches/7/outcomes', { kind: 'accepted', revision: 1 }, o);
  });

  it('a reply carries its kind', async () => {
    const send = vi.fn(async () => ({ revision: 2 }));
    const t = { get: vi.fn(), send } as unknown as Transport;
    await outcomeMethods(t).logOutcome!(7, 'replied', 1, o, 'said_no');
    expect(send).toHaveBeenCalledWith('POST', '/api/touches/7/outcomes', { kind: 'replied', replyKind: 'said_no', revision: 1 }, o);
  });

  it('a refused answer reaches the caller', async () => {
    const send = vi.fn(async () => { throw new Error('not_allowed'); });
    const t = { get: vi.fn(), send } as unknown as Transport;
    await expect(outcomeMethods(t).logOutcome!(7, 'accepted', 1, o)).rejects.toThrow('not_allowed');
  });
});
