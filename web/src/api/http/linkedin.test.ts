import { linkedinMethods } from './linkedin';
import type { Transport } from './transport';
import type { LinkedInPerson, LinkedInView } from '../types';

const PERSON: LinkedInPerson = {
  person: { id: 4, name: 'Lena Ortiz', title: 'Software Engineer', roleKind: 'engineer', email: null, linkedinUrl: 'https://www.linkedin.com/in/lena', revision: 1 },
  reason: 'no_email', steps: [{ step: 'request_sent', at: '2026-11-10T15:14:00Z' }], replyKind: null, tab: 'waiting',
};
const VIEW: LinkedInView = { asOf: '2026-11-10T15:14:00Z', groups: [], counts: { toReach: 0, waiting: 0, replied: 0, all: 0 }, requestsThisWeek: 0 };
const o = { requestId: 'req-12345678' };

describe('linkedinMethods', () => {
  it('reads the list from its path and hands it back as it came', async () => {
    const get = vi.fn<(path: string) => Promise<LinkedInView>>(async () => VIEW);
    const t = { get, send: vi.fn() } as unknown as Transport;
    expect(await linkedinMethods(t).linkedin!()).toEqual(VIEW);
    expect(get.mock.calls.map((c) => c[0])).toEqual(['/api/linkedin']);
  });

  it('logs a step to the person path with the request id, and sends no reply kind unless there is one', async () => {
    const send = vi.fn(async () => PERSON);
    const t = { get: vi.fn(), send } as unknown as Transport;
    const m = linkedinMethods(t);
    expect(await m.logLinkedIn!(4, 'request_sent', o)).toEqual(PERSON);
    expect(send).toHaveBeenLastCalledWith('POST', '/api/linkedin/4/log', { step: 'request_sent' }, o);
    await m.logLinkedIn!(4, 'replied', o, 'said_no');
    expect(send).toHaveBeenLastCalledWith('POST', '/api/linkedin/4/log', { step: 'replied', replyKind: 'said_no' }, o);
  });

  it('takes the newest step back through the undo path', async () => {
    const send = vi.fn(async () => ({ ...PERSON, steps: [], tab: 'to_reach' as const }));
    const t = { get: vi.fn(), send } as unknown as Transport;
    expect((await linkedinMethods(t).undoLinkedIn!(4, 'request_sent', o)).tab).toBe('to_reach');
    expect(send).toHaveBeenCalledWith('POST', '/api/linkedin/4/undo', { step: 'request_sent' }, o);
  });

  it('a refusal reaches the caller as the refusal it was', async () => {
    const send = vi.fn(async () => { throw new Error('not_allowed'); });
    const t = { get: vi.fn(), send } as unknown as Transport;
    await expect(linkedinMethods(t).logLinkedIn!(4, 'request_sent', o)).rejects.toThrow('not_allowed');
  });
});
